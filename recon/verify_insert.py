"""ONE controlled real generation to verify clip insertion and timeline read-back.

    cd backend && uv run python -u ../recon/verify_insert.py

Authorised once (2026-09-30). A marker file is written before Generate is clicked, so a
second run refuses even if the first one crashed. Order: pre-checks → generate → Insert
immediately → timeline read-back (scene count, total duration) → bounded frame capture →
reload and read back again (persistence). If anything fails after Generate, the browser is
left open on the Vid (a result that is not inserted lives only in that page) and nothing is
closed, reloaded or deleted. Live actions are enabled for this process only.
"""

import asyncio
import contextlib
import json
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue, PageDriver
from vidsprod.config import VidsMode, get_settings
from vidsprod.domain.enums import AspectRatio, CapabilityStatus
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import ClipRequest, ProjectHandle, SessionStateName

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
OUT = RECON / TODAY / "insert"
MARKER = OUT / "GENERATION_SPENT"
PROJECT_URL = "https://docs.google.com/videos/d/1orOiofdsM8FZ3MAnkIu5D2w0FSB48l5aTW_-1iRLavk/edit"
PROJECT_TITLE = "[vidsprod] recon verify 2026-09-29"
PROMPT = "A calm sunlit family kitchen, slow push in, natural morning light."

PROBE = """() => ({
  videos: [...document.querySelectorAll('video')].map(v => ({aria: v.getAttribute('aria-label'),
    src: (v.currentSrc || '').split('?')[0].slice(0, 80), duration: v.duration,
    w: v.videoWidth, h: v.videoHeight, inPanel: !!v.closest('[role=complementary],aside')})),
  scenes: [...document.querySelectorAll('[aria-label^="Scene"]')].map(e => e.getAttribute('aria-label')),
  total: document.querySelector('.appsFlixTimelinePlayTimeDuration')?.parentElement?.getAttribute('aria-label'),
})"""


def rel(p) -> str:
    return str(Path(p).resolve().relative_to(RECON.parent))


async def main() -> int:
    if MARKER.exists():
        print("REFUSED: the authorised generation was already used:", MARKER.read_text())
        return 2
    base = get_settings()
    settings = base.model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": OUT / "work", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    registry = live_registry(RECON / "capabilities.json")
    adapter = BrowserVidsAdapter(settings, registry, catalogue)
    keep_open = False
    report: dict = {"project": PROJECT_URL, "prompt": PROMPT}
    try:
        page = await adapter.manager.open_home()
        await page.wait_for_timeout(4000)
        fingerprint = await PageDriver(page, catalogue, OUT, timeout_ms=5000, min_interval_ms=0).fingerprint()
        if fingerprint != catalogue.ui_fingerprint:
            print("STOP: UI fingerprint changed", fingerprint, "!=", catalogue.ui_fingerprint)
            return 1
        # clip.insert is the capability under test: allowed for this run only.
        cap = registry.get("clip.insert")
        cap.status, cap.ui_fingerprint = CapabilityStatus.VERIFIED, fingerprint
        for key in ("session.check", "project.open", "clip.configure", "clip.generate"):
            if not registry.is_verified(key):
                print("STOP: prerequisite not verified:", key)
                return 1
        state = await adapter.check_session(OUT)
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1

        # Pre-check 1: the correct recon project is open.
        project = ProjectHandle(project_id=adapter._project_id(PROJECT_URL), title=PROJECT_TITLE, url=PROJECT_URL)
        await adapter.open_project(project)
        page = await adapter.manager.page()
        driver = PageDriver(page, catalogue, OUT / "work" / "precheck", timeout_ms=5000, min_interval_ms=0)
        title = await driver.locator("project.title").input_value()
        before = await adapter._timeline_readback(driver)
        results = await driver.count("generate.result")
        inserts = await driver.count("generate.result.insert")  # pre-check 2: locator known, no stale match
        print("pre-check: title", repr(title), "| timeline", before, "| results in panel", results,
              "| Insert buttons", inserts)
        await driver.screenshot("precheck_project_open")
        if title != PROJECT_TITLE or results != 0 or inserts != 0:
            print("STOP: wrong project or a stale result is present")
            return 1
        report["timeline_before"] = before

        request = ClipRequest(scene_key="S01", take_no=1, prompt=PROMPT, duration_seconds=6,
                              aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="recon-insert-0001")
        OUT.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(f"generation started {TODAY} for {PROJECT_URL}\n")
        keep_open = True  # from here on a failure must leave the page (and any result) alone
        clip = await adapter.generate_scene(project, request)
        report["clip"] = clip.locator
        print("generated + inserted:", json.dumps(clip.locator["timeline_after"]))
        print("frames:", len(clip.locator["frames"]))

        # Clip identity on the page, then persistence: after a reload the inserted clip remains.
        report["probe_after_insert"] = await page.evaluate(PROBE)
        print("probe:", json.dumps(report["probe_after_insert"])[:1200])
        await page.reload(wait_until="load")
        await page.wait_for_timeout(8000)
        await adapter._dismiss_getting_started(driver)
        report["timeline_after_reload"] = await adapter._timeline_readback(driver)
        report["probe_after_reload"] = await page.evaluate(PROBE)
        await driver.screenshot("after_reload")
        print("after reload:", report["timeline_after_reload"])
        keep_open = False
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["details"] = getattr(exc, "details", None)
        print("FAILED:", report["error"])
        if keep_open:  # read-only record of the page as it is; nothing is clicked or reloaded
            with contextlib.suppress(Exception):
                report["probe_at_failure"] = await (await adapter.manager.page()).evaluate(PROBE)
                await (await adapter.manager.page()).screenshot(path=str(OUT / "at_failure.png"))
            print("The browser stays open on the Vid for 30 minutes; nothing will be clicked, "
                  "reloaded or closed. Insert the result by hand if it is there.")
            (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
            await asyncio.sleep(1800)
        return 1
    finally:
        (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
        if not keep_open:
            await adapter.close()

    after = report["clip"]["timeline_after"]
    reload = report["timeline_after_reload"]
    persisted = reload == after
    work = sorted((OUT / "work").rglob("*.png"))
    caps_file = RECON / "capabilities.json"
    data = json.loads(caps_file.read_text())
    caps = {c["key"]: c for c in data["capabilities"]}
    common = {"status": "VERIFIED", "verified_on": TODAY, "ui_fingerprint": fingerprint}
    if persisted:
        caps["clip.insert"] = {"key": "clip.insert", **common,
                               "evidence": [rel(p) for p in work if "inserted" in p.name or "reload" in p.name]
                               + [rel(OUT / "report.json")],
                               "notes": f"Insert right after generation; timeline {report['timeline_before']} -> "
                                        f"{after}; unchanged after reload"}
    frames = report["clip"]["frames"]
    if len(frames) == 3 and all(Path(f).stat().st_size > 5000 for f in frames):
        caps["clip.capture_frames"] = {"key": "clip.capture_frames", **common,
                                       "evidence": [rel(f) for f in frames],
                                       "notes": "seek the result <video> at 15/50/85% (bounded), element screenshots; "
                                                "taken after insertion"}
    data["capabilities"] = list(caps.values())
    caps_file.write_text(json.dumps(data, indent=1))
    print("persisted after reload:", persisted, "| recorded:",
          [k for k in ("clip.insert", "clip.capture_frames") if caps.get(k, {}).get("status") == "VERIFIED"])
    return 0 if persisted else 1


sys.exit(asyncio.run(main()))
