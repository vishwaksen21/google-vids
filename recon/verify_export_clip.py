"""Export the recon Vid that holds the inserted generated clip and verify the MP4.

    cd backend && uv run python -u ../recon/verify_export_clip.py

No generation. Uses the adapter's own export (File → Download → MP4) and verify_export
(probe + full decode), then extracts frames from the file for a visual check. The Vid's
timeline is read before the export as the expected duration.
"""

import asyncio
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue, PageDriver
from vidsprod.config import VidsMode, get_settings
from vidsprod.media.ffmpeg import FFMPEG
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import ExportSpec, ProjectHandle, SessionStateName

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
OUT = RECON / TODAY / "export_clip"
PROJECT_URL = "https://docs.google.com/videos/d/1orOiofdsM8FZ3MAnkIu5D2w0FSB48l5aTW_-1iRLavk/edit"
PROJECT_TITLE = "[vidsprod] recon verify 2026-09-29"
if "--project" in sys.argv:  # export another [vidsprod] Vid: --project URL --out DIR
    PROJECT_URL = sys.argv[sys.argv.index("--project") + 1]
    PROJECT_TITLE = "(existing)"
    OUT = RECON / TODAY / sys.argv[sys.argv.index("--out") + 1]


def extract_frames(path: Path) -> list[str]:
    frames = []
    for at in (1.0, 3.0, 5.0):
        f = OUT / f"export_frame_{at:.0f}s.png"
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-ss", str(at), "-i", str(path),  # noqa: S603
                        "-frames:v", "1", str(f)], check=True)
        frames.append(str(f))
    return frames


async def main() -> int:
    settings = get_settings().model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": OUT / "work", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    adapter = BrowserVidsAdapter(settings, live_registry(RECON / "capabilities.json"), catalogue)
    report: dict = {"project": PROJECT_URL}
    try:
        state = await adapter.check_session(OUT / "work")
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1
        project = ProjectHandle(project_id=adapter._project_id(PROJECT_URL), title=PROJECT_TITLE, url=PROJECT_URL)
        await adapter.open_project(project)
        page = await adapter.manager.page()
        await page.wait_for_timeout(5000)
        driver = PageDriver(page, catalogue, OUT / "work", timeout_ms=5000, min_interval_ms=0)
        report["timeline"] = await adapter._timeline_readback(driver)
        print("timeline:", report["timeline"])
        exported = await adapter.export_video(project, ExportSpec(idempotency_key=f"export-{OUT.name}"), OUT)
        check = await adapter.verify_export(exported.path)
        report.update(file=str(exported.path), size=exported.path.stat().st_size, check=check,
                      suggested=exported.details)
        print("export:", exported.path.name, exported.path.stat().st_size, "bytes")
        print("probe:", json.dumps(check["probe"]), "| decode_ok:", check["decode_ok"])
        duration = check["probe"].get("duration_seconds") or check["probe"].get("duration")
        report["frames"] = await asyncio.to_thread(extract_frames, exported.path)
        report["duration"] = duration
    finally:
        (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
        await adapter.close()
    return 0


sys.exit(asyncio.run(main()))
