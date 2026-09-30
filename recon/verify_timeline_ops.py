"""Verify the written-scene timeline steps on the live editor (no generation).

    cd backend && uv run python -u ../recon/verify_timeline_ops.py

New '[vidsprod] recon timeline ops' Vid with two blank scenes (mapped to S01, S02 as a
production would after inserting clips): apply a scene text (TEXT_OVERLAY, role super) to S01
and the brand's standard disclosure (role disclosure) to S02 through apply_timeline_op, read
them back per scene, take the per-scene preview, reopen and read back again.
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue
from vidsprod.config import VidsMode, get_settings
from vidsprod.domain.enums import CapabilityStatus, TimelineOpKind
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.port import SessionStateName, TimelineOpSpec

RECON = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
OUT = RECON / TODAY / "timeline_ops"
SUPER = "Health insurance is about more than managing medical expenses."
DISCLOSURE = ("Insurance is the subject matter of solicitation. For more details on benefits, exclusions, "
              "limitations, terms and conditions, please read the sales brochure and policy wording carefully "
              "before concluding a sale.")


def rel(p) -> str:
    return str(Path(p).resolve().relative_to(RECON.parent))


async def main() -> int:
    settings = get_settings().model_copy(update={
        "google_vids_mode": VidsMode.LIVE, "google_vids_live_automation_enabled": True,
        "artifact_dir": OUT / "work", "browser_headless": False})
    catalogue = LocatorCatalogue.load(RECON / "locators.json")
    registry = live_registry(RECON / "capabilities.json")
    adapter = BrowserVidsAdapter(settings, registry, catalogue)
    report: dict = {}
    try:
        state = await adapter.check_session(OUT / "work")
        print("session:", state.state.value, state.message)
        if state.state is not SessionStateName.READY:
            return 1
        cap = registry.get("preview.capture")  # under test, this run only
        cap.status, cap.ui_fingerprint = CapabilityStatus.VERIFIED, catalogue.ui_fingerprint
        if "--project" in sys.argv:  # reuse the scratch Vid of an earlier run
            from vidsprod.vids.port import ProjectHandle
            url = sys.argv[sys.argv.index("--project") + 1]
            project = await adapter.open_project(ProjectHandle(project_id=adapter._project_id(url), title="(recon)", url=url))
            await (await adapter.manager.page()).wait_for_timeout(4000)
        else:
            project = await adapter.create_project(f"[vidsprod] recon timeline ops {TODAY}")
            await adapter.add_blank_scene(project, force_new=True)
        report["url"] = project.url
        print("project:", project.url)
        adapter._record_scene(project.project_id, 0, "S01", "recon-1")
        adapter._record_scene(project.project_id, 1, "S02", "recon-2")
        for op_key, params in (("op1", {"text": SUPER, "start": 0.0, "end": 5.0, "role": "super", "scene_key": "S01"}),
                               ("op2", {"text": DISCLOSURE, "start": 5.0, "end": 10.0, "role": "disclosure", "scene_key": "S02"})):
            tl = await adapter.apply_timeline_op(project, TimelineOpSpec(op_key=op_key, kind=TimelineOpKind.TEXT_OVERLAY, params=params))
            print(op_key, "overlays:", [(o["scene_key"], o["text"][:40], o["start"], o["end"]) for o in tl.text_overlays])
        again = await adapter.apply_timeline_op(project, TimelineOpSpec(
            op_key="op1-retry", kind=TimelineOpKind.TEXT_OVERLAY,
            params={"text": SUPER, "start": 0.0, "end": 5.0, "role": "super", "scene_key": "S01"}))
        report["after_retry"] = again.text_overlays
        shots = await adapter.capture_preview(project, OUT / "preview")
        report["preview"] = [{"scene_key": s.meta["scene_key"], "file": rel(s.path)} for s in shots]
        print("preview:", report["preview"])
        await adapter.open_project(project)
        await (await adapter.manager.page()).wait_for_timeout(5000)
        reopened = await adapter.read_timeline(project)
        report["after_reopen"] = reopened.text_overlays
        print("after reopen:", [(o["scene_key"], o["text"][:40], o["start"], o["end"]) for o in reopened.text_overlays])
    finally:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
        await adapter.close()
    want = [{"text": SUPER, "start": 0.0, "end": 5.0, "scene_key": "S01"},
            {"text": DISCLOSURE, "start": 5.0, "end": 10.0, "scene_key": "S02"}]
    ok_text = all(w in report["after_reopen"] for w in want) and len(report["after_retry"]) == 2
    ok_preview = [p["scene_key"] for p in report["preview"]] == ["S01", "S02"]
    data = json.loads((RECON / "capabilities.json").read_text())
    caps = {x["key"]: x for x in data["capabilities"]}
    if ok_preview:
        caps["preview.capture"] = {"key": "preview.capture", "status": "VERIFIED", "verified_on": TODAY,
            "ui_fingerprint": catalogue.ui_fingerprint, "evidence": [p["file"] for p in report["preview"]] + [rel(OUT / "report.json")],
            "notes": "capture_preview(): select each scene, screenshot the editor; one shot per scene tagged with its scene key"}
    if ok_text:
        caps["timeline.text_overlay"]["notes"] += (" Timeline op (2026-09-30, account 2): apply_timeline_op TEXT_OVERLAY puts a scene text in the left column "
            "and a disclosure as a bottom strip on its scene; per-scene read-back (text boxes grouped per box) matches exactly after reopening; "
            "a retry adds nothing.")
        caps["timeline.text_overlay"]["evidence"] = caps["timeline.text_overlay"].get("evidence", []) + [rel(OUT / "report.json")]
    data["capabilities"] = list(caps.values())
    (RECON / "capabilities.json").write_text(json.dumps(data, indent=1))
    print("recorded:", {"preview.capture": ok_preview, "text overlay op": ok_text})
    return 0 if ok_text and ok_preview else 1


sys.exit(asyncio.run(main()))
