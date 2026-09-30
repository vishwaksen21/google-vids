"""The locator catalogue and the guarded page driver.

No Google Vids selector is written in code. Every element an operation touches is looked
up by key in `recon/locators.json`, which recon produces from the live UI (see
`diagnostics.py` for discovery). Locator strategies, in order of preference: accessible
role + name, label, placeholder, test id, and CSS only as a last resort. Coordinates are
never used.

The driver's primitives either do exactly what they say and prove it (an element that
appears exactly once, a field that reads back the exact text typed), or raise with a
screenshot. An absent or ambiguous element is a UI mismatch: the agent stops instead of
clicking something else.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from vidsprod.domain.enums import ArtifactKind
from vidsprod.errors import CapabilityUnavailableError, GenerationTimeoutError, UIMismatchError
from vidsprod.vids.port import EvidenceFile

from .browser_manager import safe_url

# The locator keys each recipe needs. Recon fills these in `recon/locators.json`.
_CONFIGURE = [
    "generate.open_panel", "generate.panel", "generate.create", "generate.expand",
    "generate.prompt", "generate.settings", "generate.duration", "generate.aspect.16:9",
    "generate.aspect.9:16",
]
_TIMELINE = ["timeline.scene_group", "timeline.scene", "timeline.scene_duration",
             "timeline.scene_active", "timeline.duration"]
_FORMAT = ["menu.format", "menu.format.options", "format.size_section", "format.position_section",
           "format.width", "format.height", "format.x", "format.y"]
REQUIRED_LOCATORS: dict[str, list[str]] = {
    "project.create": ["project.new", "editor.ready", "project.title",
                       "editor.getting_started_close"],
    "project.open": ["project.title", "editor.ready"],
    "clip.configure": _CONFIGURE,
    "clip.ingredients": ["generate.ingredients", "generate.ingredients_more", "generate.ingredient_remove"],
    # Error/refusal/quota landmarks are used when recorded; they have not been observed yet.
    "clip.generate": [*_CONFIGURE, "generate.submit", "generate.result", "generate.in_progress"],
    "clip.capture_frames": ["generate.result"],
    "clip.edit_text": ["generate.result.edit", "edit.instruction", "edit.submit"],
    "clip.insert": ["generate.result", "generate.result.insert", *_TIMELINE, "canvas.video"],
    "timeline.read": _TIMELINE,
    "timeline.text_overlay": [*_TIMELINE, "text.insert", "text.add_title", "text.add_body",
                              "text.canvas_text", "text.selection", *_FORMAT],
    "timeline.image": [*_TIMELINE, "menu.insert", "menu.insert.upload", "canvas.image", *_FORMAT],
    "timeline.layout_video": [*_TIMELINE, "canvas.video", *_FORMAT],
    "timeline.add_scene": [*_TIMELINE, "timeline.new_scene", "timeline.new_scene_empty", "canvas.video"],
    "export.download_mp4": ["menu.file", "menu.file.download", "menu.file.download_mp4"],
}


class LocatorSpec(BaseModel):
    strategy: Literal["role", "label", "placeholder", "text", "testid", "css"]
    value: str
    role: str | None = None  # for strategy=role
    exact: bool = True
    within: str | None = None  # key of a parent locator
    verified_on: date | None = None
    notes: str = ""


class LocatorCatalogue(BaseModel):
    ui_fingerprint: str | None = None
    fingerprint_landmarks: list[str] = Field(default_factory=list)
    locators: dict[str, LocatorSpec] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> LocatorCatalogue:
        if not path.exists():
            return cls()
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def missing_for(self, capability: str) -> list[str]:
        return [key for key in REQUIRED_LOCATORS.get(capability, []) if key not in self.locators]


class PageDriver:
    def __init__(self, page: Any, catalogue: LocatorCatalogue, evidence_dir: Path, *,
                 timeout_ms: int, min_interval_ms: int) -> None:
        self.page = page
        self.catalogue = catalogue
        self.evidence_dir = evidence_dir
        self.timeout_ms = timeout_ms
        self.min_interval = min_interval_ms / 1000
        self._last_action = 0.0
        self.evidence: list[EvidenceFile] = []
        # Set once an irreversible control (Generate, Insert, Download) has been clicked.
        # Until then any failure means the action verifiably did not happen.
        self.irreversible_done = False
        self.trace: list[dict[str, Any]] = []  # action / expected / observed, for reports

    def locator(self, key: str, root: Any = None) -> Any:
        """`root` scopes the lookup to a parent locator (e.g. one timeline scene group)."""
        spec = self.catalogue.locators.get(key)
        if spec is None:
            raise CapabilityUnavailableError(
                f"no verified locator for '{key}'; run recon to record it", details={"locator": key})
        if root is None:
            root = self.locator(spec.within) if spec.within else self.page
        match spec.strategy:
            case "role":
                return root.get_by_role(spec.role or "button", name=spec.value, exact=spec.exact)
            case "label":
                return root.get_by_label(spec.value, exact=spec.exact)
            case "placeholder":
                return root.get_by_placeholder(spec.value, exact=spec.exact)
            case "text":
                return root.get_by_text(spec.value, exact=spec.exact)
            case "testid":
                return root.get_by_test_id(spec.value)
            case _:
                return root.locator(spec.value)

    def _record(self, action: str, expected: str, observed: str, ok: bool) -> None:
        self.trace.append({"action": action, "expected": expected, "observed": observed,
                           "ok": ok, "at": time.time()})

    async def _pace(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_action)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_action = time.monotonic()

    async def screenshot(self, label: str) -> EvidenceFile:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)[:60]
        path = self.evidence_dir / f"{int(time.time() * 1000)}_{safe}.png"
        await self.page.screenshot(path=str(path), full_page=False)
        file = EvidenceFile(path=path, kind=ArtifactKind.SCREENSHOT, mime="image/png", label=label)
        self.evidence.append(file)
        return file

    async def mismatch(self, message: str, key: str | None = None) -> UIMismatchError:
        shot = await self.screenshot(f"mismatch_{key or 'page'}")
        self._record(f"inspect {key or 'page'}", "expected UI state", message, False)
        return UIMismatchError(message, details={
            "locator": key, "screenshot": str(shot.path), "page": safe_url(self.page.url),
            "before_action": not self.irreversible_done, "trace": self.trace[-10:],
        })

    async def count(self, key: str) -> int:
        return await self.locator(key).count()

    async def is_present(self, key: str) -> bool:
        return await self.count(key) > 0

    async def expect_one(self, key: str, timeout_ms: int | None = None) -> Any:
        """Wait for exactly one match. Zero or several is a UI mismatch, never a guess."""
        loc = self.locator(key)
        deadline = time.monotonic() + (timeout_ms or self.timeout_ms) / 1000
        while True:
            n = await loc.count()
            if n == 1:
                return loc
            if n > 1:
                raise await self.mismatch(f"'{key}' matches {n} elements; expected exactly one", key)
            if time.monotonic() >= deadline:
                raise await self.mismatch(f"'{key}' did not appear", key)
            await asyncio.sleep(0.2)

    async def click(self, key: str, *, irreversible: bool = False) -> None:
        await self._pace()
        loc = await self.expect_one(key)
        if irreversible:
            self.irreversible_done = True
        await loc.click(timeout=self.timeout_ms)
        self._record(f"click {key}", "clicked", "clicked", True)

    async def fill_verified(self, key: str, text: str) -> None:
        """Fill, then read back: what was submitted must be exactly what was intended."""
        await self._pace()
        loc = await self.expect_one(key)
        await loc.fill(text, timeout=self.timeout_ms)
        actual = await loc.input_value(timeout=self.timeout_ms)
        ok = actual == text
        self._record(f"fill {key}", f"{len(text)} characters", f"{len(actual)} characters", ok)
        if not ok:
            raise await self.mismatch(
                f"'{key}' holds {len(actual)} characters after filling {len(text)}; the field "
                "truncated or altered the text", key)

    async def select_verified(self, key: str, value: str) -> None:
        await self._pace()
        loc = await self.expect_one(key)
        await loc.select_option(value, timeout=self.timeout_ms)
        actual = await loc.input_value(timeout=self.timeout_ms)
        self._record(f"select {key}", value, actual, actual == value)
        if actual != value:
            raise await self.mismatch(f"'{key}' did not take the value {value!r}", key)

    async def type_verified(self, key: str, text: str) -> None:
        """For rich-text (contenteditable) fields: focus, clear, type, read the text back.
        Never presses Enter (the Vids prompt submits on Enter)."""
        if "\n" in text:
            raise ValueError("newlines are not typed: Enter may submit")
        await self._pace()
        loc = await self.expect_one(key)
        await loc.focus()
        if (await loc.inner_text(timeout=self.timeout_ms)).strip():  # only clear existing text:
            # Backspace in an empty composer can remove an attached image (chat-style)
            await self.page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
            await self.page.keyboard.press("Backspace")
        await self.page.keyboard.type(text, delay=15)
        actual = " ".join((await loc.inner_text(timeout=self.timeout_ms)).split())
        ok = actual == " ".join(text.split())
        self._record(f"type {key}", f"{len(text)} characters", f"{len(actual)} characters", ok)
        if not ok:
            raise await self.mismatch(f"'{key}' reads back different text after typing", key)

    async def append_verified(self, key: str, text: str) -> None:
        """Type after what a rich-text field already holds (e.g. an ingredient chip), without
        clearing it: focus, caret to the end, type, then the field must end with the text.
        Never presses Enter or Backspace."""
        if "\n" in text:
            raise ValueError("newlines are not typed: Enter may submit")
        await self._pace()
        loc = await self.expect_one(key)
        await loc.focus()
        if not await loc.evaluate("el => el === document.activeElement || el.contains(document.activeElement)"):
            raise await self.mismatch(f"'{key}' did not take the keyboard focus; nothing was typed", key)
        await self.page.keyboard.press("End")
        await self.page.keyboard.type(text, delay=15)
        actual = " ".join((await loc.inner_text(timeout=self.timeout_ms)).replace("\u200b", " ").split())
        ok = actual.endswith(" ".join(text.split()))
        self._record(f"append {key}", f"{len(text)} characters", f"{len(actual)} characters", ok)
        if not ok:
            raise await self.mismatch(f"'{key}' does not end with the typed text", key)

    async def click_if_present(self, key: str) -> bool:
        """Dismiss optional UI (first-run dialogs, a collapsed composer)."""
        if key in self.catalogue.locators and await self.count(key) == 1:
            await self.click(key)
            return True
        return False

    async def press_key(self, key: str, *, allow_enter: bool = False) -> None:
        if key in ("Enter", "NumpadEnter") and not allow_enter:
            raise ValueError("Enter is only pressed where a recipe explicitly allows it")
        await self._pace()
        await self.page.keyboard.press(key)

    async def upload_verified(self, key: str, path: Path) -> None:
        await self._pace()
        loc = await self.expect_one(key)
        await loc.set_input_files(str(path), timeout=self.timeout_ms)
        self._record(f"upload {key}", path.name, "file set", True)

    async def read_text(self, key: str) -> str:
        loc = await self.expect_one(key)
        return (await loc.inner_text(timeout=self.timeout_ms)).strip()

    async def wait_for_count(self, key: str, target: int, timeout_s: float, *,
                             fail_keys: dict[str, type[Exception]]) -> None:
        """Wait until `key` has `target` matches; stop early on any failure landmark."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for fail_key, exc in fail_keys.items():
                if fail_key in self.catalogue.locators and await self.is_present(fail_key):
                    shot = await self.screenshot(f"failure_{fail_key}")
                    self._record(f"wait {key}", f"{target} present", f"'{fail_key}' shown", False)
                    raise exc(f"Vids reported '{fail_key}'", details={"screenshot": str(shot.path)})
            if await self.count(key) >= target:
                self._record(f"wait {key}", f"{target} present", f"{target} present", True)
                return
            await asyncio.sleep(2.0)
        shot = await self.screenshot(f"timeout_{key}")
        raise GenerationTimeoutError(f"'{key}' did not reach {target} within {timeout_s:.0f}s",
                                     details={"screenshot": str(shot.path)})

    async def fingerprint(self, *, expected: str | None = None, settle_s: float = 15) -> str:
        """A hash of which landmark elements exist. It changes when the editor's structure
        changes, which is when verified recipes stop being safe. The home page renders its
        landmarks over several seconds (observed: a check taken mid-load read as a changed
        UI), so with `expected` it polls until that value appears or `settle_s` passes. It
        can only match when the landmarks are really there."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (settle_s if expected else 0)
        while True:
            value = await self._fingerprint_once()
            if value == expected or loop.time() >= deadline:
                return value
            await asyncio.sleep(0.5)

    async def _fingerprint_once(self) -> str:
        parts = []
        for key in self.catalogue.fingerprint_landmarks:
            try:
                n = await self.count(key)
            except CapabilityUnavailableError:
                n = -1
            parts.append(f"{key}={n}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
