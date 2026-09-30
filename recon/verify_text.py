"""Verify the guarded text-overlay recipe on the live editor (no generation).

    cd backend && uv run python -u ../recon/verify_text.py

On the dedicated '[vidsprod] recon scenes 2026-09-30' Vid: add a title to blank scene 2 with
the adapter's recipe (double-click selects the default word; no key is sent until the text is
selected; no editing keys at all), then reopen and read the canvas text of scene 3 back.
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
OUT = RECON / TODAY / "text"
VID = ("https://docs.google.com/videos/d/1QtpavacPmOATjUckjqdMGdae_LX5lBr1ZGeL_Q84BAY/edit",
       "[vidsprod] recon scenes 2026-09-30")
TEXT = "vidsprod overlay check"
SCENE = 1  # scene 2 (blank); scene 3 keeps an unedited "Title" box from a guarded stop


def rel(p) -> str:
    return str(Path(p).resolve().relative_to(RECON.parent))


async def main() -> int:
    settings = get_settings().model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": OUT / "verify", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    registry = live_registry(RECON / "capabilities.json")
    adapter = BrowserVidsAdapter(settings, registry, catalogue)
    report: dict = {"text": TEXT, "scene": SCENE + 1}
    try:
        state = await adapter.check_session(OUT / "verify")
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1
        cap = registry.get("timeline.text_overlay")  # under test, this run only
        cap.status, cap.ui_fingerprint = CapabilityStatus.VERIFIED, catalogue.ui_fingerprint
        project = ProjectHandle(project_id=adapter._project_id(VID[0]), title=VID[1], url=VID[0])
        await adapter.open_project(project)
        page = await adapter.manager.page()
        await page.wait_for_timeout(4000)
        driver = PageDriver(page, catalogue, OUT / "verify", timeout_ms=5000, min_interval_ms=0)
        report["before"] = await adapter._timeline_readback(driver)
        report["rendered"] = await adapter.add_text_overlay(project, TEXT, scene_index=SCENE)
        print("rendered:", report["rendered"])

        await adapter.open_project(project)  # reopen: persisted?
        page = await adapter.manager.page()
        await page.wait_for_timeout(5000)
        driver = PageDriver(page, catalogue, OUT / "verify", timeout_ms=5000, min_interval_ms=0)
        await driver.locator("timeline.scene").nth(SCENE).click()
        await page.wait_for_timeout(1500)
        report["after_reopen"] = {"canvas": await adapter._canvas_text(driver),
                                  "timeline": await adapter._timeline_readback(driver)}
        await driver.screenshot("text_reopened")
        print("after reopen:", report["after_reopen"])
    finally:
        (OUT / "verify_report.json").write_text(json.dumps(report, indent=1, default=str))
        await adapter.close()

    after = report["after_reopen"]
    ok = (canvas_has_text(after["canvas"], TEXT) and "Title" not in after["canvas"]
          and len(after["timeline"]["scenes"]) == len(report["before"]["scenes"]))
    if ok:
        data = json.loads((RECON / "capabilities.json").read_text())
        caps = {x["key"]: x for x in data["capabilities"]}
        caps["timeline.text_overlay"] = {"key": "timeline.text_overlay", "status": "VERIFIED", "verified_on": TODAY,
            "ui_fingerprint": catalogue.ui_fingerprint,
            "evidence": [rel(p) for p in sorted((OUT / "verify").rglob("*.png"))] + [rel(OUT / "verify_report.json")],
            "notes": "add_text_overlay(): Insert text → Add a title → double-click the canvas word 'Title' (forced: a "
                     "hit-area path overlays it) → require the text-selection overlay AND the text-format toolbar → "
                     "type printable text only (no Backspace/Delete/Enter) → canvas read-back; persisted after reopen; "
                     "scene count unchanged. Default title style only (no formatting)."}
        data["capabilities"] = list(caps.values())
        (RECON / "capabilities.json").write_text(json.dumps(data, indent=1))
    print("recorded timeline.text_overlay:", ok)
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
