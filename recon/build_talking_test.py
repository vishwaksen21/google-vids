"""One talking clip from the photo 'amit dave.png' (approved 2026-09-30: one generation,
Omni, 720p, 10 s; the user confirmed the go-ahead for animating this person).

    cd backend && uv run python -u ../recon/build_talking_test.py

New Vid → AI video → Ingredients: ~/Desktop/images/amit dave.png → prompt with the dialogue →
settings read back (Omni, 720p, Landscape, 10 s) → Generate once → Insert → timeline read-back
→ frames → export. A marker file is written before generating, so a second run refuses. If
anything fails after Generate, the browser is left open on the Vid.
"""

import asyncio
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue
from vidsprod.config import VidsMode, get_settings
from vidsprod.domain.enums import AspectRatio, CapabilityStatus
from vidsprod.media.ffmpeg import FFMPEG
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import ClipRequest, ExportSpec, SessionStateName

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
OUT = RECON / TODAY / "talking_test"
MARKER = OUT / "GENERATION_SPENT"
TITLE = f"[vidsprod] Prudential talking test {TODAY}"
PERSON = Path.home() / "Desktop" / "images" / "amit dave.png"
DIALOGUE = ("Health insurance is about more than managing medical expenses. "
            "It's about having the right protection when your family needs it most.")
# Typed after the 'Image1' chip Vids puts at the start of the prompt once the photo is attached,
# so the prompt reads "[Image1] speaks directly to the camera …" (observed 2026-09-30).
PROMPT = (f' speaks directly to the camera in a warm, confident, natural voice, seated in a bright modern office. '
          f'He says: "{DIALOGUE}" Medium close-up, natural lip-sync, subtle head movement and hand gestures. '
          f'No on-screen text, no logos.')


def sheet_and_audio(mp4: Path) -> dict:
    sheet = OUT / "talking_contact.png"
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(mp4), "-vf",  # noqa: S603
                    "select='eq(n\\,30)+eq(n\\,120)+eq(n\\,210)+eq(n\\,285)',scale=640:-1,tile=2x2",
                    "-frames:v", "1", "-vsync", "vfr", str(sheet)], check=True)
    vol = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(mp4), "-af", "volumedetect",  # noqa: S603
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    levels = [line.split("] ")[-1] for line in vol.splitlines() if "mean_volume" in line or "max_volume" in line]
    return {"contact_sheet": str(sheet), "audio_levels": levels}


async def main() -> int:
    if MARKER.exists():
        print("REFUSED: the approved generation was already used:", MARKER.read_text())
        return 2
    if not PERSON.is_file():
        print("STOP: missing", PERSON)
        return 1
    settings = get_settings().model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": OUT / "work", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    registry = live_registry(RECON / "capabilities.json")
    adapter = BrowserVidsAdapter(settings, registry, catalogue)
    report: dict = {"title": TITLE, "person_image": PERSON.name, "dialogue": DIALOGUE, "prompt": PROMPT}
    try:
        state = await adapter.check_session(OUT / "work")
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1
        cap = registry.get("clip.ingredients")  # under test, this run only
        cap.status, cap.ui_fingerprint = CapabilityStatus.VERIFIED, catalogue.ui_fingerprint
        if "--project" in sys.argv:  # reuse the empty Vid of a run that stopped before Generate
            from vidsprod.vids.port import ProjectHandle
            url = sys.argv[sys.argv.index("--project") + 1]
            project = await adapter.open_project(ProjectHandle(project_id=adapter._project_id(url), title=TITLE, url=url))
        else:
            project = await adapter.create_project(TITLE)
        report["url"] = project.url
        print("project:", project.url)
        OUT.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(f"generation started {TODAY} for {project.url}\n")
        clip = await adapter.generate_scene(project, ClipRequest(
            scene_key="S01", take_no=1, prompt=PROMPT, duration_seconds=10, aspect_ratio=AspectRatio.LANDSCAPE,
            idempotency_key="talking-test-s01", ingredient_paths=[PERSON]))
        report["clip"] = clip.locator
        print("generated + inserted:", clip.locator["timeline_after"], "| frames:", len(clip.locator["frames"]))
        print("result label:", clip.locator["label"][:160])

        exported = await adapter.export_video(project, ExportSpec(idempotency_key="talking-test-export"), OUT)
        check = await adapter.verify_export(exported.path)
        report["export"] = {"file": str(exported.path), "probe": check["probe"], "decode_ok": check["decode_ok"],
                            "crashed_attempts": exported.details.get("crashed_attempts")}
        print("export:", exported.path.name, json.dumps(check["probe"]), "decode_ok:", check["decode_ok"])
        report.update(await asyncio.to_thread(sheet_and_audio, exported.path))
        print("audio:", report["audio_levels"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print("FAILED:", report["error"])
        if adapter.unsaved_clip is not None:
            print("A generated clip is NOT confirmed inserted. Chrome stays open on the Vid for 30 minutes.")
            (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
            await asyncio.sleep(1800)
        return 1
    finally:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
        await adapter.close()
    print("DONE")
    return 0


sys.exit(asyncio.run(main()))
