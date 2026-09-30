"""The Google Vids browser adapter, exercised with a real (headless) Chromium against local
fixture pages.

The fixtures are synthetic harnesses, not Google Vids, and the locator catalogue used here
is the test's own: these tests prove the adapter's mechanics (gates, verification,
mismatch handling, evidence, sign-in detection, recovery), not anything about the real
Vids UI. Nothing here contacts Google.
"""

from __future__ import annotations

import functools
import http.server
import os
import stat
import threading
from datetime import date

import pytest

from tests.conftest import make_settings
from vidsprod.automation.google_vids import (
    BrowserSessionManager,
    BrowserVidsAdapter,
    LocatorCatalogue,
    LocatorSpec,
    PageDriver,
    authentication,
    diagnostics,
    raise_if_wrong,
)
from vidsprod.automation.google_vids.adapter import canvas_has_text
from vidsprod.automation.google_vids.recovery import GoogleChallengeError
from vidsprod.domain.enums import AspectRatio, CapabilityStatus
from vidsprod.errors import (
    CapabilityUnavailableError,
    LiveAutomationDisabledError,
    SessionExpiredError,
    UIMismatchError,
    UnsavedClipError,
    VidsError,
    WrongAccountError,
)
from vidsprod.vids.capabilities import Capability, CapabilityRegistry, live_registry
from vidsprod.vids.port import ClipRequest, ProjectHandle, SessionStateName

FIXTURES = __import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures" / "browser"
ACCOUNT = "vids.tester@example.com"


@pytest.fixture(scope="module")
def server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _settings(tmp_path, base, page="signed_in.html", **kw):
    values = dict(google_vids_mode="browser_test", google_vids_allowed_account=ACCOUNT,
                  google_vids_url=f"{base}/{page}", browser_channel="", browser_headless=True,
                  browser_min_action_interval_ms=0, browser_action_timeout_ms=1500)
    values.update(kw)
    return make_settings(tmp_path, **values)


@pytest.fixture
async def manager_factory():
    managers = []

    def make(settings):
        m = BrowserSessionManager(settings)
        managers.append(m)
        return m

    yield make
    for m in managers:
        await m.close()


# ------------------------------------------------------------------ gates
async def test_mock_mode_never_launches_a_browser(tmp_path, server):
    manager = BrowserSessionManager(make_settings(tmp_path))
    with pytest.raises(LiveAutomationDisabledError, match="mock"):
        await manager.launch()
    assert not manager.running


async def test_browser_test_mode_refuses_every_action_that_changes_anything(tmp_path, server, manager_factory):
    settings = _settings(tmp_path, server, "harness.html")
    adapter = BrowserVidsAdapter(settings, live_registry(tmp_path / "none.json"),
                                 manager=manager_factory(settings))
    state = await adapter.check_session(tmp_path / "ev")
    assert state.state is SessionStateName.SIGNED_IN
    project = ProjectHandle(project_id="p", title="[vidsprod] x", url=settings.google_vids_url)
    request = ClipRequest(scene_key="S01", take_no=1, prompt="p", duration_seconds=5,
                          aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k")
    for action in (lambda: adapter.create_project("[vidsprod] x"),
                   lambda: adapter.generate_scene(project, request),
                   lambda: adapter.upload_reference(project, FIXTURES / "harness.html", "r"),
                   lambda: adapter.export_video(project, None, tmp_path)):
        with pytest.raises(LiveAutomationDisabledError, match="GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED"):
            await action()
    page = await adapter.manager.page()
    assert await page.evaluate("window.__clicks") == 0, "nothing was clicked"


async def test_live_mode_still_refuses_unverified_capabilities_before_touching_the_page(
        tmp_path, server, manager_factory):
    settings = _settings(tmp_path, server, "harness.html", google_vids_mode="live",
                         google_vids_live_automation_enabled=True)
    adapter = BrowserVidsAdapter(settings, live_registry(tmp_path / "none.json"),
                                 manager=manager_factory(settings))
    await adapter.check_session(tmp_path / "ev")
    project = ProjectHandle(project_id="p", title="[vidsprod] x", url=settings.google_vids_url)
    request = ClipRequest(scene_key="S01", take_no=1, prompt="p", duration_seconds=5,
                          aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k")
    with pytest.raises(CapabilityUnavailableError, match="UNVERIFIED"):
        await adapter.generate_scene(project, request)
    assert await (await adapter.manager.page()).evaluate("window.__clicks") == 0


def test_recon_cannot_claim_verified_without_evidence(tmp_path):
    recon = tmp_path / "capabilities.json"
    recon.write_text('{"capabilities": [{"key": "clip.generate", "status": "VERIFIED"}]}')
    assert live_registry(recon).get("clip.generate").status is CapabilityStatus.UNVERIFIED


# ------------------------------------------------------------------ sign-in detection
@pytest.mark.parametrize("page,expected", [
    ("signed_in.html", SessionStateName.SIGNED_IN),
    ("other_account.html", SessionStateName.WRONG_ACCOUNT),
    ("no_account.html", SessionStateName.ACCOUNT_UNCONFIRMED),
    ("challenge.html", SessionStateName.CHALLENGE),
])
async def test_sign_in_state_is_read_from_the_page(tmp_path, server, manager_factory, page, expected):
    settings = _settings(tmp_path, server, page)
    manager = manager_factory(settings)
    state = await authentication.open_login(manager)
    assert state.state is expected, state.message
    if expected is SessionStateName.SIGNED_IN:
        assert state.account_confirmed and state.account_label == "vi***@example.com"
        assert ACCOUNT not in state.model_dump_json(), "the full address is never echoed"


async def test_a_page_on_another_host_counts_as_signed_out(tmp_path, server, manager_factory):
    settings = _settings(tmp_path, server)
    manager = manager_factory(settings)
    page = await manager.launch()
    await page.goto(server.replace("127.0.0.1", "localhost") + "/signed_in.html")
    state = await authentication.detect(page, settings)
    assert state.state is SessionStateName.SIGNED_OUT


@pytest.mark.parametrize("state,error", [
    (SessionStateName.SIGNED_OUT, SessionExpiredError),
    (SessionStateName.WRONG_ACCOUNT, WrongAccountError),
    (SessionStateName.ACCOUNT_UNCONFIRMED, WrongAccountError),
    (SessionStateName.CHALLENGE, GoogleChallengeError),
    (SessionStateName.UI_CHANGED, UIMismatchError),
    (SessionStateName.SIGNED_IN, CapabilityUnavailableError),
])
def test_each_session_state_maps_to_the_right_recovery(tmp_path, state, error):
    from vidsprod.vids.port import SessionState

    with pytest.raises(error):
        raise_if_wrong(SessionState(state=state, message="m"),
                       make_settings(tmp_path, google_vids_mode="live",
                                     google_vids_allowed_account=ACCOUNT))


def test_browser_test_mode_reports_success_as_disabled_production(tmp_path):
    from vidsprod.vids.port import SessionState

    with pytest.raises(LiveAutomationDisabledError, match="BROWSER_TEST"):
        raise_if_wrong(SessionState(state=SessionStateName.SIGNED_IN, message="Signed in"),
                       make_settings(tmp_path, google_vids_mode="browser_test",
                                     google_vids_allowed_account=ACCOUNT))


# ------------------------------------------------------------------ page driver mechanics
CATALOGUE = LocatorCatalogue(
    ui_fingerprint=None,
    fingerprint_landmarks=["open"],
    locators={
        "open": LocatorSpec(strategy="role", role="button", value="Open generator"),
        "create": LocatorSpec(strategy="role", role="button", value="Create"),
        "prompt": LocatorSpec(strategy="label", value="Prompt"),
        "short": LocatorSpec(strategy="label", value="Short prompt"),
        "duration": LocatorSpec(strategy="label", value="Duration"),
        "dup": LocatorSpec(strategy="role", role="button", value="Duplicate"),
        "missing": LocatorSpec(strategy="role", role="button", value="Does not exist"),
    },
)


async def _driver(tmp_path, server, manager_factory):
    settings = _settings(tmp_path, server, "harness.html")
    manager = manager_factory(settings)
    page = await manager.open_home()
    return PageDriver(page, CATALOGUE, tmp_path / "evidence", timeout_ms=800, min_interval_ms=0)


async def test_clicks_and_fills_are_verified_and_traced(tmp_path, server, manager_factory):
    d = await _driver(tmp_path, server, manager_factory)
    await d.click("open")
    text = "A family in a bright kitchen. Camera: slow push in. " * 5
    await d.fill_verified("prompt", text)
    await d.select_verified("duration", "7")
    assert [t["ok"] for t in d.trace] == [True, True, True]
    assert d.trace[1]["expected"] == f"{len(text)} characters"


async def test_a_field_that_truncates_the_prompt_stops_the_action(tmp_path, server, manager_factory):
    d = await _driver(tmp_path, server, manager_factory)
    await d.click("open")
    with pytest.raises(UIMismatchError, match="truncated") as raised:
        await d.fill_verified("short", "this prompt is longer than ten characters")
    assert raised.value.details["before_action"] is True
    assert os.path.exists(raised.value.details["screenshot"])


async def test_missing_and_ambiguous_elements_are_never_guessed(tmp_path, server, manager_factory):
    d = await _driver(tmp_path, server, manager_factory)
    with pytest.raises(UIMismatchError, match="did not appear"):
        await d.click("missing")
    with pytest.raises(UIMismatchError, match="matches 2 elements"):
        await d.click("dup")
    with pytest.raises(CapabilityUnavailableError, match="no verified locator"):
        d.locator("generate.submit")
    assert len(d.evidence) == 2, "each mismatch left a screenshot"


# ------------------------------------------------------------------ a full recipe on the harness
VIDS_LIKE = LocatorCatalogue(
    fingerprint_landmarks=["generate.open_panel"],
    locators={
        "generate.open_panel": LocatorSpec(strategy="role", role="button", value="Generate an AI video clip"),
        "generate.panel": LocatorSpec(strategy="role", role="heading", value="AI video clip"),
        "generate.create": LocatorSpec(strategy="role", role="tab", value="Create"),
        "generate.expand": LocatorSpec(strategy="role", role="button", value="Expand"),
        "generate.prompt": LocatorSpec(strategy="css", value="[role=textbox][enterkeyhint=send]"),
        "generate.settings": LocatorSpec(strategy="css", value='[aria-label^="Generation settings"]'),
        "generate.duration": LocatorSpec(strategy="label", value="Video duration"),
        "generate.aspect.16:9": LocatorSpec(strategy="role", role="radio", value="Landscape"),
        "generate.aspect.9:16": LocatorSpec(strategy="role", role="radio", value="Portrait"),
        "generate.submit": LocatorSpec(strategy="role", role="button", value="Generate"),
        "generate.ingredients": LocatorSpec(strategy="role", role="button", value="Ingredients"),
        "generate.ingredients_more": LocatorSpec(strategy="role", role="button", value="Add avatar or ingredients"),
        "generate.ingredient_remove": LocatorSpec(strategy="role", role="button", value="Remove image"),
        "generate.result": LocatorSpec(strategy="css", value='video[aria-label^="Generated video:"]'),
        "generate.result.insert": LocatorSpec(strategy="role", role="button", value="Insert"),
        "timeline.scene": LocatorSpec(strategy="css", value='[aria-label^="Scene"]'),
        "timeline.duration": LocatorSpec(strategy="css", value=".appsFlixTimelinePlayTimeDuration"),
        "timeline.scene_group": LocatorSpec(strategy="css", value='.punch-filmstrip-thumbnail:has([aria-label^="Scene"])'),
        "timeline.scene_duration": LocatorSpec(strategy="css", value=".appsFlixTimelineSceneDurationLabelText"),
        "timeline.scene_active": LocatorSpec(strategy="css", value='[aria-label^="Scene"].appsFlixTimelineSceneItemActiveOuterBorder'),
        "timeline.new_scene": LocatorSpec(strategy="css", value='.docs-material-button[aria-label="New scene (Ctrl+M)"]'),
        "timeline.new_scene_empty": LocatorSpec(strategy="css", value=".appsFlixPluginsNewSceneButton"),
        "canvas.video": LocatorSpec(strategy="css", value="video:not([aria-label]):visible"),
        "editor.ready": LocatorSpec(strategy="role", role="button", value="Generate an AI video clip"),
        "menu.file": LocatorSpec(strategy="role", role="menuitem", value="File"),
        "menu.file.download": LocatorSpec(strategy="css", value='[role=menu] [role=menuitem]:visible:has-text("Download")'),
        "menu.file.download_mp4": LocatorSpec(strategy="css", value='[role=menu] [role=menuitem]:visible:has-text("MP4 video (.mp4)")'),
        "text.insert": LocatorSpec(strategy="role", role="button", value="Insert text"),
        "menu.insert": LocatorSpec(strategy="role", role="menuitem", value="Insert"),
        "menu.insert.upload": LocatorSpec(strategy="css", value='[role=menu] [role=menuitem]:visible:has-text("Upload")'),
        "canvas.image": LocatorSpec(strategy="css", value='g[id^="editor-"] image:visible'),
        "menu.format": LocatorSpec(strategy="role", role="menuitem", value="Format"),
        "menu.format.options": LocatorSpec(strategy="css", value='[role=menu] [role=menuitem]:visible:has-text("Format options")'),
        "format.size_section": LocatorSpec(strategy="role", role="button", value="Rotation", exact=False),
        "format.position_section": LocatorSpec(strategy="role", role="button", value="Position"),
        "format.width": LocatorSpec(strategy="css", value='input[aria-label^="Width, measured in pixels"]'),
        "format.height": LocatorSpec(strategy="css", value='input[aria-label^="Height, measured in pixels"]'),
        "format.x": LocatorSpec(strategy="css", value='input[aria-label^="X-position, measured in pixels"]'),
        "format.y": LocatorSpec(strategy="css", value='input[aria-label^="Y-position, measured in pixels"]'),
        "text.add_title": LocatorSpec(strategy="text", value="Add a title"),
        "text.add_body": LocatorSpec(strategy="text", value="Add body text"),
        "text.canvas_text": LocatorSpec(strategy="css", value='g[id^="editor-"][id*="-paragraph-"] text:visible'),
        "text.selection": LocatorSpec(strategy="css", value="rect.sketchy-text-selection-overlay:visible"),
        "generate.in_progress": LocatorSpec(strategy="css", value=".never-shown"),
        "generate.error": LocatorSpec(strategy="css", value=".error-banner"),
        "generate.refused": LocatorSpec(strategy="css", value=".refused-banner"),
        "generate.quota": LocatorSpec(strategy="css", value=".quota-banner"),
    },
)


GENERATE = ("clip.generate", "clip.insert", "timeline.add_scene")


async def _vids_like_adapter(tmp_path, server, manager_factory, verified: tuple[str, ...],
                             page: str = "vids_like.html"):
    settings = _settings(tmp_path, server, page, google_vids_mode="live",
                         google_vids_live_automation_enabled=True)
    catalogue = VIDS_LIKE.model_copy(deep=True)
    manager = manager_factory(settings)
    fingerprint = await PageDriver(await manager.open_home(), catalogue, tmp_path,
                                   timeout_ms=800, min_interval_ms=0).fingerprint()
    catalogue.ui_fingerprint = fingerprint
    registry = CapabilityRegistry([Capability(
        key=key, description="harness", status=CapabilityStatus.VERIFIED, source="observed",
        verified_on=date.today(), ui_fingerprint=fingerprint, evidence=["test"],
        params={"min_seconds": 3, "max_seconds": 10}) for key in verified], simulated=False)
    adapter = BrowserVidsAdapter(settings, registry, catalogue, manager=manager)
    state = await adapter.check_session(tmp_path / "ev")
    assert state.state is SessionStateName.READY, state.message
    return adapter, ProjectHandle(project_id="p", title="[vidsprod] x", url=settings.google_vids_url)


async def test_configure_scene_types_the_prompt_and_reads_back_the_settings(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("clip.configure",))
    request = ClipRequest(scene_key="S01", take_no=1, prompt="A calm family kitchen, morning light",
                          duration_seconds=6, aspect_ratio=AspectRatio.PORTRAIT, idempotency_key="k")
    label = await adapter.configure_scene(project, request)
    page = await adapter.manager.page()
    assert label.endswith("Portrait, 6 seconds")
    assert (await page.inner_text("[role=textbox]")).strip() == request.prompt
    assert await page.evaluate("window.__submitted") == 0, "configuring never submits"


async def test_configure_is_not_generate(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("clip.configure",))
    request = ClipRequest(scene_key="S01", take_no=1, prompt="p", duration_seconds=5,
                          aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k")
    with pytest.raises(CapabilityUnavailableError, match="clip.generate"):
        await adapter.generate_scene(project, request)


async def test_the_generation_recipe_verifies_each_step_on_a_synthetic_page(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, GENERATE)
    request = ClipRequest(scene_key="S01", take_no=1, prompt="A calm sunlit family kitchen",
                          duration_seconds=6, aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k1")
    clip = await adapter.generate_scene(project, request)
    page = await adapter.manager.page()
    assert clip.locator["result_index"] == 0
    assert clip.locator["label"].startswith("Generated video:")
    assert clip.locator["inserted_scene"] == "Scene 1 of 1", "Insert fills the selected scene (observed)"
    assert clip.locator["timeline_before"] == {"scenes": ["Scene 1 of 1"], "durations": [5.0],
                                               "active": 0, "total_seconds": 5.0}
    after = clip.locator["timeline_after"]
    assert after["durations"] == [6.0] and after["total_seconds"] == 6.0
    assert abs(after["canvas_video_seconds"] - 6.0) < 0.1
    assert await page.evaluate("window.__newScenes") == 0, "a new Vid's blank scene is used as it is"
    assert len(clip.locator["frames"]) == 3 and all(os.path.exists(f) for f in clip.locator["frames"])
    assert await page.evaluate("window.__submitted") == 1 and await page.evaluate("window.__inserted") == 1


async def test_generation_is_refused_unless_insert_is_verified(tmp_path, server, manager_factory):
    """A generated clip lives only in the open page (observed): never spend quota on one
    that cannot be inserted."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("clip.generate",))
    request = ClipRequest(scene_key="S01", take_no=1, prompt="p", duration_seconds=6,
                          aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k2")
    with pytest.raises(CapabilityUnavailableError, match="clip.insert"):
        await adapter.generate_scene(project, request)
    page = await adapter.manager.page()
    assert await page.evaluate("window.__submitted") == 0


async def test_text_overlay_selects_the_default_word_then_types(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.text_overlay",))
    rendered = await adapter.add_text_overlay(project, "vidsprod adapter text")
    page = await adapter.manager.page()
    assert rendered == ["Vidsprod adapter text"], "the default 'Title' was replaced, not appended to"
    assert await page.evaluate("window.__strayKeys") == 0


async def test_body_text_is_selected_whole_then_replaced(tmp_path, server, manager_factory):
    """Observed: 'Body text' is two words; a triple-click selects both; typing replaces them."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.text_overlay",))
    text = "Health insurance is about more than managing medical expenses."
    rendered = await adapter.add_text_overlay(project, text, kind="body")
    assert rendered == text.split()
    assert await (await adapter.manager.page()).evaluate("window.__strayKeys") == 0


async def test_two_boxes_in_a_row_keep_the_text_panel_open(tmp_path, server, manager_factory):
    """Observed: the 'Insert text' rail button toggles the panel, which stays open after a box."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.text_overlay",))
    await adapter.add_text_overlay(project, "first line", kind="body")
    rendered = await adapter.add_text_overlay(project, "second title")
    assert canvas_has_text(rendered, "second title") and canvas_has_text(rendered, "first line")


async def test_no_key_is_sent_unless_the_title_text_is_selected(tmp_path, server, manager_factory):
    """Observed: keys outside text editing reach the timeline (scene selection, deletion)."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.text_overlay",),
                                                page="vids_like.html?text=noselect")
    with pytest.raises(UIMismatchError, match="no keys were sent"):
        await adapter.add_text_overlay(project, "hello")
    page = await adapter.manager.page()
    assert await page.evaluate("window.__strayKeys") == 0


async def test_a_highlight_away_from_the_word_is_not_a_selection(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.text_overlay",),
                                                page="vids_like.html?text=elsewhere")
    with pytest.raises(UIMismatchError, match="no keys were sent"):
        await adapter.add_text_overlay(project, "hello")
    assert await (await adapter.manager.page()).evaluate("window.__strayKeys") == 0


def test_overlap_is_the_share_of_the_word_covered():
    from vidsprod.automation.google_vids.adapter import _overlap
    word = {"x": 10, "y": 10, "width": 100, "height": 40}
    assert _overlap(word, word) == 1.0
    assert _overlap({"x": 500, "y": 10, "width": 100, "height": 40}, word) == 0.0
    assert 0.45 < _overlap({"x": 60, "y": 10, "width": 100, "height": 40}, word) < 0.55


def test_overlay_text_is_one_printable_line():
    adapter = BrowserVidsAdapter.__new__(BrowserVidsAdapter)  # validation happens before any browser use
    with pytest.raises(ValueError, match="one non-empty line"):
        __import__("asyncio").run(adapter.add_text_overlay(None, "two\nlines"))


def test_the_rich_text_prompt_never_receives_a_newline():
    driver = PageDriver(None, VIDS_LIKE, __import__("pathlib").Path("."), timeout_ms=1, min_interval_ms=0)
    with pytest.raises(ValueError, match="Enter may submit"):
        __import__("asyncio").run(driver.type_verified("generate.prompt", "line one\nline two"))


async def test_a_changed_ui_demotes_verified_capabilities(tmp_path, server, manager_factory):
    settings = _settings(tmp_path, server, "harness.html", google_vids_mode="live",
                         google_vids_live_automation_enabled=True)
    catalogue = LocatorCatalogue(ui_fingerprint="fingerprint-from-another-ui",
                                 fingerprint_landmarks=["open"], locators=dict(CATALOGUE.locators))
    registry = CapabilityRegistry([Capability(
        key="clip.generate", description="h", status=CapabilityStatus.VERIFIED, source="observed",
        ui_fingerprint="fingerprint-from-another-ui", evidence=["x"])], simulated=False)
    adapter = BrowserVidsAdapter(settings, registry, catalogue, manager=manager_factory(settings))
    state = await adapter.check_session(tmp_path / "ev")
    assert state.state is SessionStateName.UI_CHANGED
    assert registry.get("clip.generate").status is CapabilityStatus.UNVERIFIED


# ------------------------------------------------------------------ diagnostics and recovery
async def test_diagnostics_lists_candidate_locators_without_touching_anything(tmp_path, server, manager_factory):
    settings = _settings(tmp_path, server, "harness.html")
    manager = manager_factory(settings)
    page = await manager.open_home()
    report = await diagnostics.diagnose(page, tmp_path / "diag")
    names = {(c["role"], c["value"]): c for c in report["candidate_locators"]}
    assert names[("button", "Open generator")]["usable"] is True
    assert names[("button", "Duplicate")]["usable"] is False, "ambiguous names are flagged"
    assert os.path.exists(report["screenshot"]) and os.path.exists(report["aria_snapshot"])
    assert await page.evaluate("window.__clicks") == 0


async def test_a_crashed_browser_is_relaunched_and_the_profile_is_private(tmp_path, server, manager_factory):
    settings = _settings(tmp_path, server)
    manager = manager_factory(settings)
    page = await manager.open_home()
    await page.context.close()  # the browser went away
    assert not manager.running
    again = await manager.page()
    assert manager.running and again is not page
    assert stat.S_IMODE(os.stat(settings.browser_profile_dir).st_mode) == 0o700


def test_the_real_vids_app_location_is_recognised(tmp_path):
    """Observed on the live site: a signed-in profile lands on docs.google.com/videos/u/0/."""
    s = make_settings(tmp_path)
    assert authentication.is_vids_page("https://docs.google.com/videos/u/0/", s)
    assert authentication.is_vids_page("https://vids.google.com/", s)
    assert not authentication.is_vids_page("https://docs.google.com/document/d/x", s)
    assert not authentication.is_vids_page("https://accounts.google.com/v3/signin/identifier", s)


def test_canvas_text_check_requires_whole_words():
    """Regression: 'Titlevidsprod adapter text' was accepted as 'vidsprod adapter text'."""
    from vidsprod.automation.google_vids.adapter import canvas_has_text

    assert not canvas_has_text(["1", "Titlevidsprod", "adapter", "text"], "vidsprod adapter text")
    assert canvas_has_text(["1", "Vidsprod", "adapter", "text", "5.0"], "vidsprod adapter text")
    assert not canvas_has_text(["Vidsprod", "adapter"], "vidsprod adapter text")


def test_safe_url_keeps_only_scheme_host_and_path():
    from vidsprod.automation.google_vids.browser_manager import safe_url
    assert safe_url("about:blank") == "about:blank"
    assert safe_url("https://docs.google.com/videos/d/x/edit?usp=a#h") == "https://docs.google.com/videos/d/x/edit"


def test_timecodes_parse():
    from vidsprod.automation.google_vids.adapter import parse_timecode
    assert parse_timecode("00:00.0") == 0.0
    assert parse_timecode("00:06.0") == 6.0
    assert parse_timecode("1:02:03.5") == 3723.5


async def test_a_disabled_ai_video_button_stops_before_any_click(tmp_path, server, manager_factory):
    """Observed: 'Generate an AI video clip' is aria-disabled in a Vid with no scene."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("clip.configure",))
    page = await adapter.manager.page()
    await page.evaluate("document.querySelector('[aria-label=\\'Generate an AI video clip\\']').setAttribute('aria-disabled', 'true')")
    request = ClipRequest(scene_key="S01", take_no=1, prompt="p", duration_seconds=6,
                          aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k")
    with pytest.raises(UIMismatchError, match="no scene"):
        await adapter.configure_scene(project, request)


async def test_a_blank_scene_is_added_after_an_existing_clip(tmp_path, server, manager_factory):
    """Observed: Insert fills the active scene. With a clip in the last scene, a blank scene
    is added after it first, and the clip lands there: nothing is overwritten."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, GENERATE,
                                                page="vids_like.html?state=clip")
    request = ClipRequest(scene_key="S02", take_no=1, prompt="p", duration_seconds=6,
                          aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k3")
    clip = await adapter.generate_scene(project, request)
    page = await adapter.manager.page()
    assert clip.locator["timeline_before"]["durations"] == [6.0, 5.0]
    assert clip.locator["timeline_before"]["active"] == 1
    assert clip.locator["timeline_after"]["durations"] == [6.0, 6.0]
    assert clip.locator["timeline_after"]["total_seconds"] == 12.0
    assert clip.locator["inserted_scene"] == "Scene 2 of 2"
    assert await page.evaluate("window.__newScenes") == 1 and await page.evaluate("window.__inserted") == 1


async def test_an_existing_blank_last_scene_is_selected_not_duplicated(tmp_path, server, manager_factory):
    """Observed: a reopened Vid starts on scene 1. The last scene is selected first."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.add_scene",),
                                                page="vids_like.html?state=clip_then_blank")
    tl = await adapter.add_blank_scene(project)
    page = await adapter.manager.page()
    assert tl["active"] == 1 and tl["durations"] == [6.0, 5.0]
    assert await page.evaluate("window.__newScenes") == 0


async def test_an_empty_vid_gets_its_first_scene(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.add_scene",),
                                                page="vids_like.html?state=empty")
    tl = await adapter.add_blank_scene(project)
    assert tl == {"scenes": ["Scene 1 of 1"], "durations": [5.0], "active": 0, "total_seconds": 5.0}


async def test_scene_preparation_and_timeline_read_back_without_generation(tmp_path, server, manager_factory):
    """The complete non-quota multi-scene workflow: prepare, read back, prepare again."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory,
                                                ("timeline.add_scene", "timeline.read"),
                                                page="vids_like.html?state=clip")
    first = await adapter.add_blank_scene(project)
    again = await adapter.add_blank_scene(project)
    timeline = await adapter.read_timeline(project)
    page = await adapter.manager.page()
    assert first["durations"] == [6.0, 5.0] and again == first, "a blank last scene is reused"
    assert await page.evaluate("window.__newScenes") == 1
    assert [i.duration_seconds for i in timeline.items] == [6.0, 5.0]
    assert timeline.total_duration_seconds == 11.0


async def test_a_new_scene_that_does_not_appear_is_a_mismatch(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.add_scene",),
                                                page="vids_like.html?state=clip")
    page = await adapter.manager.page()
    await page.evaluate("window.newScene = function(){}")
    with pytest.raises(UIMismatchError, match="did not add one blank scene"):
        await adapter.add_blank_scene(project)



async def test_frames_are_never_taken_from_an_unloaded_video(tmp_path, server, manager_factory):
    """Observed after reopening a Vid: the canvas video had no metadata yet (duration NaN).
    Three identical t=0 'frames' would have looked like success."""
    adapter, _ = await _vids_like_adapter(tmp_path, server, manager_factory, ())
    page = await adapter.manager.page()
    await page.evaluate("document.getElementById('canvas').appendChild(document.createElement('video'))")
    driver = PageDriver(page, adapter.catalogue, tmp_path, timeout_ms=800, min_interval_ms=0)
    with pytest.raises(VidsError, match="has not loaded"):
        await adapter._clip_frames(driver, driver.locator("canvas.video").first, tmp_path, "x")
    assert await adapter._clip_frames_bounded(driver, driver.locator("canvas.video").first, tmp_path, "x") == []


async def test_the_fingerprint_waits_for_late_landmarks(tmp_path, server, manager_factory):
    """Observed: a session check taken while the Vids home page was still rendering read as a
    changed UI. The check waits for the expected landmarks instead."""
    settings = _settings(tmp_path, server, "vids_like.html")
    page = await manager_factory(settings).open_home()
    catalogue = LocatorCatalogue(fingerprint_landmarks=["late"], locators={
        "late": LocatorSpec(strategy="css", value="#late-landmark")})
    driver = PageDriver(page, catalogue, tmp_path, timeout_ms=800, min_interval_ms=0)
    await page.evaluate("setTimeout(() => { const d = document.createElement('div'); d.id = 'late-landmark';"
                        " document.body.appendChild(d); }, 1500)")
    missing = await driver._fingerprint_once()
    await page.wait_for_timeout(2000)
    present = await driver._fingerprint_once()
    await page.evaluate("document.getElementById('late-landmark').remove(); setTimeout(() => {"
                        " const d = document.createElement('div'); d.id = 'late-landmark'; document.body.appendChild(d); }, 1500)")
    assert missing != present
    assert await driver.fingerprint(expected=present, settle_s=5) == present
    assert await driver.fingerprint(expected="never", settle_s=1) == present, "a real difference still shows"


async def test_force_new_always_appends_one_blank_scene(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.add_scene",))
    tl = await adapter.add_blank_scene(project, force_new=True)
    assert tl["durations"] == [5.0, 5.0] and tl["active"] == 1 and tl["total_seconds"] == 10.0
    page = await adapter.manager.page()
    assert not await page.evaluate("window.__toolbarNewScene || false"), "the timeline '+' is used"


async def test_a_disabled_generate_button_is_never_clicked(tmp_path, server, manager_factory):
    """Observed: Generate is natively disabled with an empty prompt."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, GENERATE)
    page = await adapter.manager.page()
    await page.evaluate("[...document.querySelectorAll('button')].find(x => x.textContent === 'Generate').disabled = true")
    request = ClipRequest(scene_key="S01", take_no=1, prompt="p", duration_seconds=6,
                          aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k9")
    with pytest.raises(UIMismatchError, match="Generate is disabled"):
        await adapter.generate_scene(project, request)
    assert await page.evaluate("window.__submitted") == 0


async def test_a_clip_that_was_not_inserted_is_never_navigated_away_from(tmp_path, server, manager_factory):
    """Observed 2026-09-29: a generated result lives only in the open page; a reload lost it.
    If Insert cannot be proven, the adapter refuses to leave or close the page."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, (*GENERATE, "project.open"))
    adapter.insert_timeout_s = 2
    page = await adapter.manager.page()
    await page.evaluate("window.insertClip = function(){ window.__inserted++; }")  # Insert changes nothing
    request = ClipRequest(scene_key="S01", take_no=1, prompt="p", duration_seconds=6,
                          aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k10")
    with pytest.raises(UIMismatchError, match="after Insert"):
        await adapter.generate_scene(project, request)
    assert adapter.unsaved_clip is not None
    for attempt in (adapter.open_project(project), adapter.check_session(tmp_path / "ev"),
                    adapter.generate_scene(project, request)):
        with pytest.raises(UnsavedClipError):
            await attempt
    await adapter.close()
    assert adapter.manager.running, "the browser stays open on the clip"
    assert await page.locator('video[aria-label^="Generated video:"]').count() == 1
    adapter.release_unsaved_clip()
    await adapter.close()
    assert not adapter.manager.running


async def test_the_export_runs_in_a_fresh_browser_and_verifies_the_file(tmp_path, server, manager_factory):
    """Observed: Chrome crashed during 'Downloading…' after a long editing session; the same
    Vid exported cleanly from a fresh browser. The adapter restarts Chrome before exporting."""
    from vidsprod.vids.port import ExportSpec
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("export.download_mp4",))
    adapter.settings = adapter.settings.model_copy(update={"export_timeout_seconds": 30})
    old_page = await adapter.manager.page()
    await old_page.evaluate("window.__longSession = true")
    exported = await __import__("asyncio").wait_for(
        adapter.export_video(project, ExportSpec(idempotency_key="export-0001"), tmp_path / "out"), 60)
    page = await adapter.manager.page()
    assert page is not old_page and not await page.evaluate("window.__longSession || false")
    check = await adapter.verify_export(exported.path)
    assert check["decode_ok"] and check["probe"]["video_codec"] == "h264"
    assert abs(check["probe"]["duration_seconds"] - 6.0) < 0.2


async def test_no_export_while_a_clip_is_not_inserted(tmp_path, server, manager_factory):
    from vidsprod.vids.port import ExportSpec
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("export.download_mp4",))
    adapter.unsaved_clip = {"scene_key": "S01"}
    with pytest.raises(UnsavedClipError):
        await adapter.export_video(project, ExportSpec(idempotency_key="export-0002"), tmp_path / "out")
    assert adapter.manager.running


async def test_a_crashing_export_is_retried_in_a_fresh_browser(tmp_path, server, manager_factory):
    """Observed: Chrome's browser process crashed during 'Downloading…' (intermittently)."""
    from vidsprod.automation.google_vids.adapter import ExportCrashedError
    from vidsprod.vids.port import ExportSpec
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("export.download_mp4",))
    real, calls = adapter._export_once, []

    async def flaky(*args):
        calls.append(1)
        if len(calls) < 3:
            raise ExportCrashedError("Chrome closed during the export download")
        return await real(*args)
    adapter._export_once = flaky
    exported = await adapter.export_video(project, ExportSpec(idempotency_key="export-0003"), tmp_path / "out")
    assert len(calls) == 3 and len(exported.details["crashed_attempts"]) == 2
    assert exported.path.exists()


async def test_a_browser_that_closes_before_the_download_is_a_crash_not_a_timeout(tmp_path, server, manager_factory):
    """Observed: Chrome can die while Vids prepares the download; that must not become a
    15-minute wait for a download that will never come."""
    import asyncio

    from vidsprod.automation.google_vids.adapter import ExportCrashedError
    from vidsprod.vids.port import ExportSpec
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("export.download_mp4",))
    adapter.settings = adapter.settings.model_copy(update={"export_timeout_seconds": 60})
    # the 'MP4' click starts nothing, then the browser goes away
    adapter.catalogue.locators["menu.file.download_mp4"] = LocatorSpec(strategy="css", value="#fileMenu [role=menuitem]")

    async def crash_after_menus():
        for _ in range(100):
            await asyncio.sleep(0.1)
            page = adapter.manager._page
            if page is not None and not page.is_closed() and "vids_like" in page.url \
                    and await page.locator("#dlMenu").is_visible():
                await asyncio.sleep(0.5)
                await page.close()
                return
    crasher = asyncio.ensure_future(crash_after_menus())
    with pytest.raises(ExportCrashedError):
        await asyncio.wait_for(adapter._export_once(project, ExportSpec(idempotency_key="export-0004"),
                                                    tmp_path / "out"), 30)
    await crasher



IMAGE = __import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures" / "browser" / "still.png"


async def test_a_still_image_is_uploaded_and_aligned_right(tmp_path, server, manager_factory):
    """Observed: Upload places the image fit to height at (32, 32); it is moved right and centred."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.image",))
    geo = await adapter.insert_image(project, IMAGE)
    assert geo == {"w": 1016.0, "h": 1016.0, "x": 872.0, "y": 32.0}
    page = await adapter.manager.page()
    assert await page.evaluate("document.querySelector('g[id^=editor-img]').__geo") == geo, "the image itself moved"
    tl = await adapter._timeline_readback(await adapter._driver(tmp_path))
    assert tl["durations"] == [5.0], "an image does not change the scene length"


async def test_body_text_can_be_laid_out_in_the_left_column(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.text_overlay",))
    await adapter.add_text_overlay(project, "Left column text", kind="body", layout={"w": 780, "x": 48})
    page = await adapter.manager.page()
    geo = await page.evaluate("document.querySelector('g[id^=editor-body]').__geo")
    assert geo["w"] == 780 and geo["x"] == 48 and abs(geo["y"] - (1080 - geo["h"]) / 2) <= 1


async def test_a_scene_clip_is_resized_into_the_right_column(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.layout_video",),
                                                page="vids_like.html?state=clip")
    geo = await adapter.place_scene_video(project, 0, w=1016, h=571.5, x=872, y=254.2)
    assert geo == {"w": 1016.0, "h": 571.5, "x": 872.0, "y": 254.2}
    page = await adapter.manager.page()
    assert await page.evaluate("document.querySelector('#canvas video').__geo") == geo


async def test_a_layout_value_that_does_not_stick_is_a_mismatch(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.image",),
                                                page="vids_like.html?format=ignore")
    with pytest.raises(UIMismatchError, match="did not take"):
        await adapter.insert_image(project, IMAGE)


def test_only_png_or_jpeg_images_are_uploaded(tmp_path):
    adapter = BrowserVidsAdapter.__new__(BrowserVidsAdapter)
    (tmp_path / "x.gif").write_bytes(b"GIF89a")
    with pytest.raises(ValueError, match="PNG or JPEG"):
        __import__("asyncio").run(adapter.insert_image(None, tmp_path / "x.gif"))


async def test_the_active_scene_is_found_by_position_not_by_its_label(tmp_path, server, manager_factory):
    """Observed: scene labels can go stale (two scenes both labelled 'Scene 2 of 2')."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.add_scene",),
                                                page="vids_like.html?state=clip_then_blank")
    page = await adapter.manager.page()
    await page.evaluate("document.querySelectorAll('[aria-label^=Scene]').forEach(e => e.setAttribute('aria-label', 'Scene 2 of 2'))")
    tl = await adapter._timeline_readback(await adapter._driver(tmp_path))
    assert tl["active"] == 0


async def test_a_result_without_metadata_is_measured_from_its_label(tmp_path, server, manager_factory):
    """Observed 2026-09-30: the result video's duration read 0 right after it appeared."""
    adapter, _ = await _vids_like_adapter(tmp_path, server, manager_factory, ())
    page = await adapter.manager.page()
    await page.evaluate("""() => { const v = document.createElement('video');
        v.setAttribute('aria-label', 'Generated video: a kitchen, 720p, 10 seconds'); document.body.appendChild(v); }""")
    card = page.locator('video[aria-label^="Generated video:"]').last
    assert await adapter._result_seconds(card) == 10.0



async def test_a_person_image_ingredient_is_attached_and_kept_through_the_prompt(tmp_path, server, manager_factory):
    """Observed: Ingredients opens a file chooser and shows 'Remove image' per attachment. The
    prompt is typed without clearing an empty composer (Backspace there can drop the image)."""
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, (*GENERATE, "clip.ingredients"))
    request = ClipRequest(scene_key="S01", take_no=1, prompt=' speaks to the camera. He says: "Hello there."',
                          duration_seconds=10, aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k20",
                          ingredient_paths=[IMAGE])
    await adapter.generate_scene(project, request)
    page = await adapter.manager.page()
    assert await page.evaluate("window.__attached") == ["still.png"]
    text = await page.evaluate("document.querySelector('#composer [role=textbox]').innerText")
    assert " ".join(text.replace("\u200b", " ").split()) == 'Image1 speaks to the camera. He says: "Hello there."', \
        "the dialogue follows the image chip, which stays"
    assert await page.evaluate("window.__submitted") == 1


async def test_a_missing_ingredient_stops_before_anything_is_typed(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, (*GENERATE, "clip.ingredients"))
    request = ClipRequest(scene_key="S01", take_no=1, prompt="p", duration_seconds=10,
                          aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k21",
                          ingredient_paths=[tmp_path / "missing.png"])
    with pytest.raises(ValueError, match="does not exist"):
        await adapter.generate_scene(project, request)
    assert await (await adapter.manager.page()).evaluate("window.__submitted") == 0



async def test_no_mention_sign_is_typed_after_image_chips(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, (*GENERATE, "clip.ingredients"))
    request = ClipRequest(scene_key="S01", take_no=1, prompt="the man in @Image1 speaks", duration_seconds=10,
                          aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="k22", ingredient_paths=[IMAGE])
    with pytest.raises(ValueError, match="do not type '@'"):
        await adapter.generate_scene(project, request)
    assert await (await adapter.manager.page()).evaluate("window.__submitted") == 0



async def test_scene_text_goes_beside_its_clip_and_reads_back_per_scene(tmp_path, server, manager_factory):
    """Written scenes: the scene's words in the left column, its clip moved to the right
    column, on screen for the whole scene; a disclosure is a bottom strip on its scene.
    Re-applying adds nothing, and one scene's text is not reported on another."""
    from vidsprod.automation.google_vids.adapter import RIGHT_COLUMN_VIDEO
    from vidsprod.domain.enums import TimelineOpKind
    from vidsprod.vids.port import TimelineOpSpec
    adapter, project = await _vids_like_adapter(
        tmp_path, server, manager_factory,
        ("timeline.text_overlay", "timeline.read", "timeline.layout_video"), page="vids_like.html?state=clip_then_blank")
    adapter._record_scene(project.project_id, 0, "S01", "clip-1")
    adapter._record_scene(project.project_id, 1, "S02", "clip-2")
    words = TimelineOpSpec(op_key="op1", kind=TimelineOpKind.TEXT_OVERLAY,
                           params={"text": "First scene words", "start": 0.0, "end": 6.0, "role": "super", "scene_key": "S01"})
    state = await adapter.apply_timeline_op(project, words)
    page = await adapter.manager.page()
    assert {"text": "First scene words", "scene_key": "S01", "start": 0.0, "end": 6.0} in state.text_overlays
    assert await page.evaluate("document.querySelector('#canvas video').__geo") == RIGHT_COLUMN_VIDEO
    box = await page.evaluate("document.querySelector('g[id^=editor-body]').__geo")
    assert box["x"] == 48 and box["w"] == 780

    await adapter.apply_timeline_op(project, words)
    assert await page.evaluate("window.__boxCount()") == 1, "a retry does not add the text twice"

    notice = TimelineOpSpec(op_key="op2", kind=TimelineOpKind.TEXT_OVERLAY,
                            params={"text": "Read the policy wording", "start": 6.0, "end": 11.0,
                                    "role": "disclosure", "scene_key": "S02"})
    state = await adapter.apply_timeline_op(project, notice)
    by_scene = {(o["scene_key"], o["text"]) for o in state.text_overlays}
    assert by_scene == {("S01", "First scene words"), ("S02", "Read the policy wording")}
    assert {"text": "Read the policy wording", "scene_key": "S02", "start": 6.0, "end": 11.0} in state.text_overlays
    strip = await page.evaluate("[...document.querySelectorAll('g[id^=editor-body]')].pop().__geo")
    assert strip["w"] == 1824 and abs(strip["y"] - (1080 - strip["h"] - 40)) <= 1


async def test_text_for_a_scene_that_is_not_in_the_vid_is_not_added(tmp_path, server, manager_factory):
    from vidsprod.domain.enums import TimelineOpKind
    from vidsprod.vids.port import TimelineOpSpec
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("timeline.text_overlay", "timeline.read"))
    spec = TimelineOpSpec(op_key="op1", kind=TimelineOpKind.TEXT_OVERLAY,
                          params={"text": "Orphan", "start": 0, "end": 5, "role": "super", "scene_key": "S09"})
    with pytest.raises(VidsError, match="no scene in the Vid holds S09"):
        await adapter.apply_timeline_op(project, spec)


async def test_the_preview_is_one_screenshot_per_scene_named_by_scene(tmp_path, server, manager_factory):
    adapter, project = await _vids_like_adapter(tmp_path, server, manager_factory, ("preview.capture",),
                                                page="vids_like.html?state=clip_then_blank")
    adapter._record_scene(project.project_id, 0, "S01", "clip-1")
    adapter._record_scene(project.project_id, 1, "S02", "clip-2")
    shots = await adapter.capture_preview(project, tmp_path / "preview")
    assert [s.meta["scene_key"] for s in shots] == ["S01", "S02"] and all(s.path.exists() for s in shots)
