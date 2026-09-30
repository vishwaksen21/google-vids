"""The mock Google Vids adapter.

Deterministic simulation of a Vids project for development and tests. It renders real
MP4 files with ffmpeg so that every downstream check (probing, frame extraction, black and
freeze detection, loudness, full decode) runs on actual media. Defects, errors, session
problems and crashes are injected from a named `MockScenario`.

It behaves like the real thing where that matters for correctness: actions are **not**
idempotent (calling generate twice makes two clips and spends two generations), state
persists on disk across process restarts, and a timeline edit can silently fail to
persist. The workflow must cope with all of that by itself.

Nothing produced here is presented as real output: every clip, export and capability is
marked `simulated`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vidsprod.domain.enums import ArtifactKind, AspectRatio, IntentKind, TimelineOpKind
from vidsprod.errors import (
    GenerationRefusedError,
    GenerationTimeoutError,
    ProtectedProjectError,
    QuotaExhaustedError,
    VidsError,
)
from vidsprod.media import ffmpeg
from vidsprod.vids.capabilities import CapabilityRegistry, mock_registry
from vidsprod.vids.port import (
    ClipCandidate,
    ClipEvidence,
    ClipRequest,
    EvidenceFile,
    ExportResult,
    ExportSpec,
    ProjectHandle,
    ReconcileResult,
    ReconcileStatus,
    SessionState,
    SessionStateName,
    TimelineItem,
    TimelineOpSpec,
    TimelineState,
)


class SimulatedCrash(BaseException):  # noqa: N818 - models process death, not an error
    """Raised to simulate the process dying mid-action. BaseException on purpose, so that
    no `except Exception` in the workflow can swallow it."""


class MockScenario(BaseModel):
    name: str = "clean"
    # scene_key -> defect per generated take (index 0 = take 1). Beyond the list: clean.
    clip_defects: dict[str, list[str | None]] = Field(default_factory=dict)
    # scene_key -> defect per edit (index 0 = first edit of that scene).
    edit_defects: dict[str, list[str | None]] = Field(default_factory=dict)
    # scene_key -> {take_no: "TIMEOUT" | "REFUSED"}
    generate_errors: dict[str, dict[int, str]] = Field(default_factory=dict)
    session: str = "READY"  # READY | EXPIRED_ONCE | SIGNED_OUT | UI_CHANGED
    export_defects: list[str | None] = Field(default_factory=list)  # TRUNCATED | SILENT
    timeline_op_failures: dict[str, int] = Field(default_factory=dict)  # kind -> times
    crash_after: list[str] = Field(default_factory=list)  # "GENERATE_CLIP:S02:1"
    quota_limit: int | None = None


BUILTIN_SCENARIOS: dict[str, MockScenario] = {
    "clean": MockScenario(name="clean"),
    # One scene needs regenerating, one needs a minor edit, everything else passes.
    "realistic": MockScenario(
        name="realistic",
        clip_defects={"S02": ["BLACK_FRAMES"], "S03": ["VISUAL_MINOR"]},
    ),
    # Persistent failure on one scene, a lost timeline edit and a truncated first export.
    "stress": MockScenario(
        name="stress",
        clip_defects={"S01": ["VISUAL_MAJOR", "VISUAL_MAJOR", "VISUAL_MAJOR", "VISUAL_MAJOR"],
                      "S02": ["INVENTED_SPEECH"]},
        timeline_op_failures={"TEXT_OVERLAY": 1},
        export_defects=["TRUNCATED"],
    ),
}

_SIZES = {AspectRatio.LANDSCAPE: (640, 360), AspectRatio.PORTRAIT: (360, 640)}
_SCENE_COLOURS = ("0x8A9094", "0x666D71", "0x9AA0A4", "0x7B8286", "0xB3B8BB", "0x5A6064")


class MockVidsAdapter:
    name = "mock"
    simulated = True

    def __init__(
        self, state_dir: Path, *, scenario: MockScenario | None = None,
        logo_path: Path | None = None, account_key: str = "mock-account",
        render_width: int | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        # A private copy: one-shot errors are consumed as they fire.
        self.scenario = (scenario or BUILTIN_SCENARIOS["clean"]).model_copy(deep=True)
        self.logo_path = logo_path
        self.account_key = account_key
        self.capabilities: CapabilityRegistry = mock_registry()
        self._lock = asyncio.Lock()
        self._render_width = render_width
        self._global = self._load_global()

    # ------------------------------------------------------------------ persistence
    def _global_path(self) -> Path:
        return self.state_dir / "adapter.json"

    def _load_global(self) -> dict[str, Any]:
        path = self._global_path()
        if path.exists():
            return json.loads(path.read_text())
        return {"session_checks": 0, "generations": 0, "op_failures": {}, "exports": {},
                "projects": {}}

    def _save_global(self) -> None:
        tmp = self._global_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(self._global, indent=1, default=str))
        tmp.replace(self._global_path())

    def _project_dir(self, project_id: str) -> Path:
        return self.state_dir / project_id

    def _load(self, project_id: str) -> dict[str, Any]:
        path = self._project_dir(project_id) / "state.json"
        if not path.exists():
            raise VidsError(f"mock project {project_id} does not exist")
        return json.loads(path.read_text())

    def _save(self, state: dict[str, Any]) -> None:
        path = self._project_dir(state["project_id"]) / "state.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, default=str))
        tmp.replace(path)

    def _record_intent(self, state: dict[str, Any], key: str, result: dict[str, Any]) -> None:
        state.setdefault("intents", {})[key] = result

    def _maybe_crash(self, kind: str, target: str) -> None:
        marker = f"{kind}:{target}"
        done = self._global.setdefault("crashed", [])
        if marker in self.scenario.crash_after and marker not in done:
            done.append(marker)
            self._save_global()
            raise SimulatedCrash(marker)

    # ------------------------------------------------------------------ session
    async def check_session(self, evidence_dir: Path) -> SessionState:
        self._global["session_checks"] += 1
        self._save_global()
        mode = self.scenario.session
        if mode == "SIGNED_OUT" or (mode == "EXPIRED_ONCE" and self._global["session_checks"] == 1):
            return SessionState(state=SessionStateName.SIGNED_OUT, simulated=True,
                                message="Simulated: the session has expired; sign in again")
        if mode == "UI_CHANGED":
            return SessionState(state=SessionStateName.UI_CHANGED, authenticated=True,
                                account_label="mock***", ui_fingerprint="mock-ui-v2",
                                fingerprint_matches=False, simulated=True,
                                message="Simulated: the editor UI differs from the verified one")
        return SessionState(state=SessionStateName.READY, authenticated=True,
                            account_confirmed=True, account_label="mock***", ui_fingerprint="mock-ui-v1",
                            fingerprint_matches=True, simulated=True,
                            message="Simulated Vids session")

    # ------------------------------------------------------------------ project
    async def open_or_create_project(
        self, title: str, existing: ProjectHandle | None, idempotency_key: str
    ) -> ProjectHandle:
        async with self._lock:
            if existing is not None:
                state = self._load(existing.project_id)
                if not state["title"].startswith("[vidsprod]"):
                    raise ProtectedProjectError("refusing to open a project this system did not create")
                return existing
            project_id = f"mockvid-{len(self._global['projects']) + 1:04d}"
            self._project_dir(project_id).mkdir(parents=True, exist_ok=True)
            state = {"project_id": project_id, "title": title, "clips": {}, "items": [],
                     "transitions": [], "text_overlays": [], "logos": [], "voiceover": [],
                     "music": [], "captions_enabled": False, "applied_ops": [], "intents": {}}
            handle = ProjectHandle(project_id=project_id, title=title,
                                   url=f"mock://vids/{project_id}", simulated=True)
            self._record_intent(state, idempotency_key, handle.model_dump(mode="json"))
            self._save(state)
            self._global["projects"][project_id] = title
            self._save_global()
            return handle

    # ------------------------------------------------------------------ references
    async def upload_reference(self, project: ProjectHandle, path: Path, label: str) -> dict[str, Any]:
        async with self._lock:
            state = self._load(project.project_id)
            if not path.exists():
                raise VidsError(f"reference {path.name} does not exist")
            target = self._project_dir(project.project_id) / f"ingredient-{label}{path.suffix}"
            shutil.copyfile(path, target)
            state.setdefault("ingredients", []).append({"label": label, "file": target.name})
            self._save(state)
            return {"label": label, "file": target.name, "simulated": True}

    # ------------------------------------------------------------------ clips
    async def generate_clip(self, project: ProjectHandle, request: ClipRequest) -> ClipCandidate:
        async with self._lock:
            state = self._load(project.project_id)
            error = self.scenario.generate_errors.get(request.scene_key, {}).get(request.take_no)
            if error == "TIMEOUT":
                self._consume_error(request.scene_key, request.take_no)
                raise GenerationTimeoutError(f"simulated generation timeout for {request.scene_key}")
            if error == "REFUSED":
                self._consume_error(request.scene_key, request.take_no)
                raise GenerationRefusedError("simulated: Vids declined this prompt")
            if (self.scenario.quota_limit is not None
                    and self._global["generations"] >= self.scenario.quota_limit):
                raise QuotaExhaustedError("simulated: monthly generation quota exhausted")
            defects = self.scenario.clip_defects.get(request.scene_key, [])
            defect = defects[request.take_no - 1] if request.take_no - 1 < len(defects) else None
            self._global["generations"] += 1
            self._save_global()
            clip_id = f"clip-{request.scene_key}-t{request.take_no}-{len(state['clips']) + 1}"
            path = self._project_dir(project.project_id) / f"{clip_id}.mp4"
            duration = request.duration_seconds * (0.5 if defect == "WRONG_DURATION" else 1.0)
            await self._render_clip(path, request.scene_key, f"take {request.take_no}", duration,
                                    request.aspect_ratio, defect)
            transcript = " ".join(request.spoken_lines)
            if defect == "INVENTED_SPEECH":
                transcript = f"{transcript} and call us now for a free gift".strip()
            state["clips"][clip_id] = {
                "path": path.name, "scene_key": request.scene_key, "take_no": request.take_no,
                "duration": duration, "defect": defect, "transcript": transcript or "NO_SPEECH",
                "aspect_ratio": request.aspect_ratio.value, "prompt": request.prompt,
                "ingredients": [p.name for p in request.ingredient_paths],
            }
            candidate = ClipCandidate(
                clip_id=clip_id, scene_key=request.scene_key, take_no=request.take_no,
                requested_duration_seconds=request.duration_seconds,
                locator={"panel_index": len(state["clips"])}, simulated=True,
                idempotency_key=request.idempotency_key,
            )
            self._record_intent(state, request.idempotency_key, candidate.model_dump(mode="json"))
            self._save(state)
        self._maybe_crash("GENERATE_CLIP", f"{request.scene_key}:{request.take_no}")
        return candidate

    def _consume_error(self, scene_key: str, take_no: int) -> None:
        self.scenario.generate_errors.get(scene_key, {}).pop(take_no, None)

    async def edit_clip(
        self, project: ProjectHandle, clip: ClipCandidate, instruction: str, take_no: int,
        idempotency_key: str,
    ) -> ClipCandidate:
        async with self._lock:
            state = self._load(project.project_id)
            parent = state["clips"].get(clip.clip_id)
            if parent is None:
                raise VidsError(f"clip {clip.clip_id} is not in the project")
            edits_so_far = sum(1 for c in state["clips"].values()
                               if c["scene_key"] == clip.scene_key and c.get("edited_from"))
            defects = self.scenario.edit_defects.get(clip.scene_key, [])
            defect = defects[edits_so_far] if edits_so_far < len(defects) else None
            self._global["generations"] += 1  # an Omni edit is assumed to cost a generation
            self._save_global()
            clip_id = f"clip-{clip.scene_key}-t{take_no}-edit-{len(state['clips']) + 1}"
            path = self._project_dir(project.project_id) / f"{clip_id}.mp4"
            await self._render_clip(path, clip.scene_key, f"take {take_no} (edit)",
                                    float(parent["duration"]) if parent["defect"] != "WRONG_DURATION"
                                    else clip.requested_duration_seconds,
                                    AspectRatio(parent["aspect_ratio"]), defect)
            state["clips"][clip_id] = {
                **parent, "path": path.name, "take_no": take_no, "defect": defect,
                "edited_from": clip.clip_id, "instruction": instruction,
                "duration": clip.requested_duration_seconds,
            }
            candidate = ClipCandidate(
                clip_id=clip_id, scene_key=clip.scene_key, take_no=take_no,
                requested_duration_seconds=clip.requested_duration_seconds,
                locator={"panel_index": len(state["clips"])}, simulated=True,
                idempotency_key=idempotency_key,
            )
            self._record_intent(state, idempotency_key, candidate.model_dump(mode="json"))
            self._save(state)
        return candidate

    async def capture_clip(
        self, project: ProjectHandle, clip: ClipCandidate, evidence_dir: Path
    ) -> ClipEvidence:
        state = self._load(project.project_id)
        info = state["clips"].get(clip.clip_id)
        if info is None:
            raise VidsError(f"clip {clip.clip_id} is missing from the project")
        source = self._project_dir(project.project_id) / info["path"]
        evidence_dir.mkdir(parents=True, exist_ok=True)
        media_copy = evidence_dir / source.name
        shutil.copyfile(source, media_copy)
        probe = await ffmpeg.probe(media_copy)
        stamps = ffmpeg.sample_timestamps(probe.duration_seconds or 0, 4)
        frames = await ffmpeg.extract_frames(media_copy, stamps, evidence_dir / "frames")
        audio = await ffmpeg.extract_audio(media_copy, evidence_dir / f"{source.stem}.wav")
        return ClipEvidence(
            clip_id=clip.clip_id, duration_seconds=probe.duration_seconds, width=probe.width,
            height=probe.height, has_audio=probe.has_audio,
            frames=[EvidenceFile(path=f, kind=ArtifactKind.FRAME, mime="image/jpeg",
                                 label=f"frame at {ts:.2f}s", meta={"t": ts})
                    for f, ts in zip(frames, stamps, strict=False)],
            media=EvidenceFile(path=media_copy, kind=ArtifactKind.CLIP, mime="video/mp4",
                               label="simulated clip"),
            audio=EvidenceFile(path=audio, kind=ArtifactKind.CLIP, mime="audio/wav")
            if audio else None,
            simulated=True,
            simulation_hints={"defect": info.get("defect"), "transcript": info.get("transcript")},
        )

    async def insert_clip(
        self, project: ProjectHandle, clip: ClipCandidate, position: int, idempotency_key: str
    ) -> TimelineItem:
        async with self._lock:
            state = self._load(project.project_id)
            info = state["clips"].get(clip.clip_id)
            if info is None:
                raise VidsError(f"clip {clip.clip_id} is missing from the project")
            fp = await ffmpeg.average_hash(self._project_dir(project.project_id) / info["path"])
            item = TimelineItem(
                item_id=f"item-{len(state['items']) + 1}", clip_id=clip.clip_id,
                scene_key=clip.scene_key, position=position,
                duration_seconds=float(info["duration"]), fingerprint=fp,
            )
            items = [i for i in state["items"]]
            items.insert(min(position, len(items)), item.model_dump())
            for index, entry in enumerate(items):
                entry["position"] = index
            state["items"] = items
            self._record_intent(state, idempotency_key, item.model_dump(mode="json"))
            self._save(state)
            return TimelineItem(**next(i for i in items if i["item_id"] == item.item_id))

    # ------------------------------------------------------------------ timeline
    async def read_timeline(self, project: ProjectHandle) -> TimelineState:
        state = self._load(project.project_id)
        items = [TimelineItem(**i) for i in state["items"]]
        return TimelineState(
            items=items, transitions=state["transitions"], text_overlays=state["text_overlays"],
            logos=state["logos"], voiceover=state["voiceover"], music=state["music"],
            captions_enabled=state["captions_enabled"],
            total_duration_seconds=round(sum(i.duration_seconds for i in items), 3),
            applied_ops=state["applied_ops"],
        )

    async def apply_timeline_op(self, project: ProjectHandle, op: TimelineOpSpec) -> TimelineState:
        async with self._lock:
            state = self._load(project.project_id)
            failures = self._global.setdefault("op_failures", {})
            allowed = self.scenario.timeline_op_failures.get(op.kind.value, 0)
            if failures.get(op.kind.value, 0) < allowed:
                failures[op.kind.value] = failures.get(op.kind.value, 0) + 1
                self._save_global()
            else:
                self._apply(state, op)
                state["applied_ops"].append(op.op_key)
                self._save(state)
        return await self.read_timeline(project)

    def _apply(self, state: dict[str, Any], op: TimelineOpSpec) -> None:
        p = op.params
        if op.kind is TimelineOpKind.ARRANGE:
            order = {key: index for index, key in enumerate(p["order"])}
            state["items"].sort(key=lambda i: order.get(i["scene_key"], 999))
            for index, entry in enumerate(state["items"]):
                entry["position"] = index
        elif op.kind is TimelineOpKind.TRANSITION:
            state["transitions"] = [t for t in state["transitions"]
                                    if t["after_scene"] != p["after_scene"]]
            state["transitions"].append(dict(p))
        elif op.kind is TimelineOpKind.TEXT_OVERLAY:
            state["text_overlays"].append(dict(p))
        elif op.kind is TimelineOpKind.LOGO:
            state["logos"].append(dict(p))
        elif op.kind is TimelineOpKind.VOICEOVER:
            state["voiceover"].append(dict(p))
        elif op.kind is TimelineOpKind.MUSIC:
            state["music"] = [dict(p)]
        elif op.kind is TimelineOpKind.CAPTIONS:
            state["captions_enabled"] = bool(p.get("enabled", True))

    async def capture_preview(self, project: ProjectHandle, evidence_dir: Path) -> list[EvidenceFile]:
        state = self._load(project.project_id)
        shots = []
        for item in state["items"]:
            info = state["clips"][item["clip_id"]]
            source = self._project_dir(project.project_id) / info["path"]
            frames = await ffmpeg.extract_frames(source, [min(1.0, float(info["duration"]) / 2)],
                                                 evidence_dir / f"preview_{item['position']:02d}")
            shots += [EvidenceFile(path=f, kind=ArtifactKind.SCREENSHOT, mime="image/jpeg",
                                   label=f"preview of {item['scene_key']}",
                                   meta={"scene_key": item["scene_key"]}) for f in frames]
        return shots

    # ------------------------------------------------------------------ export
    async def export(self, project: ProjectHandle, spec: ExportSpec, out_dir: Path) -> ExportResult:
        state = self._load(project.project_id)
        if not state["items"]:
            raise VidsError("nothing on the timeline to export")
        attempt = self._global["exports"].get(project.project_id, 0)
        self._global["exports"][project.project_id] = attempt + 1
        self._save_global()
        defect = (self.scenario.export_defects[attempt]
                  if attempt < len(self.scenario.export_defects) else None)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{project.project_id}-export-{attempt + 1}.mp4"
        await self._render_export(state, project.project_id, target, defect)
        result = ExportResult(path=target, simulated=True, details={
            "defect": defect,
            "voiceover": [v["text"] for v in state["voiceover"]],
            "text_overlays": [t["text"] for t in state["text_overlays"]],
        })
        self._record_intent(state, spec.idempotency_key, {"path": str(target)})
        self._save(state)
        return result

    async def reconcile(
        self, project: ProjectHandle | None, kind: IntentKind, idempotency_key: str,
        request: dict[str, Any],
    ) -> ReconcileResult:
        if project is None:
            for project_id in self._global["projects"]:
                intents = self._load(project_id).get("intents", {})
                if idempotency_key in intents:
                    return ReconcileResult(status=ReconcileStatus.FOUND,
                                           result=intents[idempotency_key])
            return ReconcileResult(status=ReconcileStatus.NOT_FOUND)
        intents = self._load(project.project_id).get("intents", {})
        if idempotency_key in intents:
            return ReconcileResult(status=ReconcileStatus.FOUND, result=intents[idempotency_key],
                                   note="simulated project records the action")
        return ReconcileResult(status=ReconcileStatus.NOT_FOUND,
                               note="simulated project has no record of the action")

    async def close(self) -> None:
        return None

    # ------------------------------------------------------------------ rendering
    async def _render_clip(
        self, path: Path, scene_key: str, label: str, duration: float, aspect: AspectRatio,
        defect: str | None,
    ) -> None:
        width, height = _SIZES[aspect]
        colour = _SCENE_COLOURS[(int(scene_key[1:]) - 1) % len(_SCENE_COLOURS)]
        # testsrc2 moves continuously, as real footage does, so freeze detection has real
        # motion to see (drawbox positions are evaluated once, so a "moving" box is static).
        # Desaturated and tinted grey to stay inside the brand palette. A FROZEN defect is a
        # flat colour source: a genuinely static picture.
        source = (f"color=c={colour}:s={width}x{height}:d={duration:.3f}:r=24"
                  if defect == "FROZEN" else f"testsrc2=s={width}x{height}:d={duration:.3f}:r=24")
        filters = ["hue=s=0"] if defect != "FROZEN" else []
        font = ffmpeg.font_file()
        if font:
            text = ffmpeg.drawtext_escape(f"SIMULATED {scene_key} {label}")
            filters.append(f"drawtext=fontfile='{font}':text='{text}':fontcolor=white:"
                           f"fontsize={height // 14}:box=1:boxcolor=0x666D71@0.8:"
                           f"x=(w-text_w)/2:y=(h-text_h)/2")
        if defect == "BLACK_FRAMES":
            start = max(0.2, duration * 0.3)
            filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black@1:t=fill:"
                           f"enable='between(t,{start:.2f},{start + min(1.5, duration * 0.4):.2f})'")
        if not filters:
            filters = ["null"]
        audio = ("anullsrc=r=44100:cl=mono" if defect == "SILENT"
                 else "sine=frequency=330:sample_rate=44100,volume=0.9")
        code, err = await ffmpeg.run_ffmpeg(
            "-y", "-f", "lavfi", "-i", source,
            "-f", "lavfi", "-i", audio, "-vf", ",".join(filters), "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path), timeout=120,
        )
        if code != 0:
            raise VidsError(f"mock clip render failed: {err[-400:]}")

    async def _render_export(
        self, state: dict[str, Any], project_id: str, target: Path, defect: str | None
    ) -> None:
        items = sorted(state["items"], key=lambda i: i["position"])
        inputs: list[str] = []
        parts = []
        for index, item in enumerate(items):
            clip = state["clips"][item["clip_id"]]
            inputs += ["-i", str(self._project_dir(project_id) / clip["path"])]
            parts.append(f"[{index}:v]scale=640:-2,setsar=1[v{index}];[{index}:a]aresample=44100[a{index}]")
        n = len(items)
        chain = ";".join(parts) + ";" + "".join(f"[v{i}][a{i}]" for i in range(n))
        chain += f"concat=n={n}:v=1:a=1[vcat][acat]"
        video = "vcat"
        font = ffmpeg.font_file()
        overlays = []
        for text in state["text_overlays"]:
            if font:
                t = ffmpeg.drawtext_escape(text["text"])
                y = "h-th-40" if text.get("position", "lower") == "lower" else "40"
                overlays.append(
                    f"drawtext=fontfile='{font}':text='{t}':fontcolor=white:fontsize=20:"
                    f"box=1:boxcolor=0x3D4144@0.6:x=(w-text_w)/2:y={y}:"
                    f"enable='between(t,{text['start']},{text['end']})'"
                )
        if overlays:
            chain += f";[{video}]{','.join(overlays)}[vtext]"
            video = "vtext"
        logo_inputs: list[str] = []
        if state["logos"] and self.logo_path and self.logo_path.exists():
            logo = state["logos"][-1]
            logo_inputs = ["-i", str(self.logo_path)]
            chain += (f";[{n}:v]scale=120:-1[lg];[{video}][lg]overlay=W-w-16:H-h-16:"
                      f"enable='between(t,{logo['start']},{logo['end']})'[vlogo]")
            video = "vlogo"
        args = ["-y", *inputs, *logo_inputs, "-filter_complex", chain, "-map", f"[{video}]"]
        if defect != "SILENT":
            args += ["-map", "[acat]", "-c:a", "aac"]
        total = sum(float(state["clips"][i["clip_id"]]["duration"]) for i in items)
        if defect == "TRUNCATED":
            args += ["-t", f"{total * 0.5:.3f}"]
        args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(target)]
        code, err = await ffmpeg.run_ffmpeg(*args, timeout=300)
        if code != 0:
            raise VidsError(f"mock export render failed: {err[-600:]}")
