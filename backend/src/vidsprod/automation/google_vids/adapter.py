"""The Google Vids browser adapter.

Three gates stand in front of every action:

1. **Mode and authorisation.** Read-only operations (launch, sign-in detection,
   diagnostics) need GOOGLE_VIDS_MODE=browser_test or live. Anything that creates, submits,
   edits or exports needs GOOGLE_VIDS_MODE=live *and* GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED=true.
2. **Capability.** The operation must be VERIFIED in the capability registry.
3. **Locators.** Every element comes from the recon locator catalogue; see `selectors.py`.

Every step follows EXECUTE → WAIT → INSPECT → VERIFY → UPDATE STATE: the page driver
records action, expected and observed result for each primitive, and a step that does not
observe its expected state raises instead of continuing.

The recipes follow the flows Google documents ("Create AI videos" → "Create" → prompt →
"Generate" → "Insert"; File → "Download as MP4"). Until recon has recorded their locators,
they refuse to run, and every live capability reports UNVERIFIED.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from vidsprod.config import Settings
from vidsprod.domain.enums import ArtifactKind, IntentKind, TimelineOpKind
from vidsprod.errors import (
    CapabilityUnavailableError,
    GenerationRefusedError,
    GenerationTimeoutError,
    LiveAutomationDisabledError,
    ProtectedProjectError,
    QuotaExhaustedError,
    SessionExpiredError,
    UIMismatchError,
    UnsavedClipError,
    VidsError,
)
from vidsprod.media import ffmpeg
from vidsprod.vids.capabilities import CapabilityRegistry
from vidsprod.vids.port import (
    ClipCandidate,
    ClipEvidence,
    ClipRequest,
    EvidenceFile,
    ExportResult,
    ExportSpec,
    ProjectHandle,
    ReconcileResult,
    ReconcileStatus,
    SessionState,
    SessionStateName,
    TimelineItem,
    TimelineOpSpec,
    TimelineState,
)

from . import authentication, diagnostics
from .browser_manager import BrowserSessionManager
from .selectors import LocatorCatalogue, PageDriver

log = logging.getLogger(__name__)

_SIGNIN_HOSTS = ("accounts.google.com",)


DEFAULT_TITLE_TEXT = "Title"  # observed content of a new 'Add a title' box
# kind → (the panel entry that adds it, its observed default words on the canvas)
TEXT_BOXES = {"title": ("text.add_title", ("Title",)), "body": ("text.add_body", ("Body", "text"))}


def _find_run(tokens: list[str], run: tuple[str, ...]) -> int | None:
    for i in range(len(tokens) - len(run) + 1):
        if tuple(tokens[i:i + len(run)]) == run:
            return i
    return None


def _count_run(tokens: list[str], run: tuple[str, ...]) -> int:
    return sum(1 for i in range(len(tokens) - len(run) + 1) if tuple(tokens[i:i + len(run)]) == run)


def _union(boxes: list[dict[str, float] | None]) -> dict[str, float] | None:
    if not boxes or any(b is None for b in boxes):
        return None
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["width"] for b in boxes)
    y1 = max(b["y"] + b["height"] for b in boxes)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def _overlap(a: dict[str, float], b: dict[str, float]) -> float:
    """Share of box `b` covered by box `a` (Playwright bounding boxes)."""
    w = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
    area = b["width"] * b["height"]
    return (w * h / area) if w > 0 and h > 0 and area else 0.0


REQUIRED_MODEL = ("Omni", "720p")  # the generation settings the user specified (2026-09-30)
CANVAS = (1920, 1080)  # Format options pixel space (observed)
RIGHT_COLUMN_VIDEO = {"w": 1016, "h": 571.5, "x": 872, "y": 254.2}  # 16:9, beside a left text column
LOGO_WIDTH, LOGO_MARGIN = 260.0, 40.0  # the uploaded logo, top right
BLANK_SCENE_SECONDS = 5.0  # a new blank scene's length (observed)
GEOMETRY_FIELDS = {"w": "format.width", "h": "format.height", "x": "format.x", "y": "format.y"}


class ExportCrashedError(VidsError):
    """Chrome closed while the export download was in progress (retryable: no Vid change)."""


def parse_timecode(text: str) -> float:
    """'00:06.0' → 6.0, '1:02:03.5' → 3723.5."""
    seconds = 0.0
    for part in text.strip().split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def canvas_has_text(rendered: list[str], text: str) -> bool:
    """The typed words appear as exact, whole, consecutive canvas tokens (case-insensitive;
    Vids capitalises the first letter). 'Titlevidsprod' does not count as 'vidsprod'."""
    tokens = [w.lower() for chunk in rendered for w in chunk.split()]
    want = text.lower().split()
    return any(tokens[i:i + len(want)] == want for i in range(len(tokens) - len(want) + 1))


class BrowserVidsAdapter:
    name = "browser"
    simulated = False

    def __init__(self, settings: Settings, registry: CapabilityRegistry,
                 catalogue: LocatorCatalogue | None = None,
                 manager: BrowserSessionManager | None = None) -> None:
        self.settings = settings
        self.capabilities = registry
        self.catalogue = catalogue or LocatorCatalogue.load(settings.locator_catalogue_path)
        self.manager = manager or BrowserSessionManager(settings)
        self.account_key = (settings.google_vids_allowed_account or "unconfigured").lower()
        self._fingerprint_ok = False
        self.last_session: SessionState | None = None
        # Set while a generated result is in the page but not proven inserted. Observed: the
        # result exists only in that page, so a reload, navigation or closing the browser loses
        # the clip (the quota is already spent). A person resolves it; the adapter will not.
        self.unsaved_clip: dict[str, Any] | None = None
        self.lost_clips: list[dict[str, Any]] = []
        self.insert_timeout_s = 30.0
        self.export_attempts = 3

    # ------------------------------------------------------------------ gates
    def _guard_unsaved_clip(self, action: str) -> None:
        if self.unsaved_clip is not None and not self.manager.running:
            # The page that held it is gone (Chrome closed or crashed): nothing is left to save,
            # and keeping the flag would block every later production.
            log.error("a generated clip was lost before it could be confirmed inserted",
                      extra={"clip": self.unsaved_clip})
            self.lost_clips.append(self.unsaved_clip)
            self.unsaved_clip = None
        if self.unsaved_clip is not None:
            raise UnsavedClipError(
                f"refusing to {action}: a generated clip is open in the browser but not inserted; "
                "insert it by hand (or confirm it is in the timeline), then call "
                "release_unsaved_clip()", details={"clip": self.unsaved_clip})

    def release_unsaved_clip(self) -> None:
        """A person confirmed the pending clip was inserted (or accepts losing it)."""
        self.unsaved_clip = None

    def _guard_live(self) -> None:
        if not self.settings.live_actions_allowed:
            raise LiveAutomationDisabledError(
                f"Live Google Vids actions are disabled (GOOGLE_VIDS_MODE="
                f"{self.settings.google_vids_mode.value}, GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED="
                f"{str(self.settings.google_vids_live_automation_enabled).lower()}). They stay "
                "disabled until the account owner confirms automation is authorised."
            )

    def _require(self, capability: str) -> None:
        self.capabilities.require(capability)
        missing = self.catalogue.missing_for(capability)
        if missing:
            raise CapabilityUnavailableError(
                f"'{capability}' has no verified locators for: {', '.join(missing)}",
                details={"capability": capability, "missing_locators": missing})
        if not self._fingerprint_ok:
            raise CapabilityUnavailableError(
                "the session has not confirmed the verified UI fingerprint; run a session check")

    async def _resume(self, project: ProjectHandle | None) -> None:
        """A production can resume after the backend restarted: the new adapter has not seen
        the session check the production passed earlier. Check it once, then reopen the
        production's Vid so the next step acts on the right page."""
        if self._fingerprint_ok:
            return
        state = await self.check_session(self._evidence("session"))
        if state.state is not SessionStateName.READY:
            raise SessionExpiredError(f"cannot resume the production: {state.message}")
        if project is not None and project.url:
            await self.open_project(project)

    async def _driver(self, evidence_dir: Path) -> PageDriver:
        page = await self.manager.page()
        host = urlparse(page.url or "").hostname or ""
        if host in _SIGNIN_HOSTS:
            raise SessionExpiredError("the Google session expired; sign in again")
        return PageDriver(page, self.catalogue, evidence_dir,
                          timeout_ms=self.settings.browser_action_timeout_ms,
                          min_interval_ms=self.settings.browser_min_action_interval_ms)

    def _evidence(self, *parts: str) -> Path:
        path = self.settings.artifact_dir / "browser"
        for part in parts:
            path = path / part
        return path

    # ================================================================== unified steps
    async def launch(self) -> dict[str, Any]:
        async with self.manager.lock:
            await self.manager.launch()
        return self.manager.status()

    async def open_home(self) -> SessionState:
        self._guard_unsaved_clip("open the Vids home page")
        async with self.manager.lock:
            page = await self.manager.open_home()
            return await authentication.detect(page, self.settings)

    async def authenticate(self, timeout_seconds: float = 300) -> SessionState:
        """Show the sign-in page and wait for the person to sign in. Never types credentials."""
        await authentication.open_login(self.manager)
        state = await authentication.wait_for_login(self.manager, timeout_seconds=timeout_seconds)
        self.last_session = state
        return state

    async def diagnose(self, label: str = "diagnostics") -> dict[str, Any]:
        async with self.manager.lock:
            page = await self.manager.page()
            if not page.url.startswith("http"):  # a fresh browser: diagnose Vids, not a blank tab
                page = await self.manager.open_home()
            return await diagnostics.diagnose(page, self._evidence("diagnostics"), label)

    async def check_session(self, evidence_dir: Path) -> SessionState:
        if not self.settings.browser_enabled:
            return SessionState(state=SessionStateName.DISABLED,
                                message="GOOGLE_VIDS_MODE is mock; the browser is not used.")
        self._guard_unsaved_clip("run a session check (it opens the home page)")
        async with self.manager.lock:
            page = await self.manager.open_home()
            state = await authentication.detect(page, self.settings, evidence_dir)
            if state.state is SessionStateName.SIGNED_IN and self.catalogue.ui_fingerprint:
                driver = PageDriver(page, self.catalogue, evidence_dir,
                                    timeout_ms=self.settings.browser_action_timeout_ms,
                                    min_interval_ms=0)
                fingerprint = await driver.fingerprint(expected=self.catalogue.ui_fingerprint)
                state.ui_fingerprint = fingerprint
                if fingerprint == self.catalogue.ui_fingerprint:
                    self._fingerprint_ok = True
                    state.fingerprint_matches = True
                    state.state = SessionStateName.READY
                    state.message = f"{state.message}; editor UI matches the verified fingerprint"
                else:
                    self._fingerprint_ok = False
                    state.fingerprint_matches = False
                    demoted = self.capabilities.invalidate_for_fingerprint(fingerprint)
                    state.state = SessionStateName.UI_CHANGED
                    state.message = (f"The editor differs from the verified UI; {len(demoted)} "
                                     "capabilities were demoted until recon re-verifies them.")
        self.last_session = state
        return state

    async def create_project(self, title: str) -> ProjectHandle:
        """Observed flow (2026-09-29): Vids home → 'Blank video' tile → editor at
        /videos/d/<id>/edit → optional 'Getting started' dialog → title field."""
        self._guard_unsaved_clip("create a project")
        self._guard_live()
        self._require("project.create")
        prefix = self.settings.google_vids_project_prefix
        if not title.startswith(prefix):
            raise ProtectedProjectError(f"project titles must start with {prefix!r}")
        async with self.manager.lock:
            driver = await self._driver(self._evidence("project"))
            if not authentication.is_vids_page(driver.page.url, self.settings):
                await driver.page.goto(self.settings.google_vids_url, wait_until="load")
            await driver.click("project.new", irreversible=True)
            await driver.page.wait_for_url("**/videos/d/**", timeout=self.settings.browser_action_timeout_ms * 3)
            await self._dismiss_getting_started(driver)
            await driver.expect_one("editor.ready", self.settings.browser_action_timeout_ms * 2)
            await self._set_title(driver, title)
            await driver.screenshot("project_created")
            url = driver.page.url.split("?")[0]
            return ProjectHandle(project_id=self._project_id(url), title=title, url=url)

    async def _dismiss_getting_started(self, driver: PageDriver) -> None:
        """A new Vid opens under a 'Getting started' dialog that hides the editor controls
        (observed). Wait for whichever appears first; close the dialog if it is there."""
        deadline = asyncio.get_running_loop().time() + self.settings.browser_action_timeout_ms * 2 / 1000
        while asyncio.get_running_loop().time() < deadline:
            if await driver.click_if_present("editor.getting_started_close"):
                await driver.page.wait_for_timeout(1000)
                return
            if await driver.count("editor.ready") == 1:
                return
            await asyncio.sleep(0.5)

    @staticmethod
    def _project_id(url: str) -> str:
        parts = urlparse(url).path.strip("/").split("/")
        return parts[parts.index("d") + 1] if "d" in parts else url

    async def _set_title(self, driver: PageDriver, title: str) -> None:
        await driver.click("project.title")
        await driver.fill_verified("project.title", title)
        await driver.press_key("Enter", allow_enter=True)  # commits the title field only
        await driver.page.wait_for_timeout(1500)
        stored = await driver.locator("project.title").input_value()
        if stored != title:
            raise await driver.mismatch(f"title reads {stored!r} after renaming", "project.title")

    async def open_project(self, handle: ProjectHandle) -> ProjectHandle:
        self._guard_unsaved_clip("open a project")
        self._guard_live()
        self._require("project.open")
        prefix = self.settings.google_vids_project_prefix
        if not handle.url or not authentication.is_vids_page(handle.url, self.settings):
            raise ProtectedProjectError("refusing to open a URL outside Google Vids")
        async with self.manager.lock:
            driver = await self._driver(self._evidence("project"))
            await driver.page.goto(handle.url, wait_until="load")
            await self._dismiss_getting_started(driver)
            await driver.expect_one("editor.ready", self.settings.browser_action_timeout_ms * 2)
            # The title loads after the editor: wait for it rather than judge a placeholder.
            title = ""
            for _ in range(20):
                title = await driver.locator("project.title").input_value()
                if title.startswith(prefix):
                    break
                await asyncio.sleep(0.5)
            if not title.startswith(prefix):
                raise ProtectedProjectError("the opened Vid was not created by this system")
        return handle

    async def configure_project(self, project: ProjectHandle, aspect_ratio: str) -> None:
        """Aspect ratio is set per generated clip in the AI video settings (observed); there is
        no separate project-level control in the recipes."""
        raise CapabilityUnavailableError("aspect ratio is configured per clip (clip.configure)")

    async def enter_prompt(self, driver: PageDriver, request: ClipRequest) -> str:
        """Observed flow: open the AI video panel, Create tab, expand the composer, type the
        prompt into the rich-text field (never Enter), then Generation settings: duration
        slider (3–10 s, whole seconds) and aspect radio; the settings button's label is the
        read-back. Returns that label."""
        if await driver.count("generate.panel") == 0:
            button = await driver.expect_one("generate.open_panel")
            if await button.get_attribute("aria-disabled") == "true":  # observed: a Vid with no scene
                raise await driver.mismatch("AI video is disabled: the Vid has no scene",
                                            "generate.open_panel")
            await driver.click("generate.open_panel")
            await driver.expect_one("generate.panel")
        await driver.click("generate.create")
        await driver.click_if_present("generate.expand")
        attached = 0
        if request.ingredient_paths:
            # Observed 2026-09-30: 'Ingredients' opens a file chooser; an attached image shows a
            # 'Remove image' control (and the button becomes 'Add avatar or ingredients').
            self._require("clip.ingredients")
            for path in request.ingredient_paths:
                if not Path(path).is_file():
                    raise ValueError(f"ingredient {path} does not exist")
                button = "generate.ingredients" if attached == 0 else "generate.ingredients_more"
                # Observed: the pointer left over 'Expand' hovers 'Ingredients' once the composer
                # opens, and its tooltip then covers the button. Move away and let it fade.
                await driver.page.mouse.move(5, 5)
                await driver.page.wait_for_timeout(800)
                async with driver.page.expect_file_chooser(timeout=self.settings.browser_action_timeout_ms) as chooser:
                    await driver.click(button)
                await (await chooser.value).set_files(str(path))
                attached += 1
                await self._wait_ingredients(driver, attached)
        if attached:
            # Observed: each attached image puts a non-editable 'Image1' chip at the start of
            # the prompt (and relabels the field). The text is typed after the chips, which must
            # stay; '@' is not typed (it opens the mention picker).
            if "@" in request.prompt:
                raise ValueError("with ingredients the prompt is typed after the image chips; do not type '@'")
            await driver.append_verified("generate.prompt", request.prompt)
            await self._wait_ingredients(driver, attached)
        else:
            await driver.type_verified("generate.prompt", request.prompt)
        await driver.click("generate.settings")
        seconds = int(round(request.duration_seconds))
        await driver.fill_verified("generate.duration", str(seconds))
        aspect = {"16:9": "Landscape", "9:16": "Portrait"}[request.aspect_ratio.value]
        await driver.click(f"generate.aspect.{request.aspect_ratio.value}")
        await driver.press_key("Escape")
        label = (await driver.locator("generate.settings").get_attribute("aria-label")) or ""
        # Observed labels: 'Omni, 720p, Landscape, 6 seconds' (09-29), '… 10 sec' (09-30).
        if not re.search(rf"\b{aspect}, {seconds} sec(ond)?s?\b", label) or \
                not all(part in label for part in REQUIRED_MODEL):
            raise await driver.mismatch(f"generation settings read {label!r}", "generate.settings")
        return label

    async def _wait_ingredients(self, driver: PageDriver, n: int) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30
        while await driver.count("generate.ingredient_remove") != n:
            if loop.time() > deadline:
                raise await driver.mismatch(f"{n} ingredient image(s) should be attached", "generate.ingredient_remove")
            await asyncio.sleep(0.5)

    async def configure_scene(self, project: ProjectHandle, request: ClipRequest) -> str:
        """Everything before Generate, verified by read-back. Spends no quota."""
        self._guard_live()
        self._require("clip.configure")
        async with self.manager.lock:
            driver = await self._driver(self._evidence(request.scene_key))
            label = await self.enter_prompt(driver, request)
            await driver.screenshot(f"{request.scene_key}_configured")
            return label

    async def upload_reference(self, project: ProjectHandle, path: Path, label: str) -> dict[str, Any]:
        self._guard_live()
        self._require("clip.ingredients")
        async with self.manager.lock:
            driver = await self._driver(self._evidence("ingredients"))
            await driver.click("generate.ingredients")
            await driver.upload_verified("generate.ingredients.upload", path)
            shot = await driver.screenshot(f"ingredient_{label}")
        return {"label": label, "file": path.name, "screenshot": str(shot.path)}

    async def generate_scene(self, project: ProjectHandle, request: ClipRequest) -> ClipCandidate:
        """Observed 2026-09-29 (one real generation): after Generate the panel shows
        'Bringing your vision to life! …' with a percentage, then 'Your generated video is
        ready for preview' and a <video aria-label="Generated video: <prompt>, 720p, 6 seconds">
        with an 'Insert' button. The result lives ONLY in the open page: a reload loses it. So
        nothing touches the page between the result appearing and Insert. Insert fills the
        ACTIVE scene (observed 2026-09-30), so a blank scene at the end is prepared and made
        active before anything is typed; the insertion is proved by the timeline read-back
        (same scene count, that scene now the clip's length, a visible canvas video of the
        clip's duration); only then is the clip inspected, best-effort and bounded. Generation is refused unless insertion is
        verified too, never spending quota on a clip that cannot be kept."""
        self._guard_unsaved_clip("generate another clip")
        self._guard_live()
        self._require("clip.generate")
        self._require("clip.insert")
        self._require("timeline.add_scene")
        params = self.capabilities.get("clip.generate").params
        if not params.get("min_seconds", 0) <= request.duration_seconds <= params.get("max_seconds", 0):
            raise VidsError(f"{request.duration_seconds}s is outside the verified clip range")
        async with self.manager.lock:
            driver = await self._driver(self._evidence(request.scene_key))
            stem = f"{request.scene_key}_t{request.take_no}"
            before = await driver.count("generate.result")
            if before:
                # Earlier results stay in the AI panel after they are inserted (observed with a
                # second scene). With several, which 'Insert' is ours would be a guess, so the Vid
                # is reopened: inserted clips are saved in it, and the panel starts empty.
                await driver.page.goto(project.url, wait_until="load")
                await self._dismiss_getting_started(driver)
                await driver.expect_one("editor.ready", self.settings.browser_action_timeout_ms * 2)
                before = await driver.count("generate.result")
                if before:
                    raise await driver.mismatch(
                        f"the AI panel still holds {before} result(s) after reopening the Vid", "generate.result")
            # Observed: Insert fills the ACTIVE scene. Make it a blank scene at the end first.
            timeline_before = await self._prepare_blank_scene(driver, stem, project_id=project.project_id)
            await self.enter_prompt(driver, request)
            await driver.screenshot(f"{stem}_before_generate")
            submit = await driver.expect_one("generate.submit")
            if await submit.is_disabled():  # observed with an empty prompt; nothing was submitted
                raise await driver.mismatch("Generate is disabled; nothing was submitted", "generate.submit")
            await driver.click("generate.submit", irreversible=True)
            await self._wait_for_generation(driver, before)
            card = driver.locator("generate.result").nth(before)
            label = (await card.get_attribute("aria-label")) or ""
            self.unsaved_clip = {"project": project.url, "scene_key": request.scene_key,
                                 "take_no": request.take_no, "label": label}
            timeline_after = await self._insert_latest(driver, card, timeline_before, stem)
            self.unsaved_clip = None  # proven in the timeline; the Vid saves it
            self._record_scene(project.project_id, timeline_after["active"], request.scene_key,
                               f"{project.project_id}:{request.scene_key}:{request.take_no}")
            evidence = await self._clip_frames_bounded(driver, card, self._evidence(request.scene_key), stem)
        return ClipCandidate(
            clip_id=f"{project.project_id}:{request.scene_key}:{request.take_no}",
            scene_key=request.scene_key, take_no=request.take_no,
            requested_duration_seconds=request.duration_seconds,
            locator={"result_index": before, "label": label,
                     "inserted_scene": timeline_after["scenes"][timeline_after["active"]],
                     "timeline_before": timeline_before, "timeline_after": timeline_after,
                     "frames": [str(f.path) for f in evidence]},
            idempotency_key=request.idempotency_key)

    async def _wait_for_generation(self, driver: PageDriver, before: int) -> None:
        """Success = a new result appears. Failure = the progress indicator went away without
        one (or an error landmark shows), or the timeout passes. Never a guess."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.browser_generation_timeout_seconds
        seen_progress, gone_since = False, None
        while loop.time() < deadline:
            if await driver.count("generate.result") > before:
                return
            for key, exc in (("generate.refused", GenerationRefusedError),
                             ("generate.quota", QuotaExhaustedError), ("generate.error", VidsError)):
                if key in driver.catalogue.locators and await driver.is_present(key):
                    shot = await driver.screenshot(f"failure_{key}")
                    raise exc(f"Vids reported '{key}'", details={"screenshot": str(shot.path)})
            if await driver.is_present("generate.in_progress"):
                seen_progress, gone_since = True, None
            elif seen_progress:
                gone_since = gone_since or loop.time()
                if loop.time() - gone_since > 15:
                    shot = await driver.screenshot("generation_ended_without_result")
                    raise VidsError("generation progress ended without a result",
                                    details={"screenshot": str(shot.path)})
            await asyncio.sleep(2)
        shot = await driver.screenshot("generation_timeout")
        raise GenerationTimeoutError("no generated result within the timeout",
                                     details={"screenshot": str(shot.path)})

    async def _result_seconds(self, card: Any) -> float:
        """The generated result's length. Observed 2026-09-30: the result <video> can still be
        without metadata when it appears (duration read as 0), so wait for it; its label also
        states it ('Generated video: …, 720p, 10 seconds')."""
        seconds = float(await card.evaluate("""v => new Promise(res => {
            if (v.duration > 0) return res(v.duration);
            v.addEventListener('loadedmetadata', () => res(v.duration || 0), {once: true});
            setTimeout(() => res(v.duration || 0), 10000); })""", timeout=15000) or 0)
        if not seconds or seconds != seconds:
            label = (await card.get_attribute("aria-label")) or ""
            match = re.search(r"(\d+(?:\.\d+)?) seconds", label)
            seconds = float(match.group(1)) if match else 0.0
        return seconds

    async def _clip_frames(self, driver: PageDriver, card: Any, out: Path, stem: str) -> list[EvidenceFile]:
        """Frames of the real clip: seek the result <video> (bounded waits) and screenshot it."""
        out.mkdir(parents=True, exist_ok=True)
        frames = []
        # The editor loads canvas media lazily (observed after reopening: duration NaN, 0x0).
        duration = await card.evaluate("""v => new Promise(res => {
            if (v.duration > 0) return res(v.duration);
            v.addEventListener('loadedmetadata', () => res(v.duration || 0), {once: true});
            setTimeout(() => res(v.duration || 0), 10000); })""", timeout=15000)
        if not duration or duration != duration or duration == float("inf"):
            raise VidsError("the clip's video has not loaded; no frames were taken")
        for t in (0.15, 0.5, 0.85):
            at = round(duration * t, 2)
            await card.evaluate("""(v, at) => new Promise(res => {
                const done = () => res(true); v.pause();
                v.addEventListener('seeked', done, {once: true}); setTimeout(done, 3000);
                v.currentTime = at; })""", at, timeout=5000)
            path = out / f"{stem}_frame_{at:05.2f}.png"
            await card.screenshot(path=str(path), timeout=5000)
            frames.append(EvidenceFile(path=path, kind=ArtifactKind.FRAME, mime="image/png",
                                       label=f"frame at {at:.2f}s", meta={"t": at}))
        return frames

    async def _clip_frames_bounded(self, driver: PageDriver, card: Any, out: Path, stem: str) -> list[EvidenceFile]:
        """After insertion the clip is safe; inspection must never undo that by failing."""
        try:
            return await asyncio.wait_for(self._clip_frames(driver, card, out, stem), 30)
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            driver._record("capture frames", "3 frames", f"skipped: {type(exc).__name__}", False)
            return []

    async def _timeline_readback(self, driver: PageDriver) -> dict[str, Any]:
        """Observed 2026-09-30: each timeline thumbnail group holds the scene button ('Scene N
        of M'), its duration label ('5.0', always in the DOM, timing shown or not), and the
        active scene carries an 'active' border class. The player shows the total as mm:ss.f.
        Selection is not saved: a reopened Vid starts on scene 1."""
        groups = driver.locator("timeline.scene_group")
        scenes, durations, active_at = [], [], []
        for i in range(await groups.count()):
            group = groups.nth(i)
            scene = driver.locator("timeline.scene", root=group)
            scenes.append(" ".join((await scene.first.get_attribute("aria-label") or "").split()))
            label = driver.locator("timeline.scene_duration", root=group)
            durations.append(float((await label.first.text_content()) or "nan") if await label.count() else None)
            if await driver.locator("timeline.scene_active", root=group).count():
                active_at.append(i)
        # By position, not label: labels can go stale ('Scene 2 of 2' in a 6-scene Vid, observed).
        active = active_at[0] if len(active_at) == 1 else None
        total = parse_timecode(await driver.read_text("timeline.duration"))
        return {"scenes": scenes, "durations": durations, "active": active, "total_seconds": total}

    async def _wait_timeline(self, driver: PageDriver, ok: Any, what: str, key: str,
                             timeout_s: float = 10) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            state = await self._timeline_readback(driver)
            if ok(state):
                return state
            if loop.time() >= deadline:
                raise await driver.mismatch(f"{what}; the timeline reads {state}", key)
            await asyncio.sleep(0.5)

    async def _active_scene_has_video(self, driver: PageDriver) -> bool:
        """Only the active scene's canvas video is visible; others stay in the DOM hidden."""
        return await driver.count("canvas.video") > 0

    async def _select_last_scene(self, driver: PageDriver) -> dict[str, Any]:
        tl = await self._timeline_readback(driver)
        if not tl["scenes"]:
            await driver.click("timeline.new_scene_empty")
            tl = await self._wait_timeline(driver, lambda t: len(t["scenes"]) == 1,
                                           "no scene appeared in the empty Vid", "timeline.new_scene_empty")
        last = len(tl["scenes"]) - 1
        if tl["active"] != last:
            await driver.locator("timeline.scene").nth(last).click(timeout=driver.timeout_ms)
            tl = await self._wait_timeline(driver, lambda t: t["active"] == last,
                                           f"scene {last + 1} did not become active", "timeline.scene")
        return tl

    async def _append_blank_scene(self, driver: PageDriver) -> dict[str, Any]:
        """Observed: 'New scene' adds a blank scene after the active one and makes it active."""
        before = await self._select_last_scene(driver)
        await driver.click("timeline.new_scene")
        tl = await self._wait_timeline(
            driver, lambda t: len(t["scenes"]) == len(before["scenes"]) + 1
            and t["active"] == len(before["scenes"]) and t["durations"][-1] is not None
            and abs(t["total_seconds"] - before["total_seconds"] - t["durations"][-1]) <= 0.2,
            "'New scene' did not add one blank scene at the end", "timeline.new_scene")
        if await self._active_scene_has_video(driver):
            raise await driver.mismatch("the new scene shows a video", "canvas.video")
        return tl

    async def _last_scene_is_taken(self, driver: PageDriver, tl: dict[str, Any], project_id: str | None) -> bool:
        """Does the (active) last scene already hold something? Observed 2026-09-30: right
        after a Vid is reopened its canvas video is not drawn yet, so 'no visible video' alone
        read a filled scene as blank. So: a clip recorded there, a length other than a blank
        scene's 5.0 s, or a video that appears within a few seconds."""
        last = len(tl["scenes"]) - 1
        if project_id and str(last) in self._scene_map(project_id):
            return True
        if tl["durations"] and tl["durations"][last] not in (None, BLANK_SCENE_SECONDS):
            return True
        for _ in range(6):
            if await self._active_scene_has_video(driver):
                return True
            await asyncio.sleep(0.5)
        return False

    async def _prepare_blank_scene(self, driver: PageDriver, stem: str, *, force_new: bool = False,
                                   project_id: str | None = None) -> dict[str, Any]:
        """Make the LAST scene the active one and ensure it holds no video, adding a blank
        scene after it when it does. Observed: a new Vid has one blank 5.0 s scene; an empty
        Vid has none (then AI video is disabled); a reopened Vid starts on scene 1. Returns
        the verified timeline."""
        tl = await self._select_last_scene(driver)
        if force_new or await self._last_scene_is_taken(driver, tl, project_id):
            tl = await self._append_blank_scene(driver)
        await driver.screenshot(f"{stem}_blank_scene_ready")
        driver._record("prepare scene", "active last scene without video",
                       f"scene {tl['active'] + 1} of {len(tl['scenes'])}", True)
        return tl

    async def add_blank_scene(self, project: ProjectHandle, *, force_new: bool = False) -> dict[str, Any]:
        """Prepare the scene the next clip will fill (no generation): the last scene if it is
        blank, else a new blank scene after it. `force_new` always appends one."""
        self._guard_live()
        self._require("timeline.add_scene")
        async with self.manager.lock:
            driver = await self._driver(self._evidence("scenes"))
            return await self._prepare_blank_scene(driver, "prepare", force_new=force_new,
                                                   project_id=project.project_id)

    async def _insert_latest(self, driver: PageDriver, card: Any, before: dict[str, Any],
                             stem: str) -> dict[str, Any]:
        """Insert the new result, then prove it. Two observed outcomes (2026-09-30):
        * the active scene is blank: Insert fills it, and it takes the clip's length;
        * the active scene holds a clip: Vids shows a preview with 'Insert in new scene',
          which adds the clip as a new scene after it.
        Proof either way: that scene is the clip's length, the total changed by the same, and a
        visible canvas <video> has the result's duration."""
        clip_seconds = await self._result_seconds(card)
        index, old = before["active"], before["durations"][before["active"]]
        driver.irreversible_done = True
        await driver.click("generate.result.insert")
        new_scene = False
        for _ in range(8):  # the preview offering a new scene appears within a moment, or not at all
            if "generate.insert_new_scene" in driver.catalogue.locators and await driver.locator("generate.insert_new_scene").first.is_visible():
                await driver.click("generate.insert_new_scene")
                new_scene = True
                break
            await asyncio.sleep(0.4)
        loop = asyncio.get_running_loop()
        deadline, after, on_canvas = loop.time() + self.insert_timeout_s, before, None
        target = index + 1 if new_scene else index
        want_total = before["total_seconds"] - (0 if new_scene else old)  # + the new scene's length
        while loop.time() < deadline:
            after = await self._timeline_readback(driver)
            videos = driver.locator("canvas.video")
            on_canvas = (float(await videos.first.evaluate("v => v.duration || 0", timeout=5000))
                         if await videos.count() == 1 else None)
            scene = after["durations"][target] if len(after["durations"]) > target else None
            count_ok = len(after["scenes"]) == len(before["scenes"]) + (1 if new_scene else 0)
            if (count_ok and after["active"] == target and scene is not None and abs(scene - clip_seconds) <= 0.6
                    and abs(after["total_seconds"] - (want_total + scene)) <= 0.2
                    and on_canvas is not None and abs(on_canvas - clip_seconds) <= 0.1):
                await driver.screenshot(f"{stem}_inserted")
                driver._record("insert", f"scene {target + 1} = {clip_seconds:.2f}s clip",
                               f"scene {target + 1} {scene:.1f}s ({'new scene' if new_scene else 'filled'}), "
                               f"canvas video {on_canvas:.2f}s, total {after['total_seconds']:.1f}s", True)
                return {**after, "clip_seconds": clip_seconds, "canvas_video_seconds": on_canvas}
            await asyncio.sleep(1)
        raise await driver.mismatch(
            f"after Insert the timeline reads {after}, canvas video {on_canvas}; expected scene "
            f"{target + 1} to hold the {clip_seconds:.2f}s clip", "generate.result.insert")

    async def regenerate_scene(self, project: ProjectHandle, request: ClipRequest) -> ClipCandidate:
        """Only the defective scene, with its revised prompt: a new generation, new take."""
        return await self.generate_scene(project, request)

    async def inspect_scene(self, project: ProjectHandle, clip: ClipCandidate,
                            evidence_dir: Path) -> ClipEvidence:
        """Frames were captured from the real result <video> before insertion (the result is
        gone after a reload); inspection uses those files."""
        self._guard_live()
        self._require("clip.capture_frames")
        frames = [Path(p) for p in clip.locator.get("frames", []) if Path(p).exists()]
        if not frames:
            raise CapabilityUnavailableError("no frames were captured for this clip at generation")
        evidence_dir.mkdir(parents=True, exist_ok=True)
        return ClipEvidence(
            clip_id=clip.clip_id, duration_seconds=clip.requested_duration_seconds,
            frames=[EvidenceFile(path=p, kind=ArtifactKind.FRAME, mime="image/png",
                                 label=p.stem) for p in frames],
            notes=["Frames captured from the generated <video> before insertion."])

    async def edit_scene(self, project: ProjectHandle, clip: ClipCandidate, instruction: str,
                         take_no: int, idempotency_key: str) -> ClipCandidate:
        self._guard_live()
        self._require("clip.edit_text")
        raise CapabilityUnavailableError(
            "the Omni edit recipe is not implemented until recon records how an edit result is "
            "presented (a new result card, or in place)")

    async def assemble_timeline(self, project: ProjectHandle, clip: ClipCandidate,
                                position: int) -> TimelineItem:
        """generate_scene already inserted the clip (it cannot survive otherwise) and proved it
        with the timeline read-back; this records that result and never clicks Insert again."""
        self._guard_live()
        self._require("clip.insert")
        after = clip.locator.get("timeline_after")
        if not after or not clip.locator.get("inserted_scene"):
            raise CapabilityUnavailableError(
                "this clip was not inserted when it was generated; it cannot be inserted later")
        return TimelineItem(item_id=f"{clip.clip_id}:item", clip_id=clip.clip_id,
                            scene_key=clip.scene_key, position=len(after["scenes"]) - 1,
                            duration_seconds=clip.requested_duration_seconds)

    async def export_video(self, project: ProjectHandle, spec: ExportSpec, out_dir: Path) -> ExportResult:
        """File → Download → MP4, in a fresh browser, retried when Chrome crashes.

        Observed 2026-09-30: Chrome's browser process crashed (crash key LEGACY_DOWNLOAD) about
        2 s into 'Downloading…' on 3 of 4 attempts for a 3-scene Vid, from long and fresh
        sessions alike; one attempt exported cleanly. An export changes nothing in the Vid and
        spends no quota, so up to `export_attempts` fresh-browser attempts are made; each crash
        is recorded in the result."""
        crashes: list[str] = []
        for attempt in range(1, self.export_attempts + 1):
            try:
                result = await self._export_once(project, spec, out_dir)
            except ExportCrashedError as exc:
                crashes.append(f"attempt {attempt}: {exc.message}")
                log.warning("export attempt crashed", extra={"attempt": attempt})
                continue
            result.details["crashed_attempts"] = crashes
            return result
        raise VidsError(f"Chrome crashed during all {self.export_attempts} export attempts",
                        details={"crashes": crashes})

    async def _export_once(self, project: ProjectHandle, spec: ExportSpec, out_dir: Path) -> ExportResult:
        """Observed: File → Download → 'MP4 video (.mp4)' shows 'Preparing download…',
        'Downloading…', 'Download started', then the browser downloads
        '<title>.mp4' (H.264 1920x1080 30 fps). A download listener is attached before the
        click and the wait is bounded; the file is then verified by `verify_export`."""
        self._guard_live()
        self._require("export.download_mp4")
        self._guard_unsaved_clip("restart Chrome for the export")
        # Observed 2026-09-30: Chrome crashed ~2 s into 'Downloading…' when a 3-scene Vid was
        # exported at the end of a long editing session (twice, crash dumps written); the same
        # Vid exported cleanly from a freshly started browser. So the export always runs in a
        # fresh browser, on the reopened Vid. (close() takes the lock itself.)
        await self.manager.close()
        async with self.manager.lock:
            driver = await self._driver(out_dir)
            await driver.page.goto(project.url, wait_until="load")
            await self._dismiss_getting_started(driver)
            await driver.expect_one("editor.ready", self.settings.browser_action_timeout_ms * 2)
            out_dir.mkdir(parents=True, exist_ok=True)
            downloads: list[Any] = []
            arrived = asyncio.Event()

            def on_download(d: Any) -> None:
                downloads.append(d)
                arrived.set()

            driver.page.on("download", on_download)
            try:
                await self._open_download_menu(driver)
                await driver.click("menu.file.download_mp4", irreversible=True)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + self.settings.export_timeout_seconds
                while not arrived.is_set() and loop.time() < deadline:
                    if driver.page.is_closed():  # the browser crashed before the download began
                        raise ExportCrashedError("Chrome closed while Vids prepared the download "
                                                 "(a browser crash); nothing in the Vid changed")
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(arrived.wait(), 2)
            finally:
                with contextlib.suppress(Exception):
                    driver.page.remove_listener("download", on_download)
            if not downloads:
                shot = await driver.screenshot("export_no_download")
                raise VidsError("Vids did not start a download within the export timeout",
                                details={"screenshot": str(shot.path)})
            download = downloads[0]
            failure = await download.failure()
            if failure and "closed" in failure:
                raise ExportCrashedError("Chrome closed during the export download (a browser crash); "
                                         "nothing in the Vid changed", details={"failure": failure})
            if failure:
                raise VidsError(f"the download failed: {failure}")
            target = out_dir / f"{project.project_id}-{spec.idempotency_key[-8:]}.mp4"
            await download.save_as(str(target))
        return ExportResult(path=target, details={"suggested_name": download.suggested_filename})

    async def _open_download_menu(self, driver: PageDriver, attempts: int = 3) -> None:
        """File → Download until 'MP4 video (.mp4)' shows. Observed 2026-09-30: right after a
        fresh load the submenu once did not open; the menus are simply opened again."""
        for attempt in range(attempts):
            await driver.page.wait_for_timeout(1500 if attempt else 500)
            await driver.click("menu.file")
            with contextlib.suppress(UIMismatchError):
                item = await driver.expect_one("menu.file.download", 5000)
                await driver.click("menu.file.download")
                if not await driver.locator("menu.file.download_mp4").first.is_visible():
                    # Observed on a second account: the submenu opens on hover, not on click.
                    await item.hover(timeout=driver.timeout_ms)
                    await driver.page.wait_for_timeout(1000)
                await driver.expect_one("menu.file.download_mp4", 5000)
                return
            await driver.press_key("Escape")
            await driver.press_key("Escape")
        raise await driver.mismatch("the File → Download menu did not open", "menu.file.download_mp4")

    async def verify_export(self, path: Path) -> dict[str, Any]:
        """Probe and fully decode the downloaded file (the EXPORT stage does the rules)."""
        probe = await ffmpeg.probe(path)
        analysis = await ffmpeg.analyse(path, has_audio=probe.has_audio)
        return {"probe": probe.to_dict(), "decode_ok": analysis.decode_ok}

    async def close(self, *, force: bool = False) -> None:
        if self.unsaved_clip is not None and not force:
            log.error("browser left open: a generated clip is not inserted", extra={"clip": self.unsaved_clip})
            return
        await self.manager.close()

    # ================================================================== workflow port
    async def open_or_create_project(self, title: str, existing: ProjectHandle | None,
                                     idempotency_key: str) -> ProjectHandle:
        await self._resume(None)
        if existing is not None:
            return await self.open_project(existing)
        return await self.create_project(title)

    async def generate_clip(self, project: ProjectHandle, request: ClipRequest) -> ClipCandidate:
        await self._resume(project)
        return await self.generate_scene(project, request)

    async def capture_clip(self, project: ProjectHandle, clip: ClipCandidate,
                           evidence_dir: Path) -> ClipEvidence:
        await self._resume(project)
        return await self.inspect_scene(project, clip, evidence_dir)

    async def edit_clip(self, project: ProjectHandle, clip: ClipCandidate, instruction: str,
                        take_no: int, idempotency_key: str) -> ClipCandidate:
        return await self.edit_scene(project, clip, instruction, take_no, idempotency_key)

    async def insert_clip(self, project: ProjectHandle, clip: ClipCandidate, position: int,
                          idempotency_key: str) -> TimelineItem:
        await self._resume(project)
        return await self.assemble_timeline(project, clip, position)

    # Which planned scene fills which timeline position. The editor itself does not say (a
    # scene is only 'Scene N of M'), so it is recorded when a clip is inserted and kept on disk
    # next to the evidence, surviving restarts.
    def _scene_map_path(self, project_id: str) -> Path:
        return self._evidence("scene_map") / f"{project_id}.json"

    def _scene_map(self, project_id: str) -> dict[str, dict[str, str]]:
        path = self._scene_map_path(project_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _record_scene(self, project_id: str, position: int, scene_key: str, clip_id: str) -> None:
        mapping = self._scene_map(project_id)
        mapping[str(position)] = {"scene_key": scene_key, "clip_id": clip_id}
        path = self._scene_map_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping, indent=1), encoding="utf-8")

    def scene_position(self, project_id: str, scene_key: str) -> int | None:
        for position, entry in self._scene_map(project_id).items():
            if entry["scene_key"] == scene_key:
                return int(position)
        return None

    async def read_timeline(self, project: ProjectHandle) -> TimelineState:
        """Scenes in order with their durations and the total (see `_timeline_readback`), each
        named by the planned scene whose clip was inserted there. A position with no recorded
        clip reads as 'unplanned-N', which the assembly check then reports."""
        await self._resume(project)
        self._guard_live()
        self._require("timeline.read")
        async with self.manager.lock:
            driver = await self._driver(self._evidence("timeline"))
            tl = await self._timeline_readback(driver)
            if any(d is None for d in tl["durations"]):
                raise await driver.mismatch(f"a scene duration could not be read: {tl}", "timeline.scene_duration")
            # Each scene's text boxes, read from its canvas (a box shows for its whole scene).
            overlays, logos, start = [], [], 0.0
            for i, d in enumerate(tl["durations"]):
                window = {"position_index": i, "start": round(start, 2), "end": round(start + d, 2)}
                if self.capabilities.is_verified("timeline.text_overlay"):
                    await self._select_scene(driver, i)
                    for text in await self._scene_boxes(driver):
                        overlays.append({"text": text, **window})
                if self.capabilities.is_verified("timeline.image"):
                    await self._select_scene(driver, i)
                    if await self._corner_images(driver):
                        logos.append({"position": "top-right", **window})
                start += d
        mapping = self._scene_map(project.project_id)
        items = []
        for i, d in enumerate(tl["durations"]):
            entry = mapping.get(str(i), {})
            items.append(TimelineItem(item_id=f"{project.project_id}:scene:{i + 1}", clip_id=entry.get("clip_id", ""),
                                      scene_key=entry.get("scene_key", f"unplanned-{i + 1}"), position=i,
                                      duration_seconds=d))
        for o in [*overlays, *logos]:
            o["scene_key"] = items[o.pop("position_index")].scene_key
        return TimelineState(items=items, text_overlays=overlays, logos=logos,
                             total_duration_seconds=tl["total_seconds"])

    async def add_text_overlay(self, project: ProjectHandle, text: str, *,
                               scene_index: int | None = None, kind: str = "title",
                               layout: dict[str, float] | None = None) -> list[str]:
        """Add a title or body text box to a scene and set its text, without editing keys.

        Observed 2026-09-30. Keystrokes go through one hidden iframe that always has focus,
        and the timeline also listens: End / Shift+Home once selected all four scenes, and
        Backspace outside text editing deleted scenes (twice). A visible caret is no signal:
        it also shows after clicking a scene. What does work: 'Insert text' → 'Add a title'
        ('Add body text') adds a box reading 'Title' ('Body text'); double-clicking the word
        (triple-clicking the paragraph, for two words) on the canvas selects it, and a
        text-selection highlight is drawn over it; typing replaces it. So no key is sent until
        that highlight covers the default text, only printable text is typed (never Backspace,
        Delete or Enter), and the result is read back from the canvas text alone. The scene
        count is checked before and after. Body text wraps inside its box; punctuation types
        as is. Title and body boxes both appear centred, so a scene gets one of them."""
        if any(ch in text for ch in "\n\r\t") or not text.strip():  # Enter/Tab are never typed
            raise ValueError("overlay text must be one non-empty line")
        if kind not in TEXT_BOXES:
            raise ValueError(f"kind must be one of {sorted(TEXT_BOXES)}")
        add_key, default = TEXT_BOXES[kind]
        self._guard_live()
        self._require("timeline.text_overlay")
        async with self.manager.lock:
            driver = await self._driver(self._evidence("text"))
            tl = await self._timeline_readback(driver)
            if scene_index is not None and tl["active"] != scene_index:
                await driver.locator("timeline.scene").nth(scene_index).click(timeout=driver.timeout_ms)
                tl = await self._wait_timeline(driver, lambda t: t["active"] == scene_index,
                                               f"scene {scene_index + 1} did not become active", "timeline.scene")
            scenes_before = len(tl["scenes"])
            if _count_run(await self._canvas_text(driver), default):  # which box is new would be a guess
                raise await driver.mismatch(f"the scene already has a default {' '.join(default)!r} box",
                                            "text.canvas_text")
            # The rail button toggles the Text panel, which stays open after adding a box
            # (observed): open it only when the entry is not already showing.
            if not await driver.locator(add_key).first.is_visible():
                await driver.click("text.insert")
            await driver.click(add_key)
            deadline = asyncio.get_running_loop().time() + 10
            while (start := _find_run(await self._canvas_text(driver), default)) is None:
                if asyncio.get_running_loop().time() > deadline:
                    raise await driver.mismatch(f"no new {' '.join(default)!r} box appeared", add_key)
                await asyncio.sleep(0.3)
            words = driver.locator("text.canvas_text")
            first = words.nth(start)
            # A hit-area path overlays the text, so the clicks are forced onto the word itself.
            if len(default) == 1:  # the verified title gesture: double-click the word
                await first.dblclick(force=True, timeout=driver.timeout_ms)
            else:  # two or more words: triple-click selects the paragraph (observed)
                await first.click(click_count=3, force=True, timeout=driver.timeout_ms)
            boxes = [await words.nth(start + i).bounding_box(timeout=driver.timeout_ms) for i in range(len(default))]
            await self._require_text_selection(driver, _union(boxes))
            await driver.page.keyboard.type(text, delay=15)
            rendered = await self._canvas_text(driver)
            after = await self._timeline_readback(driver)
            if len(after["scenes"]) != scenes_before:
                raise await driver.mismatch(f"the scene count changed while typing: {after}", "timeline.scene")
            if not canvas_has_text(rendered, text) or _count_run(rendered, default):
                raise await driver.mismatch(f"canvas text {rendered} does not read {text!r}", "text.canvas_text")
            await driver.press_key("Escape")  # leaves text editing; the box stays selected (observed)
            if layout:  # e.g. {"w": 780, "x": 48}: width first (the height reflows), then place it
                geo = await self._set_geometry(driver, w=layout["w"], x=layout["x"])
                y = CANVAS[1] - geo["h"] - layout["bottom"] if "bottom" in layout else (CANVAS[1] - geo["h"]) / 2
                geo = await self._set_geometry(driver, y=round(y, 1))
                driver._record("text layout", str(layout), str(geo), True)
            await driver.press_key("Escape")
            await driver.screenshot(f"text_overlay_set_{kind}")
            driver._record("text overlay", text, " ".join(rendered), True)
        return rendered

    async def _canvas_text(self, driver: PageDriver) -> list[str]:
        loc = driver.locator("text.canvas_text")
        return [((await loc.nth(i).text_content()) or "").strip() for i in range(await loc.count())]

    async def _require_text_selection(self, driver: PageDriver, target: dict[str, float] | None) -> None:
        """No key is sent unless the default text itself is highlighted: a visible
        text-selection overlay covering it (observed only while editing that text; the
        toolbar is no signal: with the Text panel open its text buttons overflow)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        while loop.time() < deadline:
            overlays = driver.locator("text.selection")
            covered = 0.0
            for i in range(await overlays.count()):
                box = await overlays.nth(i).bounding_box()
                if box and target:
                    covered += _overlap(box, target)
            if covered > 0.8:
                return
            await asyncio.sleep(0.25)
        raise await driver.mismatch("the default text is not selected for editing; no keys were sent",
                                    "text.selection")

    # ------------------------------------------------------------------ layout
    async def _open_format_options(self, driver: PageDriver) -> None:
        """Observed: Format → Format options opens a side panel for the SELECTED object with
        pixel fields (canvas 1920x1080): Size and rotation (Width, Height) and Position (X, Y
        from the top left). Its sections are buttons with aria-expanded."""
        if not await driver.locator("format.x").first.is_visible():
            await driver.click("menu.format")
            await driver.click("menu.format.options")
        for section in ("format.size_section", "format.position_section"):
            button = await driver.expect_one(section)
            if await button.get_attribute("aria-expanded") != "true":
                await button.click(timeout=driver.timeout_ms)
        await driver.expect_one("format.x")

    async def _read_geometry(self, driver: PageDriver) -> dict[str, float]:
        await self._open_format_options(driver)
        return {k: float(await driver.locator(key).first.input_value()) for k, key in GEOMETRY_FIELDS.items()}

    async def _set_geometry(self, driver: PageDriver, **values: float) -> dict[str, float]:
        """Fill the pixel fields (size before position), each read back; Tab commits a field
        and moves to the next field (observed; focus stays in the panel)."""
        await self._open_format_options(driver)
        for k in ("w", "h", "x", "y"):
            if k in values:
                value = values[k]
                await driver.fill_verified(GEOMETRY_FIELDS[k], f"{value:g}")
                await driver.press_key("Tab")
                await driver.page.wait_for_timeout(400)
        geo = await self._read_geometry(driver)
        off = {k: v for k, v in values.items() if abs(geo[k] - v) > 1}
        if off:
            raise await driver.mismatch(f"the object did not take {off}; it reads {geo}", "format.x")
        return geo

    async def insert_image(self, project: ProjectHandle, path: Path, *, scene_index: int | None = None,
                           align: str = "right", margin: float = 32, corner_width: float | None = None) -> dict[str, float]:
        """Upload a still image into a scene and place it, no generation.

        Observed 2026-09-30: Insert → Upload opens a file chooser; the image lands on the
        ACTIVE scene's canvas (an SVG <image>), scaled to fit the height (800x800 → 1016x1016),
        top left at (32, 32), selected; the scene keeps its length. It is then aligned right (or
        left) and centred vertically through Format options. A 'Convert to video' button shows
        on the selected image: it is never clicked (it would animate the image)."""
        path = Path(path)
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise ValueError(f"{path} is not a PNG or JPEG file")
        if align not in ("left", "right"):
            raise ValueError("align must be 'left' or 'right'")
        self._guard_live()
        self._require("timeline.image")
        async with self.manager.lock:
            driver = await self._driver(self._evidence("images"))
            tl = await self._timeline_readback(driver)
            if scene_index is not None and tl["active"] != scene_index:
                await driver.locator("timeline.scene").nth(scene_index).click(timeout=driver.timeout_ms)
                tl = await self._wait_timeline(driver, lambda t: t["active"] == scene_index,
                                               f"scene {scene_index + 1} did not become active", "timeline.scene")
            before = await driver.count("canvas.image")
            await driver.click("menu.insert")
            async with driver.page.expect_file_chooser(timeout=driver.timeout_ms) as chooser:
                await driver.click("menu.insert.upload")
            await (await chooser.value).set_files(str(path))
            deadline = asyncio.get_running_loop().time() + 30
            while await driver.count("canvas.image") != before + 1:
                if asyncio.get_running_loop().time() > deadline:
                    raise await driver.mismatch("the uploaded image did not appear on the canvas", "canvas.image")
                await asyncio.sleep(0.5)
            after = await self._timeline_readback(driver)
            if after["durations"] != tl["durations"]:
                raise await driver.mismatch(f"the timeline changed: {tl} → {after}", "timeline.scene")
            size = await self._read_geometry(driver)
            if corner_width:  # a logo: small, top right, its own proportions kept
                h = round(corner_width * size["h"] / size["w"], 1)
                geo = await self._set_geometry(driver, w=corner_width, h=h,
                                               x=round(CANVAS[0] - margin - corner_width, 1), y=margin)
            else:
                x = CANVAS[0] - margin - size["w"] if align == "right" else margin
                geo = await self._set_geometry(driver, x=round(x, 1), y=round((CANVAS[1] - size["h"]) / 2, 1))
            await driver.screenshot(f"image_placed_{path.stem[:20]}")
            driver._record("insert image", path.name, str(geo), True)
        return geo

    async def place_scene_video(self, project: ProjectHandle, scene_index: int, *,
                                w: float, h: float, x: float, y: float) -> dict[str, float]:
        """Resize and move the clip filling a scene (select it on the canvas, then Format
        options). Changes layout only: the clip, its length and the timeline stay."""
        self._guard_live()
        self._require("timeline.layout_video")
        async with self.manager.lock:
            driver = await self._driver(self._evidence("layout"))
            tl = await self._timeline_readback(driver)
            if tl["active"] != scene_index:
                await driver.locator("timeline.scene").nth(scene_index).click(timeout=driver.timeout_ms)
                tl = await self._wait_timeline(driver, lambda t: t["active"] == scene_index,
                                               f"scene {scene_index + 1} did not become active", "timeline.scene")
            video = await driver.expect_one("canvas.video")
            await video.click(force=True, timeout=driver.timeout_ms)  # selects it (overlays intercept)
            geo = await self._set_geometry(driver, w=w, h=h, x=x, y=y)
            after = await self._timeline_readback(driver)
            if after["durations"] != tl["durations"]:
                raise await driver.mismatch(f"the timeline changed: {tl} → {after}", "timeline.scene")
            await driver.screenshot(f"scene_{scene_index + 1}_video_placed")
        return geo

    async def apply_timeline_op(self, project: ProjectHandle, op: TimelineOpSpec) -> TimelineState:
        await self._resume(project)
        self._guard_live()
        capability = {
            TimelineOpKind.ARRANGE: "timeline.arrange", TimelineOpKind.TRANSITION: "timeline.transition",
            TimelineOpKind.TEXT_OVERLAY: "timeline.text_overlay", TimelineOpKind.LOGO: "timeline.logo",
            TimelineOpKind.VOICEOVER: "timeline.voiceover", TimelineOpKind.MUSIC: "timeline.music",
            TimelineOpKind.CAPTIONS: "timeline.captions",
        }[op.kind]
        if op.kind is TimelineOpKind.LOGO and op.params.get("asset_path"):
            self._require("timeline.image")  # the uploaded logo is an image placed top right
            return await self._apply_logo(project, op.params)
        self._require(capability)
        if op.kind is TimelineOpKind.TEXT_OVERLAY:
            return await self._apply_text_overlay(project, op.params)
        raise CapabilityUnavailableError(f"no recipe for {op.kind.value} until recon records it")

    async def _apply_text_overlay(self, project: ProjectHandle, params: dict[str, Any]) -> TimelineState:
        """A scene's text: a body text box in the left column, with the scene's clip moved to
        the right column so the text is on white (layout verified 2026-09-30). A disclosure: a
        full-width strip at the bottom of its scene. A box is on screen for its whole scene.
        Re-applying is safe: text already on the scene is not added again."""
        key = params.get("scene_key")
        position = self.scene_position(project.project_id, key) if key else None
        if position is None:
            raise VidsError(f"no scene in the Vid holds {key or 'this text'}; the text was not added",
                            details={"params": params})
        text = params["text"]
        if params.get("role") == "disclosure":
            layout: dict[str, float] = {"w": 1824, "x": 48, "bottom": 40}
        else:
            layout = {"w": 780, "x": 48}
            if self.capabilities.is_verified("timeline.layout_video") and await self._scene_clip_is_full_frame(position):
                await self.place_scene_video(project, position, **RIGHT_COLUMN_VIDEO)
        if canvas_has_text(await self._scene_texts(project, position), text):
            await self._relayout_box(position, text, layout)  # a retry fixes the layout, adds nothing
        else:
            await self.add_text_overlay(project, text, scene_index=position, kind="body", layout=layout)
        return await self.read_timeline(project)

    async def _relayout_box(self, position: int, text: str, layout: dict[str, float]) -> None:
        """Select the box holding `text` (a single click selects a box, observed) and set its
        size and position again."""
        async with self.manager.lock:
            driver = await self._driver(self._evidence("text"))
            await self._select_scene(driver, position)
            box_id = await driver.locator("text.canvas_text").evaluate_all(
                """(els, want) => { const boxes = {};
                    for (const t of els) { const id = ((t.closest('g[id]') || {}).id || '').split('-paragraph-')[0];
                        (boxes[id] = boxes[id] || []).push((t.textContent || '').trim()); }
                    const norm = s => s.split(/\\s+/).join(' ').toLowerCase();
                    return Object.keys(boxes).find(id => norm(boxes[id].join(' ')).includes(norm(want))) || null; }""", text)
            if not box_id:
                return
            await driver.page.locator(f'g[id^="{box_id}-paragraph-"] text').first.click(force=True, timeout=driver.timeout_ms)
            geo = await self._set_geometry(driver, w=layout["w"], x=layout["x"])
            y = CANVAS[1] - geo["h"] - layout["bottom"] if "bottom" in layout else (CANVAS[1] - geo["h"]) / 2
            await self._set_geometry(driver, y=round(y, 1))
            await driver.press_key("Escape")

    async def _apply_logo(self, project: ProjectHandle, params: dict[str, Any]) -> TimelineState:
        """The uploaded logo in the top-right corner of its scene, 260 px wide, 40 px in from
        the edges (upload + pixel layout, both verified). Re-applying is safe."""
        key = params.get("scene_key")
        position = self.scene_position(project.project_id, key) if key else None
        if position is None:
            raise VidsError(f"no scene in the Vid holds {key}; the logo was not added", details={"params": params})
        path = Path(params["asset_path"])
        async with self.manager.lock:
            driver = await self._driver(self._evidence("images"))
            await self._select_scene(driver, position)
            present = await self._corner_images(driver)
        if not present:
            await self.insert_image(project, path, scene_index=position, margin=LOGO_MARGIN, corner_width=LOGO_WIDTH)
        return await self.read_timeline(project)

    async def _corner_images(self, driver: PageDriver) -> int:
        """Images on the active scene's canvas that sit in its top-right corner (a logo)."""
        return await driver.locator("canvas.image").evaluate_all("""els => els.filter(img => {
            const svg = img.closest('svg'); if (!svg) return false;
            const c = svg.getBoundingClientRect(), b = img.getBoundingClientRect();
            return b.width > 0 && b.width < 0.3 * c.width && b.right > c.right - 0.12 * c.width && b.top < c.top + 0.25 * c.height;
        }).length""")

    async def _select_scene(self, driver: PageDriver, position: int) -> None:
        tl = await self._timeline_readback(driver)
        if tl["active"] != position:
            await driver.locator("timeline.scene").nth(position).click(timeout=driver.timeout_ms)
            await self._wait_timeline(driver, lambda t: t["active"] == position,
                                      f"scene {position + 1} did not become active", "timeline.scene")
            await driver.page.wait_for_timeout(600)

    async def _scene_boxes(self, driver: PageDriver) -> list[str]:
        """The active scene's text boxes, each as its words joined (a box's words are separate
        <text> elements under 'editor-<box>-paragraph-<n>' groups, observed)."""
        pairs = await driver.locator("text.canvas_text").evaluate_all(
            "els => els.map(t => [((t.closest('g[id]') || {}).id || ''), (t.textContent || '').trim()])")
        boxes: dict[str, list[str]] = {}
        for group_id, word in pairs:
            if word:
                boxes.setdefault(group_id.split("-paragraph-")[0], []).append(word)
        return [" ".join(words) for words in boxes.values()]

    async def _scene_texts(self, project: ProjectHandle, position: int) -> list[str]:
        async with self.manager.lock:
            driver = await self._driver(self._evidence("timeline"))
            await self._select_scene(driver, position)
            return await self._scene_boxes(driver)

    async def _scene_clip_is_full_frame(self, position: int) -> bool:
        async with self.manager.lock:
            driver = await self._driver(self._evidence("layout"))
            await self._select_scene(driver, position)
            videos = driver.locator("canvas.video")
            if await videos.count() != 1:
                return False
            box = await videos.first.bounding_box()
            canvas = await driver.page.evaluate("() => window.innerWidth")
            return bool(box) and box["width"] > 0.45 * canvas

    async def capture_preview(self, project: ProjectHandle, evidence_dir: Path) -> list[EvidenceFile]:
        """One screenshot of the canvas per scene: select each scene in turn (a click on its
        timeline thumbnail changes nothing in the Vid) and capture the editor."""
        await self._resume(project)
        self._guard_live()
        self._require("preview.capture")
        async with self.manager.lock:
            driver = await self._driver(evidence_dir)
            shots = []
            mapping = self._scene_map(project.project_id)
            tl = await self._timeline_readback(driver)
            for i in range(len(tl["scenes"])):
                await driver.locator("timeline.scene").nth(i).click(timeout=driver.timeout_ms)
                await self._wait_timeline(driver, lambda t, i=i: t["active"] == i,
                                          f"scene {i + 1} did not become active", "timeline.scene")
                await driver.page.wait_for_timeout(1200)  # let the canvas media draw
                shot = await driver.screenshot(f"preview_scene_{i + 1}")
                shot.meta["scene_key"] = mapping.get(str(i), {}).get("scene_key", f"unplanned-{i + 1}")
                shot.label = f"preview of {shot.meta['scene_key']}"
                shots.append(shot)
            if not shots:
                raise await driver.mismatch("the Vid has no scene to preview", "timeline.scene")
            return shots

    async def export(self, project: ProjectHandle, spec: ExportSpec, out_dir: Path) -> ExportResult:
        await self._resume(project)
        return await self.export_video(project, spec, out_dir)

    async def reconcile(self, project: ProjectHandle | None, kind: IntentKind, idempotency_key: str,
                        request: dict[str, Any]) -> ReconcileResult:
        # No verified way yet to match a past action to what the editor shows. Claiming FOUND
        # or NOT_FOUND without one risks a duplicate (quota) or a silently lost generation, so
        # the project is inspected by a person before anything is submitted again.
        return ReconcileResult(
            status=ReconcileStatus.UNKNOWN,
            note="Open the Vid and check whether this action happened before the agent retries.")
