"""Prudential Health India video v1: 20 s, one generated clip + two still photos + section text.

    cd backend && uv run python -u ../recon/build_prudential_v1.py

Approved 2026-09-30: still images only (no animation of real people), exactly ONE generation,
target 20 s. Scenes: 1 = generated clip (10 s, fictional family, no text/logos) + Section 1;
2 = still photo + Section 2 (5 s); 3 = still photo + Section 3 (5 s). Text sits in the left
column (w 780, x 48), visuals on the right. A marker file is written before generating, so a
second run can never generate again; with --resume URL a run continues a Vid without
generating. If anything fails after Generate, the browser is left open on the Vid.
"""

import asyncio
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue, PageDriver
from vidsprod.automation.google_vids.adapter import canvas_has_text
from vidsprod.config import VidsMode, get_settings
from vidsprod.domain.enums import AspectRatio
from vidsprod.media.ffmpeg import FFMPEG
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import ClipRequest, ExportSpec, ProjectHandle, SessionStateName

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
OUT = RECON / TODAY / "prudential_v1"
MARKER = OUT / "GENERATION_SPENT"
TITLE = f"[vidsprod] Prudential Health India v1 {TODAY}"
IMAGES = Path.home() / "Desktop" / "images"
# macOS screenshot names hold a narrow no-break space before 'PM', so they are matched by pattern
STILLS = {1: IMAGES / "1697537937950.jpeg",
          2: next(iter(sorted(IMAGES.glob("Screenshot 2026-09-25 at 4.29.19*.png"))), IMAGES / "missing.png")}
PROMPT = ("Warm, realistic scene in a bright Indian family home: a mother, father and their young daughter "
          "share breakfast at the kitchen table, relaxed and smiling, soft natural morning light, slow gentle "
          "push-in. No text, no logos.")
CLIP_SECONDS = 10
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
TEXT_LAYOUT = {"w": 780, "x": 48}
VIDEO_LAYOUT = {"w": 1016, "h": 571.5, "x": 872, "y": 254.2}  # 16:9 in the right column


def contact_sheet(mp4: Path) -> Path:
    sheet = OUT / "v1_contact.png"
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(mp4), "-vf",  # noqa: S603
                    "select='eq(n\\,60)+eq(n\\,240)+eq(n\\,390)+eq(n\\,540)',scale=640:-1,tile=2x2",
                    "-frames:v", "1", "-vsync", "vfr", str(sheet)], check=True)
    return sheet


async def main() -> int:
    resume = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else None
    if not resume and MARKER.exists():
        print("REFUSED: the approved generation was already used:", MARKER.read_text())
        return 2
    for still in STILLS.values():
        if not still.is_file():
            print("STOP: missing image", still)
            return 1
    settings = get_settings().model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": OUT / "work", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    adapter = BrowserVidsAdapter(settings, live_registry(RECON / "capabilities.json"), catalogue)
    report: dict = {"title": TITLE, "prompt": PROMPT, "stills": {k: v.name for k, v in STILLS.items()}}
    try:
        state = await adapter.check_session(OUT / "work")
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1
        if resume:
            project = await adapter.open_project(ProjectHandle(project_id=adapter._project_id(resume),
                                                               title=TITLE, url=resume))
        else:
            project = await adapter.create_project(TITLE)
            OUT.mkdir(parents=True, exist_ok=True)
            MARKER.write_text(f"generation started {TODAY} for {project.url}\n")
            clip = await adapter.generate_scene(project, ClipRequest(
                scene_key="S01", take_no=1, prompt=PROMPT, duration_seconds=CLIP_SECONDS,
                aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="prudential-v1-s01"))
            report["clip"] = clip.locator
            print("generated + inserted:", clip.locator["timeline_after"], "| frames:", len(clip.locator["frames"]))
        report["url"] = project.url
        print("project:", project.url)
        page = await adapter.manager.page()
        driver = PageDriver(page, catalogue, OUT / "work", timeout_ms=5000, min_interval_ms=0)
        while len((await adapter._timeline_readback(driver))["scenes"]) < len(SECTIONS):
            await adapter.add_blank_scene(project, force_new=True)
        report["timeline_built"] = await adapter._timeline_readback(driver)
        print("scenes:", report["timeline_built"]["durations"])

        try:
            report["video_layout"] = await adapter.place_scene_video(project, 0, **VIDEO_LAYOUT)
            print("clip placed right:", report["video_layout"])
        except Exception as exc:  # layout only: the clip stays; report and continue
            report["video_layout_error"] = f"{type(exc).__name__}: {exc}"
            print("clip layout skipped:", report["video_layout_error"])

        for scene, still in STILLS.items():
            await driver.locator("timeline.scene").nth(scene).click()
            await page.wait_for_timeout(800)
            if await driver.count("canvas.image"):
                print(f"scene {scene + 1} already has an image")
                continue
            report[f"image_{scene + 1}"] = await adapter.insert_image(project, still, scene_index=scene)
            print(f"scene {scene + 1} image:", report[f"image_{scene + 1}"])

        for i, text in enumerate(SECTIONS):
            await driver.locator("timeline.scene").nth(i).click()
            await page.wait_for_timeout(800)
            if canvas_has_text(await adapter._canvas_text(driver), text):
                print(f"scene {i + 1} already has its text")
                continue
            await adapter.add_text_overlay(project, text, scene_index=i, kind="body", layout=TEXT_LAYOUT)
            print(f"scene {i + 1} text set")

        await adapter.open_project(project)  # reopen and read back
        page = await adapter.manager.page()
        await page.wait_for_timeout(6000)
        driver = PageDriver(page, catalogue, OUT / "work", timeout_ms=5000, min_interval_ms=0)
        timeline = await adapter.read_timeline(project)
        report["timeline"] = {"durations": [i.duration_seconds for i in timeline.items],
                              "total": timeline.total_duration_seconds}
        report["scenes"] = []
        for i in range(len(SECTIONS)):
            await driver.locator("timeline.scene").nth(i).click()
            await page.wait_for_timeout(1500)
            report["scenes"].append({"scene": i + 1, "text_ok": canvas_has_text(await adapter._canvas_text(driver), SECTIONS[i]),
                                     "images": await driver.count("canvas.image"),
                                     "videos": await driver.count("canvas.video")})
            await driver.screenshot(f"v1_scene_{i + 1}")
        print("read back:", report["timeline"], report["scenes"])

        exported = await adapter.export_video(project, ExportSpec(idempotency_key="prudential-v1-export"), OUT)
        check = await adapter.verify_export(exported.path)
        report["export"] = {"file": str(exported.path), "probe": check["probe"], "decode_ok": check["decode_ok"],
                            "crashed_attempts": exported.details.get("crashed_attempts")}
        print("export:", exported.path.name, json.dumps(check["probe"]), "decode_ok:", check["decode_ok"])
        report["contact_sheet"] = str(await asyncio.to_thread(contact_sheet, exported.path))
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print("FAILED:", report["error"])
        if adapter.unsaved_clip is not None:
            print("A generated clip is NOT inserted. Chrome stays open on the Vid for 30 minutes: insert it by hand.")
            (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
            await asyncio.sleep(1800)
        return 1
    finally:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
        await adapter.close()  # refuses (and logs) while a clip is not inserted
    ok = (report["timeline"]["durations"] == [float(CLIP_SECONDS), 5.0, 5.0]
          and all(s["text_ok"] for s in report["scenes"]) and report["export"]["decode_ok"])
    print("DONE ok =", ok)
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
