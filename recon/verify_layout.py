"""Verify still-image upload and pixel layout on the live editor (no generation).

    cd backend && uv run python -u ../recon/verify_layout.py

On '[vidsprod] recon scenes 2026-09-30': append a blank scene (timeline '+'), upload a neutral
test image aligned right, add body text in the left column (w 780, x 48, centred), reopen and
check the image and text are there and the scene length did not change.
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue, PageDriver
from vidsprod.automation.google_vids.adapter import canvas_has_text
from vidsprod.config import VidsMode, get_settings
from vidsprod.domain.enums import CapabilityStatus
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import ProjectHandle, SessionStateName

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
OUT = RECON / TODAY / "images"
VID = ("https://docs.google.com/videos/d/1QtpavacPmOATjUckjqdMGdae_LX5lBr1ZGeL_Q84BAY/edit",
       "[vidsprod] recon scenes 2026-09-30")
TEXT = "Layout check: text in the left column, image on the right."


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
        cap = registry.get("timeline.image")  # under test, this run only
        cap.status, cap.ui_fingerprint = CapabilityStatus.VERIFIED, catalogue.ui_fingerprint
        project = ProjectHandle(project_id=adapter._project_id(VID[0]), title=VID[1], url=VID[0])
        await adapter.open_project(project)
        await (await adapter.manager.page()).wait_for_timeout(4000)
        tl = await adapter.add_blank_scene(project, force_new=True)
        scene = tl["active"]
        report["image"] = await adapter.insert_image(project, OUT / "recon_test_image.png", scene_index=scene)
        print("image placed:", report["image"])
        report["text"] = await adapter.add_text_overlay(project, TEXT, scene_index=scene, kind="body",
                                                         layout={"w": 780, "x": 48})
        print("text:", " ".join(report["text"]))

        await adapter.open_project(project)
        page = await adapter.manager.page()
        await page.wait_for_timeout(5000)
        driver = PageDriver(page, catalogue, OUT / "verify", timeout_ms=5000, min_interval_ms=0)
        await driver.locator("timeline.scene").nth(scene).click()
        await page.wait_for_timeout(1500)
        report["after_reopen"] = {"images": await driver.count("canvas.image"),
                                  "text_ok": canvas_has_text(await adapter._canvas_text(driver), TEXT),
                                  "timeline": await adapter._timeline_readback(driver)}
        await driver.screenshot("layout_reopened")
        print("after reopen:", report["after_reopen"])
    finally:
        (OUT / "verify_report.json").write_text(json.dumps(report, indent=1, default=str))
        await adapter.close()
    r = report["after_reopen"]
    ok = (r["images"] == 1 and r["text_ok"] and r["timeline"]["durations"][scene] == 5.0
          and report["image"]["x"] == 872.0)
    if ok:
        data = json.loads((RECON / "capabilities.json").read_text())
        caps = {x["key"]: x for x in data["capabilities"]}
        caps["timeline.image"] = {"key": "timeline.image", "status": "VERIFIED", "verified_on": TODAY,
            "ui_fingerprint": catalogue.ui_fingerprint,
            "evidence": [rel(p) for p in sorted((OUT / "verify").rglob("*.png"))] + [rel(OUT / "verify_report.json")],
            "notes": "insert_image(): Insert → Upload (file chooser) → image on the active scene, fit to height at (32,32) → "
                     "aligned right and centred via Format options pixel fields (read back); persisted; scene length unchanged. "
                     "Body text layout (w 780, x 48, centred) set the same way. 'Convert to video' is never clicked."}
        data["capabilities"] = list(caps.values())
        (RECON / "capabilities.json").write_text(json.dumps(data, indent=1))
    print("recorded timeline.image:", ok)
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
