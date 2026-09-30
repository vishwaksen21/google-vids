"""Verify scene preparation and timeline read-back on the live editor (no generation).

    cd backend && uv run python -u ../recon/verify_scenes.py

Uses the adapter's own recipes on two [vidsprod] recon Vids:
* the generated-clip Vid (clip + blank scene): read_timeline, then add_blank_scene must
  select the blank LAST scene and reuse it (no scene added);
* the dedicated scenes Vid: add_blank_scene(force_new) must append one blank scene after the
  last one; reopening must show it persisted.
Adds at most one blank scene. Nothing is generated, inserted or deleted.
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue, PageDriver
from vidsprod.config import VidsMode, get_settings
from vidsprod.domain.enums import CapabilityStatus
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import ProjectHandle, SessionStateName

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
OUT = RECON / TODAY / "scenes"
CLIP_VID = ("https://docs.google.com/videos/d/1orOiofdsM8FZ3MAnkIu5D2w0FSB48l5aTW_-1iRLavk/edit",
            "[vidsprod] recon verify 2026-09-29")
SCENES_VID = ("https://docs.google.com/videos/d/1QtpavacPmOATjUckjqdMGdae_LX5lBr1ZGeL_Q84BAY/edit",
              "[vidsprod] recon scenes 2026-09-30")


def rel(p) -> str:
    return str(Path(p).resolve().relative_to(RECON.parent))


async def main() -> int:
    settings = get_settings().model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": OUT / "verify", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    registry = live_registry(RECON / "capabilities.json")
    adapter = BrowserVidsAdapter(settings, registry, catalogue)
    report: dict = {}
    try:
        state = await adapter.check_session(OUT / "verify")
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1
        for key in ("timeline.read", "timeline.add_scene"):  # under test, this run only
            cap = registry.get(key)
            cap.status, cap.ui_fingerprint = CapabilityStatus.VERIFIED, catalogue.ui_fingerprint

        clip = ProjectHandle(project_id=adapter._project_id(CLIP_VID[0]), title=CLIP_VID[1], url=CLIP_VID[0])
        await adapter.open_project(clip)
        await (await adapter.manager.page()).wait_for_timeout(4000)
        tl = await adapter.read_timeline(clip)
        report["clip_read"] = {"durations": [i.duration_seconds for i in tl.items], "total": tl.total_duration_seconds}
        print("clip Vid read_timeline:", report["clip_read"])
        prepared = await adapter.add_blank_scene(clip)
        report["clip_prepare"] = prepared
        print("clip Vid add_blank_scene:", prepared)

        scenes = ProjectHandle(project_id=adapter._project_id(SCENES_VID[0]), title=SCENES_VID[1], url=SCENES_VID[0])
        await adapter.open_project(scenes)
        await (await adapter.manager.page()).wait_for_timeout(4000)
        before = await adapter.read_timeline(scenes)
        appended = await adapter.add_blank_scene(scenes, force_new=True)
        await adapter.open_project(scenes)  # reopen: persisted?
        page = await adapter.manager.page()
        await page.wait_for_timeout(5000)
        after = await adapter.read_timeline(scenes)
        driver = PageDriver(page, catalogue, OUT / "verify", timeout_ms=5000, min_interval_ms=0)
        await driver.screenshot("scenes_vid_reopened")
        report["scenes"] = {"before": [i.duration_seconds for i in before.items], "appended": appended,
                            "after_reopen": [i.duration_seconds for i in after.items],
                            "after_total": after.total_duration_seconds}
        print("scenes Vid:", json.dumps(report["scenes"]))
    finally:
        (OUT / "verify_report.json").write_text(json.dumps(report, indent=1, default=str))
        await adapter.close()

    c, sc = report["clip_prepare"], report["scenes"]
    ok_read = report["clip_read"] == {"durations": [6.0, 5.0], "total": 11.0}
    ok_prepare = (c["scenes"] == ["Scene 1 of 2", "Scene 2 of 2"] and c["active"] == 1
                  and sc["appended"]["active"] == len(sc["before"])
                  and sc["after_reopen"] == sc["before"] + [5.0]
                  and abs(sc["after_total"] - sum(sc["before"]) - 5.0) < 0.05)
    data = json.loads((RECON / "capabilities.json").read_text())
    caps = {x["key"]: x for x in data["capabilities"]}
    common = {"status": "VERIFIED", "verified_on": TODAY, "ui_fingerprint": catalogue.ui_fingerprint}
    shots = [rel(p) for p in sorted((OUT / "verify").rglob("*.png"))] + [rel(OUT / "verify_report.json")]
    if ok_read:
        caps["timeline.read"] = {"key": "timeline.read", **common, "evidence": shots,
            "notes": "read_timeline(): per-scene durations + total; clip Vid reads [6.0, 5.0] / 11.0 s"}
    if ok_prepare:
        caps["timeline.add_scene"] = {"key": "timeline.add_scene", **common, "evidence": shots,
            "notes": "add_blank_scene(): selects the last scene, reuses it when blank, else 'New scene' appends "
                     "a blank 5.0 s scene after it (active); persisted after reopening"}
    data["capabilities"] = list(caps.values())
    (RECON / "capabilities.json").write_text(json.dumps(data, indent=1))
    print("recorded:", {"timeline.read": ok_read, "timeline.add_scene": ok_prepare})
    return 0 if ok_read and ok_prepare else 1


sys.exit(asyncio.run(main()))
