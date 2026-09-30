"""Verify the no-quota Vids capabilities against the live UI and record the evidence.

    cd backend && uv run python ../recon/verify_no_quota.py

Runs the production adapter's own recipes on the signed-in dedicated profile:
session check (+ UI fingerprint), create_project, open_project, configure_scene (prompt,
duration, aspect ratio, read back). It never clicks Generate and spends no quota. On
success it writes the fingerprint into recon/locators.json and VERIFIED entries (with
evidence paths) into recon/capabilities.json. Live actions are enabled for this process
only; backend/.env is not changed.
"""

import asyncio
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue, PageDriver
from vidsprod.config import VidsMode, get_settings
from vidsprod.domain.enums import AspectRatio, CapabilityStatus
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import ClipRequest, ExportSpec, SessionStateName

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
EVIDENCE = RECON / TODAY / "verify"
PROMPT = "A calm sunlit family kitchen, slow push in, natural morning light."


def copy_evidence(files) -> list[str]:
    out = []
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for f in files:
        target = EVIDENCE / Path(f).name
        if Path(f).resolve() != target.resolve():
            shutil.copyfile(f, target)
        out.append(str(target.relative_to(RECON.parent)))
    return out


async def main() -> int:
    base = get_settings()
    settings = base.model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": RECON / TODAY / "work", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    registry = live_registry(RECON / "capabilities.json")
    adapter = BrowserVidsAdapter(settings, registry, catalogue)
    results: dict[str, dict] = {}
    try:
        page = await adapter.manager.open_home()
        await page.wait_for_timeout(4000)
        fingerprint = await PageDriver(page, catalogue, EVIDENCE, timeout_ms=5000,
                                       min_interval_ms=0).fingerprint()
        catalogue.ui_fingerprint = fingerprint
        # Capabilities under verification are allowed for this run only.
        for key in ("session.check", "project.create", "project.open", "clip.configure"):
            cap = registry.get(key)
            cap.status, cap.ui_fingerprint = CapabilityStatus.VERIFIED, fingerprint
        state = await adapter.check_session(EVIDENCE)
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1
        results["session.check"] = {"evidence": copy_evidence([e.path for e in state.evidence])}

        if "--project" in sys.argv:  # reuse an existing [vidsprod] Vid instead of creating one
            from vidsprod.vids.port import ProjectHandle
            url = sys.argv[sys.argv.index("--project") + 1]
            project = ProjectHandle(project_id=adapter._project_id(url), title="(existing)", url=url)
        else:
            project = await adapter.create_project(f"{settings.google_vids_project_prefix} recon verify {TODAY}")
            print("created:", project.project_id, project.title)
            results["project.create"] = {"project": project.model_dump(mode="json")}

        project = await adapter.open_project(project)
        print("reopened: title verified")
        results["project.open"] = {"project": project.project_id}

        label = await adapter.configure_scene(project, ClipRequest(
            scene_key="S01", take_no=1, prompt=PROMPT, duration_seconds=6,
            aspect_ratio=AspectRatio.LANDSCAPE, idempotency_key="recon-no-quota"))
        print("configured:", label)
        results["clip.configure"] = {"settings_label": label, "prompt": PROMPT}

        if "--text-and-export" in sys.argv:
            for key in ("timeline.text_overlay", "export.download_mp4"):
                cap = registry.get(key)
                cap.status, cap.ui_fingerprint = CapabilityStatus.VERIFIED, fingerprint
            await adapter.manager.page()  # close the AI side sheet by reopening the Vid
            await adapter.open_project(project)
            rendered = await adapter.add_text_overlay(project, "vidsprod adapter text")
            print("text overlay rendered:", rendered)
            results["timeline.text_overlay"] = {"rendered": rendered}
            exported = await adapter.export_video(project, ExportSpec(idempotency_key="recon-export-0001"),
                                                  RECON / TODAY / "export")
            check = await adapter.verify_export(exported.path)
            print("export:", exported.path, check)
            if not check["decode_ok"] or not check["probe"]["video_codec"]:
                raise SystemExit("export did not verify")
            results["export.download_mp4"] = {"file": str(exported.path.relative_to(RECON.parent)),
                                              "probe": check["probe"]}
    finally:
        work = sorted((RECON / TODAY / "work" / "browser").rglob("*.png")) if (RECON / TODAY / "work").exists() else []
        shots = copy_evidence(work)
        await adapter.close()

    for key in results:
        results[key]["evidence"] = results[key].get("evidence", []) + shots
    caps_file = RECON / "capabilities.json"
    existing = json.loads(caps_file.read_text()).get("capabilities", []) if caps_file.exists() else []
    kept = [c for c in existing if c["key"] not in results]
    for key, data in results.items():
        kept.append({"key": key, "status": "VERIFIED", "verified_on": TODAY,
                     "ui_fingerprint": fingerprint, "evidence": data["evidence"],
                     "notes": json.dumps({k: v for k, v in data.items() if k != "evidence"})[:500]})
    caps_file.write_text(json.dumps({"capabilities": kept}, indent=1))
    raw = json.loads((RECON / "locators.json").read_text())
    raw["ui_fingerprint"] = fingerprint
    (RECON / "locators.json").write_text(json.dumps(raw, indent=1))
    print("verified:", ", ".join(results), "| fingerprint", fingerprint)
    return 0


sys.exit(asyncio.run(main()))
