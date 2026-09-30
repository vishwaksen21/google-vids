"""Finish the Prudential v1 Vid (no generation): put scene 1's clip in the right column so the
Section 1 text sits on white, verify it after reopening, then export and verify the MP4.

    cd backend && uv run python -u ../recon/finish_prudential_v1.py
"""

import asyncio
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue, PageDriver
from vidsprod.config import VidsMode, get_settings
from vidsprod.domain.enums import CapabilityStatus
from vidsprod.media.ffmpeg import FFMPEG
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import ExportSpec, ProjectHandle, SessionStateName

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
OUT = RECON / TODAY / "prudential_v1"
URL = "https://docs.google.com/videos/d/1xTJlOaeUWcMRCdfCAPtm0USILX7ngf2YVELyU3VJT0c/edit"
TITLE = f"[vidsprod] Prudential Health India v1 {TODAY}"
VIDEO_LAYOUT = {"w": 1016, "h": 571.5, "x": 872, "y": 254.2}


def rel(p) -> str:
    return str(Path(p).resolve().relative_to(RECON.parent))


def contact_sheet(mp4: Path) -> Path:
    sheet = OUT / "v1_contact.png"
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(mp4), "-vf",  # noqa: S603
                    "select='eq(n\\,60)+eq(n\\,240)+eq(n\\,390)+eq(n\\,540)',scale=640:-1,tile=2x2",
                    "-frames:v", "1", "-vsync", "vfr", str(sheet)], check=True)
    return sheet


async def main() -> int:
    settings = get_settings().model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": OUT / "finish", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    registry = live_registry(RECON / "capabilities.json")
    adapter = BrowserVidsAdapter(settings, registry, catalogue)
    report: dict = {"url": URL}
    try:
        state = await adapter.check_session(OUT / "finish")
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1
        cap = registry.get("timeline.layout_video")  # under test, this run only
        cap.status, cap.ui_fingerprint = CapabilityStatus.VERIFIED, catalogue.ui_fingerprint
        project = await adapter.open_project(ProjectHandle(project_id=adapter._project_id(URL), title=TITLE, url=URL))
        page = await adapter.manager.page()
        await page.wait_for_timeout(5000)
        report["video_layout"] = await adapter.place_scene_video(project, 0, **VIDEO_LAYOUT)
        print("clip placed:", report["video_layout"])

        await adapter.open_project(project)  # reopen: did the layout persist?
        page = await adapter.manager.page()
        await page.wait_for_timeout(6000)
        driver = PageDriver(page, catalogue, OUT / "finish", timeout_ms=5000, min_interval_ms=0)
        await driver.locator("timeline.scene").nth(0).click()
        await page.wait_for_timeout(1500)
        video = await driver.expect_one("canvas.video")
        await video.click(force=True)
        report["video_after_reopen"] = await adapter._read_geometry(driver)
        report["timeline"] = await adapter._timeline_readback(driver)
        await driver.press_key("Escape")
        await driver.screenshot("v1_scene_1_final")
        print("after reopen:", report["video_after_reopen"], report["timeline"]["durations"])

        exported = await adapter.export_video(project, ExportSpec(idempotency_key="prudential-v1-export"), OUT)
        check = await adapter.verify_export(exported.path)
        report["export"] = {"file": str(exported.path), "probe": check["probe"], "decode_ok": check["decode_ok"],
                            "crashed_attempts": exported.details.get("crashed_attempts")}
        print("export:", exported.path.name, json.dumps(check["probe"]), "decode_ok:", check["decode_ok"])
        report["contact_sheet"] = str(await asyncio.to_thread(contact_sheet, exported.path))
    finally:
        (OUT / "finish_report.json").write_text(json.dumps(report, indent=1, default=str))
        await adapter.close()

    geo_ok = all(abs(report["video_after_reopen"][k] - v) <= 1 for k, v in VIDEO_LAYOUT.items())
    if geo_ok and report["timeline"]["durations"] == [10.0, 5.0, 5.0]:
        data = json.loads((RECON / "capabilities.json").read_text())
        caps = {x["key"]: x for x in data["capabilities"]}
        caps["timeline.layout_video"] = {"key": "timeline.layout_video", "status": "VERIFIED", "verified_on": TODAY,
            "ui_fingerprint": catalogue.ui_fingerprint,
            "evidence": [rel(p) for p in sorted((OUT / "finish").rglob("*.png"))] + [rel(OUT / "finish_report.json")],
            "notes": "place_scene_video(): select the canvas clip (forced click) → Format options pixel fields → "
                     "1016x571.5 at (872, 254.2); persisted after reopening; scene lengths unchanged"}
        data["capabilities"] = list(caps.values())
        (RECON / "capabilities.json").write_text(json.dumps(data, indent=1))
    ok = geo_ok and report["export"]["decode_ok"] and abs(report["export"]["probe"]["duration_seconds"] - 20) < 0.3
    print("DONE ok =", ok)
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
