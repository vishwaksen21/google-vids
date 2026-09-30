"""No-quota draft of the Prudential Health India video: three scenes with each section's text.

    cd backend && uv run python -u ../recon/build_prudential_draft.py

Uses only verified adapter recipes: create_project, add_blank_scene (the timeline '+'),
add_text_overlay(kind="body"), read_timeline, export_video + verify_export. No generation, no
images (their use awaits consent and a generation budget). Live actions are enabled for this
process only; body text also verifies the body variant of timeline.text_overlay.
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue, PageDriver
from vidsprod.automation.google_vids.adapter import canvas_has_text
from vidsprod.config import VidsMode, get_settings
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import ExportSpec, ProjectHandle, SessionStateName

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
OUT = RECON / TODAY / "prudential_draft"
TITLE = f"[vidsprod] Prudential Health India draft {TODAY}"
SECTIONS = [
    "Health insurance is about more than managing medical expenses. It is about having the right "
    "protection, support, and confidence when you and your family need healthcare the most.",
    "PRUHealth Edge is designed for comprehensive health protection, with personalised Prudential "
    "Health Check-ups, unlimited restoration from the second claim onwards, Loyalty Addition, and "
    "coverage for eligible modern treatments.",
    "PRUHealth Signature is designed to grow with your family, offering two-times protection from day "
    "one, unlimited restoration from the first claim, enhanced Prudential Health Check-ups, and "
    "Loyalty Addition.",
]


async def main() -> int:
    settings = get_settings().model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": OUT / "work", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    adapter = BrowserVidsAdapter(settings, live_registry(RECON / "capabilities.json"), catalogue)
    report: dict = {"title": TITLE}
    try:
        state = await adapter.check_session(OUT / "work")
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1
        if "--resume" in sys.argv:  # continue the Vid a previous run created
            url = sys.argv[sys.argv.index("--resume") + 1]
            project = await adapter.open_project(ProjectHandle(project_id=adapter._project_id(url), title=TITLE, url=url))
        else:
            project = await adapter.create_project(TITLE)
        report["url"] = project.url
        print("project:", project.url)
        page = await adapter.manager.page()
        driver = PageDriver(page, catalogue, OUT / "work", timeout_ms=5000, min_interval_ms=0)
        while len((tl := await adapter._timeline_readback(driver))["scenes"]) < len(SECTIONS):
            tl = await adapter.add_blank_scene(project, force_new=True)
        print("scenes:", tl["scenes"], tl["durations"])
        report["rendered"] = []
        for i, text in enumerate(SECTIONS):
            await driver.locator("timeline.scene").nth(i).click()
            await page.wait_for_timeout(1000)
            if canvas_has_text(await adapter._canvas_text(driver), text):
                print(f"scene {i + 1} already has its text")
                continue
            rendered = await adapter.add_text_overlay(project, text, scene_index=i, kind="body")
            report["rendered"].append(" ".join(rendered))
            print(f"scene {i + 1} text set ({len(rendered)} words)")

        await adapter.open_project(project)  # reopen and read everything back
        page = await adapter.manager.page()
        await page.wait_for_timeout(5000)
        driver = PageDriver(page, catalogue, OUT / "work", timeout_ms=5000, min_interval_ms=0)
        timeline = await adapter.read_timeline(project)
        report["timeline"] = {"durations": [i.duration_seconds for i in timeline.items],
                              "total": timeline.total_duration_seconds}
        report["after_reopen"] = []
        for i in range(len(SECTIONS)):
            await driver.locator("timeline.scene").nth(i).click()
            await page.wait_for_timeout(1200)
            words = await adapter._canvas_text(driver)
            report["after_reopen"].append({"scene": i + 1, "ok": canvas_has_text(words, SECTIONS[i]),
                                           "text": " ".join(words)})
            await driver.screenshot(f"scene_{i + 1}_reopened")
        print("after reopen:", report["timeline"], [s["ok"] for s in report["after_reopen"]])

        exported = await adapter.export_video(project, ExportSpec(idempotency_key="prudential-draft-0001"), OUT)
        check = await adapter.verify_export(exported.path)
        report["export"] = {"file": str(exported.path), "probe": check["probe"], "decode_ok": check["decode_ok"]}
        print("export:", exported.path.name, json.dumps(check["probe"]), "decode_ok:", check["decode_ok"])
    finally:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
        await adapter.close()
    return 0 if all(s["ok"] for s in report["after_reopen"]) else 1


sys.exit(asyncio.run(main()))
