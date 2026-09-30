"""No-quota follow-up to verify_insert.py: reopen the recon Vid through the adapter and prove
the inserted clip persisted (timeline read-back, clip identity on the canvas), then capture
frames from it with the adapter's bounded capture. Nothing is generated, inserted or edited.

    cd backend && uv run python -u ../recon/verify_inserted.py
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue, PageDriver
from vidsprod.config import VidsMode, get_settings
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import ProjectHandle, SessionStateName

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
OUT = RECON / TODAY / "insert"
PROJECT_URL = "https://docs.google.com/videos/d/1orOiofdsM8FZ3MAnkIu5D2w0FSB48l5aTW_-1iRLavk/edit"
PROJECT_TITLE = "[vidsprod] recon verify 2026-09-29"


def rel(p) -> str:
    return str(Path(p).resolve().relative_to(RECON.parent))


async def main() -> int:
    settings = get_settings().model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": OUT / "reopen", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    registry = live_registry(RECON / "capabilities.json")
    adapter = BrowserVidsAdapter(settings, registry, catalogue)
    report: dict = {}
    try:
        state = await adapter.check_session(OUT / "reopen")
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1
        fingerprint = catalogue.ui_fingerprint
        project = ProjectHandle(project_id=adapter._project_id(PROJECT_URL), title=PROJECT_TITLE, url=PROJECT_URL)
        await adapter.open_project(project)
        page = await adapter.manager.page()
        await page.wait_for_timeout(5000)
        driver = PageDriver(page, catalogue, OUT / "reopen", timeout_ms=5000, min_interval_ms=0)
        title = await driver.locator("project.title").input_value()
        timeline = await adapter._timeline_readback(driver)
        videos = driver.locator("canvas.video")
        n = await videos.count()
        clip = await videos.first.evaluate("""v => new Promise(res => {
            const r = () => res({duration: v.duration, w: v.videoWidth, h: v.videoHeight});
            if (v.readyState >= 1) return r();
            v.addEventListener('loadedmetadata', r, {once: true}); setTimeout(r, 15000); })""",
            timeout=20000) if n else None
        await driver.screenshot("reopened")
        print("title", repr(title), "| timeline", timeline, "| canvas videos", n, clip)
        report.update(title=title, timeline=timeline, canvas_videos=n, clip=clip)
        frames = await adapter._clip_frames_bounded(driver, videos.first, OUT / "reopen" / "frames", "S01_inserted") if n == 1 else []
        after = await adapter._timeline_readback(driver)
        report.update(frames=[str(f.path) for f in frames], timeline_after_frames=after)
        print("frames:", len(frames), "| timeline unchanged by inspection:", after == timeline)
    finally:
        (OUT / "reopen_report.json").write_text(json.dumps(report, indent=1, default=str))
        await adapter.close()

    ok_insert = (report.get("title") == PROJECT_TITLE and timeline == {"scenes": ["Scene 1 of 1"], "total_seconds": 6.0}
                 and n == 1 and clip and abs(clip["duration"] - 6.016) < 0.1 and (clip["w"], clip["h"]) == (1280, 720))
    ok_frames = len(frames) == 3 and all(f.path.stat().st_size > 5000 for f in frames) and after == timeline
    data = json.loads((RECON / "capabilities.json").read_text())
    caps = {c["key"]: c for c in data["capabilities"]}
    common = {"status": "VERIFIED", "verified_on": TODAY, "ui_fingerprint": fingerprint}
    shots = sorted(p for p in (OUT / "work").rglob("*.png")) + sorted((OUT / "reopen").glob("*reopened*.png"))
    if ok_insert:
        caps["clip.insert"] = {"key": "clip.insert", **common,
            "evidence": [rel(p) for p in shots] + [rel(OUT / "report.json"), rel(OUT / "reopen_report.json")],
            "notes": "One real generation 2026-09-30 (~28 s) then '← Insert' at once. Insert fills the SELECTED "
                     "blank scene: 1 scene 5.0 s -> 1 scene 6.0 s; canvas <video> 6.016 s 1280x720; unchanged "
                     "after reopening. It does not append a scene: one blank scene per clip is needed first."}
    if ok_frames:
        caps["clip.capture_frames"] = {"key": "clip.capture_frames", **common,
            "evidence": [rel(f.path) for f in frames],
            "notes": "adapter's bounded capture (seek 15/50/85%, 30 s cap) on the inserted canvas video, after "
                     "insertion; timeline unchanged by it"}
    data["capabilities"] = list(caps.values())
    (RECON / "capabilities.json").write_text(json.dumps(data, indent=1))
    print("recorded:", {"clip.insert": bool(ok_insert), "clip.capture_frames": ok_frames})
    return 0 if ok_insert else 1


sys.exit(asyncio.run(main()))
