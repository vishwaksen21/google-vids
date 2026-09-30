"""Stages that operate Google Vids: session, project, scene production, timeline, preview,
export, final QA and delivery."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from vidsprod.agents.final_qa import run_final_qa
from vidsprod.agents.inspection import SceneBudget, decide_correction, inspect_clip, steering
from vidsprod.agents.timeline import (
    CAPABILITY_FOR,
    plan_edits,
    scene_windows,
    verify_locked,
    verify_op,
)
from vidsprod.automation.google_vids.recovery import raise_if_wrong
from vidsprod.brand.projections import BrandStage, project
from vidsprod.db.models import (
    ApprovalRequest,
    Artifact,
    BrowserSession,
    Delivery,
    Export,
    ProductionJob,
    Scene,
    SceneTake,
    SceneVersion,
    TimelineOperation,
)
from vidsprod.domain.enums import (
    ApprovalKind,
    ApprovalStatus,
    ArtifactKind,
    AspectRatio,
    CorrectionAction,
    DeliveryStatus,
    ExportStatus,
    IntentKind,
    RuleCategory,
    SceneStatus,
    Severity,
    StageId,
    TakeOrigin,
    TakeStatus,
    TimelineOpKind,
)
from vidsprod.domain.validation import RuleResult, failed, not_run, passed
from vidsprod.errors import (
    BudgetExhaustedError,
    CapabilityUnavailableError,
    GenerationRefusedError,
    QuotaExhaustedError,
    VidsError,
)
from vidsprod.media import ffmpeg
from vidsprod.storage.artifacts import sha256_file
from vidsprod.vids.port import (
    ClipCandidate,
    ClipRequest,
    ExportSpec,
    ProjectHandle,
    TimelineOpSpec,
)
from vidsprod.workflow import audit, budget
from vidsprod.workflow.context import ApprovalSpec, StageContext, StageOutcome
from vidsprod.workflow.engine import now, persist_results
from vidsprod.workflow.intents import BudgetClaim

C = RuleCategory


def _project(ctx: StageContext) -> ProjectHandle:
    ref = (ctx.job.vids_project_ref or {}).get("project")
    if not ref:
        raise VidsError("the job has no Vids project yet")
    return ProjectHandle(**ref)


async def _refresh_job(ctx: StageContext) -> None:
    async with ctx.s.db.session() as session:
        ctx.job = await session.get(ProductionJob, ctx.job.id)


async def locked_takes(ctx: StageContext) -> dict[str, dict[str, Any]]:
    async with ctx.s.db.session() as session:
        rows = (await session.execute(
            select(Scene.scene_key, SceneTake).join(SceneTake, SceneTake.id == Scene.locked_take_id)
            .where(Scene.job_id == ctx.job_id))).all()
    return {key: {"clip_id": take.clip_ref["clip_id"], "take_id": str(take.id),
                  "fingerprint": (take.timeline_item_ref or {}).get("fingerprint")}
            for key, take in rows}


async def latest_verified_export(ctx: StageContext) -> tuple[Export, Artifact] | None:
    async with ctx.s.db.session() as session:
        export = await session.scalar(select(Export).where(
            Export.job_id == ctx.job_id, Export.status == ExportStatus.VERIFIED.value)
            .order_by(Export.attempt.desc()).limit(1))
        if export is None:
            return None
        return export, await session.get(Artifact, export.artifact_id)


def _required_capabilities(scene_plan: dict, script: dict, brief: dict) -> list[str]:
    caps = ["clip.generate", "clip.capture_frames", "clip.insert", "timeline.read",
            "export.download_mp4"]
    if script["supers"] or script.get("disclosure_texts"):
        caps.append("timeline.text_overlay")
    if any(x["kind"] == "VOICEOVER" for x in script["lines"]):
        caps.append("timeline.voiceover")
    if brief["output"].get("end_card_required", True):
        caps.append("timeline.logo")
    if brief["output"].get("captions_required", True):
        caps.append("timeline.captions")
    if brief.get("reference_asset_ids"):
        caps.append("clip.ingredients")
    return caps


# ======================================================================= session, project
class VidsSessionStage:
    stage = StageId.VIDS_SESSION
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        adapter = ctx.s.adapter
        state = await adapter.check_session(ctx.work_dir())
        stored = await ctx.store_evidence(state.evidence, "vids_session", adapter.simulated)
        async with ctx.s.db.transaction() as session:
            session.add(BrowserSession(
                adapter=adapter.name, account_key=adapter.account_key, state=state.state.value,
                authenticated=state.authenticated, account_label=state.account_label,
                ui_fingerprint=state.ui_fingerprint, fingerprint_matches=state.fingerprint_matches,
                details={"message": state.message, "simulated": state.simulated},
                evidence_artifact_id=stored[0].id if stored else None,
            ))
        raise_if_wrong(state, ctx.settings)  # each non-ready state → the error the engine acts on
        registry = adapter.capabilities
        needed = _required_capabilities(await ctx.doc("SCENE_PLAN"), await ctx.doc("SCRIPT"),
                                        await ctx.doc("CAMPAIGN_BRIEF"))
        missing = [c for c in needed if not registry.is_verified(c)]
        if missing:
            raise CapabilityUnavailableError(
                "production needs Vids capabilities that are not verified: " + ", ".join(missing),
                details={"missing": missing},
            )
        results = [
            passed("session.ready", C.OPERATIONAL, state.message or "session ready",
                   evidence=[str(a.id) for a in stored]),
            passed("capabilities.preflight", C.OPERATIONAL,
                   f"all {len(needed)} capabilities this plan needs are verified"
                   + (" (simulated adapter)" if registry.simulated else ""),
                   data={"capabilities": needed}),
        ]
        if adapter.simulated:
            results.append(passed("adapter.simulated", C.OPERATIONAL,
                                  "SIMULATION: the mock Vids adapter is in use; nothing is sent "
                                  "to Google Vids", severity=Severity.INFO))
        return StageOutcome.done(results)


class VidsProjectStage:
    stage = StageId.VIDS_PROJECT
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        campaign = await ctx.campaign()
        prefix = ctx.settings.google_vids_project_prefix
        title = f"{prefix} {campaign.name} ({str(ctx.job_id)[:8]})"
        adapter = ctx.s.adapter

        async def on_confirm(session, result: dict) -> None:
            job = await session.get(ProductionJob, ctx.job_id, with_for_update=True)
            job.vids_project_ref = {**(job.vids_project_ref or {}), "project": result}

        result = await ctx.s.intents.run(
            job_id=ctx.job_id, kind=IntentKind.CREATE_PROJECT, target="project",
            request={"title": title},
            action=lambda key: adapter.open_or_create_project(title, None, key),
            on_confirm=on_confirm,
        )
        handle = ProjectHandle(**result)
        reopened = await adapter.open_or_create_project(title, handle, f"{ctx.job_id}:verify-project")
        results = []
        if reopened.project_id != handle.project_id or not reopened.title.startswith(prefix):
            results.append(failed("project.exists", C.OPERATIONAL, Severity.BLOCKER,
                                  "the project could not be reopened as created"))
        else:
            results.append(passed("project.exists", C.OPERATIONAL,
                                  f"project {handle.project_id} exists and reopens",
                                  data={"project_id": handle.project_id, "title": handle.title}))
        await _refresh_job(ctx)
        return StageOutcome.done(results, {"project": result})


# ======================================================================= scene production
class SceneProductionStage:
    """One scene step per call: generate, inspect, decide, then edit / regenerate / accept.

    Accepted scenes are locked and never touched again; only the failing scene is
    regenerated. Scenes are worked in order, but a scene waiting for a person does not stop
    the others from being produced.
    """

    stage = StageId.SCENE_PRODUCTION
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        project = _project(ctx)
        await self._apply_review_decisions(ctx)
        scenes = await self._scenes(ctx)
        await ctx.progress(**_progress(scenes))
        for scene, version, takes in scenes:
            if scene.status in (SceneStatus.ACCEPTED.value, SceneStatus.FAILED.value,
                                SceneStatus.HUMAN_REVIEW.value):
                continue
            await self._advance(ctx, project, scene, version, takes)
            return StageOutcome.proceed(f"{scene.scene_key}: {scene.status}")

        waiting = [(s, v, t) for s, v, t in scenes if s.status == SceneStatus.HUMAN_REVIEW.value]
        if waiting:
            return StageOutcome.wait([self._review_spec(s, t) for s, _, t in waiting],
                                     f"{len(waiting)} scene(s) need review")
        failed_scenes = [s.scene_key for s, _, _ in scenes if s.status == SceneStatus.FAILED.value]
        if failed_scenes:
            return StageOutcome.done([failed("scenes.all_accepted", C.CONTENT, Severity.BLOCKER,
                                             f"scenes rejected by review: {failed_scenes}")])
        results = [passed("scenes.all_accepted", C.CONTENT,
                          f"all {len(scenes)} scenes accepted and locked")]
        summary = []
        for scene, _, takes in scenes:
            gens = sum(1 for t in takes if t.origin == TakeOrigin.GENERATE.value)
            edits = sum(1 for t in takes if t.origin == TakeOrigin.EDIT.value)
            summary.append({"scene_key": scene.scene_key, "generations": gens, "edits": edits,
                            "locked_take_id": str(scene.locked_take_id)})
            results.append(passed("scene.locked", C.OPERATIONAL,
                                  f"{scene.scene_key}: take locked after {gens} generation(s) "
                                  f"and {edits} edit(s)", subject=f"scene:{scene.scene_key}",
                                  evidence=[str(scene.locked_take_id)]))
        return StageOutcome.done(results, {"scenes": summary})

    # ------------------------------------------------------------------ loading
    async def _scenes(self, ctx: StageContext) -> list[tuple[Scene, SceneVersion, list[SceneTake]]]:
        async with ctx.s.db.session() as session:
            scenes = (await session.execute(select(Scene).where(Scene.job_id == ctx.job_id)
                                            .order_by(Scene.order_index))).scalars().all()
            out = []
            for scene in scenes:
                version = await session.get(SceneVersion, scene.current_version_id)
                takes = (await session.execute(select(SceneTake).where(
                    SceneTake.scene_id == scene.id).order_by(SceneTake.take_no))).scalars().all()
                out.append((scene, version, list(takes)))
            return out

    async def _advance(self, ctx, project, scene, version, takes) -> None:
        status = SceneStatus(scene.status)
        latest = takes[-1] if takes else None
        if status is SceneStatus.BLOCKED:
            # Unblocked by a person (budget raised, quota reset): resume where it stopped.
            async with ctx.s.db.transaction() as session:
                row = await session.get(Scene, scene.id, with_for_update=True)
                row.status = row.resume_status or SceneStatus.READY.value
                row.resume_status = None
            return
        if status in (SceneStatus.PLANNED, SceneStatus.READY, SceneStatus.REGENERATION_REQUIRED,
                      SceneStatus.SUBMITTED, SceneStatus.GENERATING):
            await self._blockable(ctx, scene, self._generate(ctx, project, scene, version, takes))
        elif status is SceneStatus.NEEDS_EDIT:
            await self._blockable(ctx, scene, self._edit(ctx, project, scene, version, takes))
        elif status in (SceneStatus.GENERATED, SceneStatus.INSPECTING) and latest is not None:
            if (latest.status == TakeStatus.INSPECTED.value
                    and latest.correction == CorrectionAction.ACCEPT_AND_LOCK.value):
                await self._accept(ctx, project, scene, latest)
            else:
                await self._inspect(ctx, project, scene, version, latest)
        else:
            raise VidsError(f"{scene.scene_key} is {scene.status} with no take to work on")

    async def _blockable(self, ctx, scene: Scene, work) -> None:
        """Run a generation/edit; if the budget or the Vids quota stops it, mark the scene
        BLOCKED (remembering where it was) and let the job wait for a person."""
        try:
            await work
        except (BudgetExhaustedError, QuotaExhaustedError) as exc:
            async with ctx.s.db.transaction() as session:
                row = await session.get(Scene, scene.id, with_for_update=True)
                if row.status != SceneStatus.BLOCKED.value:
                    row.resume_status = (SceneStatus.READY.value
                                         if row.status in (SceneStatus.SUBMITTED.value,
                                                           SceneStatus.GENERATING.value)
                                         else row.status)
                    row.status = SceneStatus.BLOCKED.value
                await audit.record(session, type="scene.blocked", job_id=ctx.job_id,
                                   campaign_id=ctx.job.campaign_id,
                                   message=f"{scene.scene_key}: {exc.message}")
            raise

    # ------------------------------------------------------------------ generate
    async def _generate(self, ctx, project, scene, version, takes) -> None:
        limit = ctx.settings.max_takes_per_scene + scene.extra_takes_granted
        if scene.takes_used >= limit:
            await self._to_review(ctx, scene, f"no takes left ({scene.takes_used} used)")
            return
        gen_no = scene.takes_used + 1
        take_no = (max((t.take_no for t in takes), default=0)) + 1
        prompt = version.prompt
        async with ctx.s.db.transaction() as session:
            row = await session.get(Scene, scene.id, with_for_update=True)
            row.status = SceneStatus.SUBMITTED.value
        ingredients = await self._ingredient_paths(ctx, prompt.get("reference_asset_ids", []))
        request = ClipRequest(
            ingredient_paths=ingredients,
            scene_key=scene.scene_key, take_no=take_no, prompt=prompt["prompt"],
            duration_seconds=prompt["duration_seconds"],
            aspect_ratio=AspectRatio(prompt["aspect_ratio"]),
            spoken_lines=prompt.get("spoken_lines", []), idempotency_key="pending",
        )

        async def on_confirm(session, result: dict) -> None:
            exists = await session.scalar(select(SceneTake.id).where(
                SceneTake.scene_id == scene.id, SceneTake.take_no == result["take_no"]))
            if exists:
                return
            row = await session.get(Scene, scene.id, with_for_update=True)
            session.add(SceneTake(
                scene_id=scene.id, scene_version_id=version.id, take_no=result["take_no"],
                origin=TakeOrigin.GENERATE.value, status=TakeStatus.GENERATED.value,
                prompt_text=prompt["prompt"], clip_ref=result,
                simulated=bool(result.get("simulated")),
            ))
            row.takes_used += 1
            row.status = SceneStatus.GENERATED.value
            await audit.record(session, type="scene.generated", job_id=ctx.job_id,
                               campaign_id=ctx.job.campaign_id,
                               message=f"{scene.scene_key} take {result['take_no']} generated",
                               data={"scene_key": scene.scene_key, "take_no": result["take_no"]})

        try:
            await ctx.s.intents.run(
                job_id=ctx.job_id, kind=IntentKind.GENERATE_CLIP,
                target=f"{scene.scene_key}:gen{gen_no}",
                request=request.model_dump(mode="json", exclude={"ingredient_paths"}),
                action=lambda key: ctx.s.adapter.generate_clip(
                    project, request.model_copy(update={"idempotency_key": key})),
                on_confirm=on_confirm, project=project,
                claim=BudgetClaim(ctx.s.adapter.account_key, ctx.job.campaign_id, scene.id,
                                  f"generate {scene.scene_key}"),
            )
        except GenerationRefusedError as exc:
            await self._to_review(ctx, scene, f"Vids declined the prompt: {exc.message}")

    async def _ingredient_paths(self, ctx, asset_ids: list[str]) -> list[Path]:
        paths = []
        async with ctx.s.db.session() as session:
            for asset_id in asset_ids:
                artifact = await session.get(Artifact, uuid.UUID(asset_id))
                if artifact is None:
                    raise VidsError(f"reference image {asset_id} no longer exists")
                paths.append(ctx.s.store.resolve(artifact.uri))
        return paths

    # ------------------------------------------------------------------ inspect
    async def _inspect(self, ctx, project, scene, version, take) -> None:
        adapter = ctx.s.adapter
        async with ctx.s.db.transaction() as session:
            row = await session.get(Scene, scene.id, with_for_update=True)
            row.status = SceneStatus.INSPECTING.value
        evidence = await adapter.capture_clip(project, ClipCandidate(**take.clip_ref),
                                              ctx.work_dir(scene.scene_key, f"take{take.take_no}"))
        files = [*evidence.frames, *evidence.screenshots]
        if evidence.media:
            files.append(evidence.media)
        stored = await ctx.store_evidence(files, "scene_inspector", adapter.simulated)
        frame_ids = [str(a.id) for a in stored if a.kind in (ArtifactKind.FRAME.value,
                                                            ArtifactKind.SCREENSHOT.value)]
        continuity = await self._continuity_frames(ctx, version.spec.get("depends_on", []))
        brand_block = project_block(ctx)
        inspection = await inspect_clip(
            ai=ctx.s.ai, settings=ctx.settings, registry=adapter.capabilities,
            scene=version.spec, prompt=version.prompt, evidence=evidence,
            brand_block=brand_block, continuity_frames=continuity, job_id=ctx.job_id,
            subject=f"scene:{scene.scene_key}",
        )
        for r in inspection.results:
            if r.judge == "llm" or r.rule_id.startswith("tech."):
                r.evidence = r.evidence or frame_ids
        async with ctx.s.db.session() as session:
            available = await budget.available(session, ctx.settings,
                                               account_key=adapter.account_key,
                                               campaign_id=ctx.job.campaign_id)
        decision = decide_correction(inspection.verdict, SceneBudget(
            takes_used=scene.takes_used, edits_used=scene.edits_used,
            max_takes=ctx.settings.max_takes_per_scene + scene.extra_takes_granted,
            max_edits=ctx.settings.max_edits_per_scene, generations_available=available,
            edit_verified=adapter.capabilities.is_verified("clip.edit_text"),
        ))
        fingerprint = None
        if evidence.media:
            fingerprint = await ffmpeg.average_hash(evidence.media.path)
        async with ctx.s.db.transaction() as session:
            await persist_results(session, ctx.job_id, ctx.attempt.id, inspection.results, take.id)
            row = await session.get(SceneTake, take.id, with_for_update=True)
            row.status = TakeStatus.INSPECTED.value
            row.verdict = inspection.verdict.value
            row.verdict_reasons = inspection.reasons
            row.correction = decision.action.value
            row.evidence = [str(a.id) for a in stored]
            row.fingerprint = {"ahash": fingerprint} if fingerprint else None
            s = await session.get(Scene, scene.id, with_for_update=True)
            if decision.action is CorrectionAction.EDIT:
                s.status = SceneStatus.NEEDS_EDIT.value
                s.pending_instruction = inspection.edit_instruction
            elif decision.action is CorrectionAction.REGENERATE:
                row.status = TakeStatus.REJECTED.value
                s.status = SceneStatus.REGENERATION_REQUIRED.value
                await self._steered_version(session, s, version, steering(inspection.results))
            elif decision.action is CorrectionAction.HUMAN_REVIEW:
                s.status = SceneStatus.HUMAN_REVIEW.value
            await audit.record(
                session, type="scene.inspected", job_id=ctx.job_id, campaign_id=ctx.job.campaign_id,
                message=f"{scene.scene_key} take {take.take_no}: {inspection.verdict.value} → "
                        f"{decision.action.value} ({decision.reason})",
                data={"scene_key": scene.scene_key, "take_no": take.take_no,
                      "verdict": inspection.verdict.value, "action": decision.action.value,
                      "reasons": inspection.reasons[:5]},
            )
        if decision.action is CorrectionAction.ACCEPT_AND_LOCK:
            async with ctx.s.db.session() as session:
                take = await session.get(SceneTake, take.id)
            await self._accept(ctx, project, scene, take)

    async def _steered_version(self, session, scene: Scene, version: SceneVersion,
                               lines: list[str], reason: str = "regeneration") -> None:
        prompt = dict(version.prompt)
        base = prompt["prompt"].split("\n\nMake sure that:")[0]
        if lines:
            prompt["prompt"] = base + "\n\nMake sure that:\n" + "\n".join(f"- {x}" for x in lines)
        from vidsprod.workflow.context import content_hash

        n = (await session.scalar(select(func.max(SceneVersion.version)).where(
            SceneVersion.scene_id == scene.id)) or 0) + 1
        sv = SceneVersion(scene_id=scene.id, version=n, spec=version.spec, prompt=prompt,
                          prompt_hash=content_hash(prompt),
                          reason=f"{reason}: " + ("; ".join(lines) if lines else "same prompt"))
        session.add(sv)
        await session.flush()
        scene.current_version_id = sv.id

    async def _continuity_frames(self, ctx, depends_on: list[str]) -> list[tuple[str, Path]]:
        out = []
        async with ctx.s.db.session() as session:
            for key in depends_on:
                scene = await session.scalar(select(Scene).where(Scene.job_id == ctx.job_id,
                                                                 Scene.scene_key == key))
                if scene is None or scene.locked_take_id is None:
                    continue
                take = await session.get(SceneTake, scene.locked_take_id)
                for artifact_id in take.evidence:
                    artifact = await session.get(Artifact, uuid.UUID(artifact_id))
                    if artifact and artifact.kind in (ArtifactKind.FRAME.value,
                                                      ArtifactKind.SCREENSHOT.value):
                        out.append((key, ctx.s.store.resolve(artifact.uri)))
                        break
        return out

    # ------------------------------------------------------------------ edit
    async def _edit(self, ctx, project, scene, version, takes) -> None:
        parent = next((t for t in reversed(takes) if t.status == TakeStatus.INSPECTED.value), None)
        if parent is None:
            raise VidsError(f"{scene.scene_key} has no inspected take to edit")
        instruction = scene.pending_instruction or "Improve the clip to match the prompt"
        take_no = max(t.take_no for t in takes) + 1
        edit_no = scene.edits_used + 1

        async def on_confirm(session, result: dict) -> None:
            exists = await session.scalar(select(SceneTake.id).where(
                SceneTake.scene_id == scene.id, SceneTake.take_no == result["take_no"]))
            if exists:
                return
            row = await session.get(Scene, scene.id, with_for_update=True)
            p = await session.get(SceneTake, parent.id, with_for_update=True)
            p.status = TakeStatus.REJECTED.value
            session.add(SceneTake(
                scene_id=scene.id, scene_version_id=version.id, parent_take_id=parent.id,
                take_no=result["take_no"], origin=TakeOrigin.EDIT.value,
                status=TakeStatus.GENERATED.value, prompt_text=parent.prompt_text,
                edit_instruction=instruction, clip_ref=result,
                simulated=bool(result.get("simulated")),
            ))
            row.edits_used += 1
            row.pending_instruction = None
            row.status = SceneStatus.GENERATED.value
            await audit.record(session, type="scene.edited", job_id=ctx.job_id,
                               campaign_id=ctx.job.campaign_id,
                               message=f"{scene.scene_key}: edit applied to take {parent.take_no}",
                               data={"instruction": instruction})

        await ctx.s.intents.run(
            job_id=ctx.job_id, kind=IntentKind.EDIT_CLIP, target=f"{scene.scene_key}:edit{edit_no}",
            request={"parent": parent.clip_ref["clip_id"], "instruction": instruction},
            action=lambda key: ctx.s.adapter.edit_clip(
                project, ClipCandidate(**parent.clip_ref), instruction, take_no, key),
            on_confirm=on_confirm, project=project,
            claim=BudgetClaim(ctx.s.adapter.account_key, ctx.job.campaign_id, scene.id,
                              f"edit {scene.scene_key}"),
        )

    # ------------------------------------------------------------------ accept
    async def _accept(self, ctx, project, scene, take) -> None:
        async with ctx.s.db.session() as session:
            position = await session.scalar(select(func.count()).select_from(Scene).where(
                Scene.job_id == ctx.job_id, Scene.status == SceneStatus.ACCEPTED.value,
                Scene.order_index < scene.order_index))

        async def on_confirm(session, result: dict) -> None:
            row = await session.get(SceneTake, take.id, with_for_update=True)
            if row.locked_at is not None:
                return
            row.status = TakeStatus.ACCEPTED.value
            row.locked_at = now()
            row.timeline_item_ref = result
            s = await session.get(Scene, scene.id, with_for_update=True)
            s.status = SceneStatus.ACCEPTED.value
            s.locked_take_id = take.id
            await audit.record(session, type="scene.accepted", job_id=ctx.job_id,
                               campaign_id=ctx.job.campaign_id,
                               message=f"{scene.scene_key} take {take.take_no} accepted and locked",
                               data={"scene_key": scene.scene_key, "take_id": str(take.id)})

        await ctx.s.intents.run(
            job_id=ctx.job_id, kind=IntentKind.INSERT_CLIP,
            target=f"{scene.scene_key}:take{take.take_no}",
            request={"clip_id": take.clip_ref["clip_id"], "position": position},
            action=lambda key: ctx.s.adapter.insert_clip(
                project, ClipCandidate(**take.clip_ref), position or 0, key),
            on_confirm=on_confirm, project=project,
        )

    # ------------------------------------------------------------------ human review
    async def _to_review(self, ctx, scene: Scene, reason: str) -> None:
        async with ctx.s.db.transaction() as session:
            s = await session.get(Scene, scene.id, with_for_update=True)
            s.status = SceneStatus.HUMAN_REVIEW.value
            await audit.record(session, type="scene.needs_review", job_id=ctx.job_id,
                               campaign_id=ctx.job.campaign_id,
                               message=f"{scene.scene_key}: {reason}", data={"reason": reason})

    def _review_spec(self, scene: Scene, takes: list[SceneTake]) -> ApprovalSpec:
        latest = takes[-1] if takes else None
        return ApprovalSpec(
            kind=ApprovalKind.SCENE_REVIEW,
            subject=f"scene:{scene.scene_key}:take{latest.take_no if latest else 0}",
            title=f"Review scene {scene.scene_key}",
            summary=("; ".join((latest.verdict_reasons or [])[:3]) if latest else
                     "no usable take was produced") or "automatic inspection could not decide",
            payload={"scene_id": str(scene.id), "scene_key": scene.scene_key,
                     "take_id": str(latest.id) if latest else None,
                     "evidence": latest.evidence if latest else [],
                     "takes_used": scene.takes_used, "edits_used": scene.edits_used},
            options=["accept", "regenerate", "edit", "reject"],
        )

    async def _apply_review_decisions(self, ctx) -> None:
        for approval in await ctx.approvals():
            if (approval.kind != ApprovalKind.SCENE_REVIEW.value
                    or approval.status == ApprovalStatus.PENDING.value
                    or (approval.decision or {}).get("applied")):
                continue
            decision = approval.decision or {}
            async with ctx.s.db.transaction() as session:
                a = await session.get(ApprovalRequest, approval.id, with_for_update=True)
                s = await session.get(Scene, uuid.UUID(approval.payload["scene_id"]), with_for_update=True)
                take_id = approval.payload.get("take_id")
                take = await session.get(SceneTake, uuid.UUID(take_id)) if take_id else None
                action = decision.get("action") if approval.status == ApprovalStatus.APPROVED.value else "reject"
                if action == "accept" and take is not None:
                    take_row = await session.get(SceneTake, take.id, with_for_update=True)
                    take_row.status = TakeStatus.INSPECTED.value
                    take_row.correction = CorrectionAction.ACCEPT_AND_LOCK.value
                    s.status = SceneStatus.INSPECTING.value
                    await persist_results(session, ctx.job_id, ctx.attempt.id, [passed(
                        "review.human_accept", C.CONTENT,
                        f"{s.scene_key} accepted by {approval.decided_by}: {decision.get('reason', '')}",
                        judge="human", subject=f"scene:{s.scene_key}")], take.id)
                elif action == "regenerate":
                    s.extra_takes_granted += 1
                    s.status = SceneStatus.REGENERATION_REQUIRED.value
                    version = await session.get(SceneVersion, s.current_version_id)
                    extra = [decision["instruction"]] if decision.get("instruction") else []
                    await self._steered_version(session, s, version, extra, "human-requested regeneration")
                elif action == "edit" and take is not None:
                    s.status = SceneStatus.NEEDS_EDIT.value
                    s.pending_instruction = decision["instruction"]
                    take_row = await session.get(SceneTake, take.id, with_for_update=True)
                    if take_row.status != TakeStatus.INSPECTED.value:
                        take_row.status = TakeStatus.INSPECTED.value
                else:
                    s.status = SceneStatus.FAILED.value
                a.decision = {**decision, "applied": True}
                await audit.record(session, type="scene.review_applied", actor=approval.decided_by or "human",
                                   job_id=ctx.job_id, campaign_id=ctx.job.campaign_id,
                                   message=f"{s.scene_key}: {action}")


def project_block(ctx: StageContext) -> str:
    return project(ctx.s.brand, BrandStage.VALIDATION).as_prompt_block()


def _progress(scenes) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for scene, _, _ in scenes:
        counts[scene.status] = counts.get(scene.status, 0) + 1
    active = next((s.scene_key for s, _, _ in scenes
                   if s.status not in (SceneStatus.ACCEPTED.value, SceneStatus.FAILED.value)), None)
    return {"scene_counts": counts, "active_scene": active, "scene_total": len(scenes)}


# ======================================================================= timeline
class TimelineAssemblyStage:
    stage = StageId.TIMELINE_ASSEMBLY
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        project_ = _project(ctx)
        adapter = ctx.s.adapter
        scene_plan = await ctx.doc("SCENE_PLAN")
        planned = [s["scene_key"] for s in scene_plan["scenes"]]
        locked = await locked_takes(ctx)
        state = await adapter.read_timeline(project_)
        order = [i.scene_key for i in sorted(state.items, key=lambda i: i.position)]
        results: list[RuleResult] = []
        if order != planned and sorted(order) == sorted(planned):
            spec = TimelineOpSpec(op_key="arrange", kind=TimelineOpKind.ARRANGE,
                                  params={"order": planned})

            async def noop(session, result):  # the read-back below is the verification
                return None

            await ctx.s.intents.run(
                job_id=ctx.job_id, kind=IntentKind.TIMELINE_OP,
                target=f"arrange:{ctx.attempt.attempt}", request=spec.model_dump(mode="json"),
                action=lambda key: adapter.apply_timeline_op(project_, spec),
                on_confirm=noop, project=project_,
            )
            state = await adapter.read_timeline(project_)
            order = [i.scene_key for i in sorted(state.items, key=lambda i: i.position)]
            results.append(passed("timeline.arranged", C.OPERATIONAL,
                                  "scenes were reordered to the plan"))
        if order != planned:
            results.append(failed("timeline.order", C.OPERATIONAL, Severity.MAJOR,
                                  f"timeline order {order} does not match the plan {planned}"))
        else:
            results.append(passed("timeline.order", C.OPERATIONAL,
                                  f"{len(order)} scenes in planned order"))
        results.append(verify_locked(state, locked, "timeline"))
        durations = {s["scene_key"]: s["duration_seconds"] for s in scene_plan["scenes"]}
        off = [f"{i.scene_key} {i.duration_seconds:.1f}s (planned {durations.get(i.scene_key)}s)"
               for i in state.items
               if abs(i.duration_seconds - durations.get(i.scene_key, i.duration_seconds))
               > ctx.settings.duration_tolerance_seconds]
        if off:
            results.append(failed("timeline.durations", C.TECHNICAL, Severity.MINOR,
                                  "scene durations differ from the plan: " + "; ".join(off)))
        else:
            results.append(passed("timeline.durations", C.TECHNICAL,
                                  f"total {state.total_duration_seconds:.1f}s, every scene as planned"))
        await ctx.save_doc("TIMELINE_STATE", state.model_dump(mode="json"))
        return StageOutcome.done(results, {"total_duration_seconds": state.total_duration_seconds})


class TimelineEditsStage:
    """Applies the edit plan one operation per step, verifying each by read-back and
    re-checking the locked scenes after every one."""

    stage = StageId.TIMELINE_EDITS
    auto_retry = True
    MAX_OP_ATTEMPTS = 3

    async def run(self, ctx: StageContext) -> StageOutcome:
        project_ = _project(ctx)
        adapter = ctx.s.adapter
        async with ctx.s.db.session() as session:
            ops = (await session.execute(select(TimelineOperation).where(
                TimelineOperation.job_id == ctx.job_id).order_by(TimelineOperation.seq))).scalars().all()
        if not ops:
            return await self._plan(ctx, project_)
        pending = next((o for o in ops if o.status == "PENDING"), None)
        await ctx.progress(ops_total=len(ops),
                           ops_verified=sum(1 for o in ops if o.status == "VERIFIED"),
                           active_op=pending.params.get("text", pending.kind)[:60] if pending else None)
        if pending is None:
            done = [o for o in ops if o.status == "VERIFIED"]
            return StageOutcome.done([passed(
                "timeline.edits", C.OPERATIONAL,
                f"{len(done)} of {len(ops)} edits verified by read-back")],
                {"verified": len(done), "total": len(ops)})
        attempt_no = (pending.result or {}).get("attempts", 0) + 1
        spec = TimelineOpSpec(op_key=pending.params.get("op_key", f"op{pending.seq}"),
                              kind=TimelineOpKind(pending.kind), params=pending.params["params"])

        async def noop(session, result):
            return None

        await ctx.s.intents.run(
            job_id=ctx.job_id, kind=IntentKind.TIMELINE_OP,
            target=f"{spec.op_key}:{attempt_no}", request=spec.model_dump(mode="json"),
            action=lambda key: adapter.apply_timeline_op(project_, spec),
            on_confirm=noop, project=project_,
        )
        state = await adapter.read_timeline(project_)
        ok, desc = verify_op({"kind": pending.kind, "params": pending.params["params"]}, state)
        locked_check = verify_locked(state, await locked_takes(ctx), f"op:{spec.op_key}")
        severity = Severity(pending.params.get("severity", "MAJOR"))
        results: list[RuleResult] = []
        async with ctx.s.db.transaction() as session:
            op = await session.get(TimelineOperation, pending.id, with_for_update=True)
            if ok:
                op.status = "VERIFIED"
                op.result = {"attempts": attempt_no, "verified": desc}
                results.append(passed(f"timeline.op.{pending.kind.lower()}", C.OPERATIONAL,
                                      f"{desc}: verified", subject=f"op:{spec.op_key}",
                                      data={"affects": pending.affects_scenes}))
            elif attempt_no < self.MAX_OP_ATTEMPTS:
                op.result = {"attempts": attempt_no, "last_failure": f"{desc}: not persisted"}
                await audit.record(session, type="timeline.op_unverified", job_id=ctx.job_id,
                                   campaign_id=ctx.job.campaign_id,
                                   message=f"{desc} did not persist; applying again")
            else:
                op.status = "FAILED"
                op.result = {"attempts": attempt_no, "last_failure": f"{desc}: not persisted"}
                results.append(failed(f"timeline.op.{pending.kind.lower()}", C.OPERATIONAL, severity,
                                      f"{desc} did not persist after {attempt_no} attempts",
                                      subject=f"op:{spec.op_key}"))
            if locked_check.failed:
                results.append(locked_check)
        return StageOutcome.proceed(desc, results)

    async def _plan(self, ctx: StageContext, project_: ProjectHandle) -> StageOutcome:
        adapter = ctx.s.adapter
        state = await adapter.read_timeline(project_)
        ops = plan_edits(scene_plan=await ctx.doc("SCENE_PLAN"), script=await ctx.doc("SCRIPT"),
                         brief=await ctx.doc("CAMPAIGN_BRIEF"),
                         profile={"logo_position": (await ctx.brand_profile()).logo_position},
                         state=state, settings=ctx.settings)
        results: list[RuleResult] = []
        async with ctx.s.db.transaction() as session:
            for op in ops:
                cap = CAPABILITY_FOR[TimelineOpKind(op["kind"])]
                status = "PENDING"
                if not adapter.capabilities.is_verified(cap):
                    status = "SKIPPED"
                    sev = Severity(op["severity"]) if op["required"] else Severity.MINOR
                    results.append(failed(f"timeline.capability.{op['kind'].lower()}", C.OPERATIONAL,
                                          sev, f"{op['kind']} is not a verified Vids capability "
                                               f"({cap}); the edit was not attempted"))
                session.add(TimelineOperation(
                    job_id=ctx.job_id, seq=op["seq"], kind=op["kind"],
                    params={"op_key": op["op_key"], "params": op["params"],
                            "severity": op["severity"], "required": op["required"]},
                    affects_scenes=op["affects_scenes"], status=status))
        await ctx.save_doc("TIMELINE_PLAN", {"operations": ops})
        results.append(passed("timeline.plan", C.CONTENT, f"{len(ops)} edits planned"))
        return StageOutcome.proceed(f"{len(ops)} edits planned", results)


class PreviewStage:
    stage = StageId.PREVIEW
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        project_ = _project(ctx)
        adapter = ctx.s.adapter
        shots = await adapter.capture_preview(project_, ctx.work_dir())
        stored = await ctx.store_evidence(shots, "preview", adapter.simulated)
        state = await adapter.read_timeline(project_)
        results = [verify_locked(state, await locked_takes(ctx), "preview")]
        covered = {s.meta.get("scene_key") for s in shots if s.meta.get("scene_key")}
        missing = [i.scene_key for i in state.items if covered and i.scene_key not in covered]
        if not stored:
            results.append(not_run("preview.captured", C.OPERATIONAL, Severity.MAJOR,
                                   "no preview screenshots could be captured"))
        elif missing:
            results.append(failed("preview.captured", C.OPERATIONAL, Severity.MINOR,
                                  f"no preview of {missing}"))
        else:
            results.append(passed("preview.captured", C.OPERATIONAL,
                                  f"{len(stored)} preview screenshots",
                                  evidence=[str(a.id) for a in stored]))
        await ctx.save_doc("TIMELINE_STATE", state.model_dump(mode="json"))
        return StageOutcome.done(results)


# ======================================================================= export
class ExportStage:
    stage = StageId.EXPORT
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        project_ = _project(ctx)
        adapter = ctx.s.adapter
        brief = await ctx.doc("CAMPAIGN_BRIEF")
        timeline = await adapter.read_timeline(project_)
        out_dir = ctx.work_dir(f"attempt{ctx.attempt.attempt}")
        async with ctx.s.db.session() as session:
            attempt_no = (await session.scalar(select(func.max(Export.attempt)).where(
                Export.job_id == ctx.job_id)) or 0) + 1

        async def on_confirm(session, result: dict) -> None:
            if await session.scalar(select(Export.id).where(Export.job_id == ctx.job_id,
                                                            Export.attempt == attempt_no)):
                return
            session.add(Export(job_id=ctx.job_id, attempt=attempt_no,
                               status=ExportStatus.DOWNLOADED.value,
                               simulated=bool(result.get("simulated")), probe={"path": result["path"]}))

        result = await ctx.s.intents.run(
            job_id=ctx.job_id, kind=IntentKind.EXPORT, target=f"export:{attempt_no}",
            request={"format": "mp4"},
            action=lambda key: adapter.export(project_, ExportSpec(idempotency_key=key), out_dir),
            on_confirm=on_confirm, project=project_,
        )
        path = Path(result["path"])
        results, probe_dict = await verify_export(ctx, path, timeline.total_duration_seconds, brief)
        ok = not any(r.failed and r.severity in (Severity.MAJOR, Severity.BLOCKER) for r in results)
        artifact = None
        if path.exists():
            artifact = await ctx.store_file(path, ArtifactKind.EXPORT, "export", adapter.simulated,
                                            meta={"attempt": attempt_no, **result.get("details", {})})
        async with ctx.s.db.transaction() as session:
            export = await session.scalar(select(Export).where(Export.job_id == ctx.job_id,
                                                               Export.attempt == attempt_no))
            export.status = (ExportStatus.VERIFIED if ok and artifact else ExportStatus.FAILED).value
            export.artifact_id = artifact.id if artifact else None
            export.duration_seconds = probe_dict.get("duration_seconds")
            export.size_bytes = probe_dict.get("size_bytes")
            export.probe = {**probe_dict, "details": result.get("details", {})}
            export.verification = [r.model_dump(mode="json") for r in results]
        if artifact:
            for r in results:
                r.evidence = [str(artifact.id)]
        return StageOutcome.done(results, {"export_attempt": attempt_no, "verified": ok})


async def verify_export(ctx: StageContext, path: Path, expected: float,
                        brief: dict) -> tuple[list[RuleResult], dict]:
    s = ctx.settings
    r: list[RuleResult] = []
    if not path.exists():
        return [failed("export.file_exists", C.TECHNICAL, Severity.MAJOR,
                       "the export file does not exist")], {}
    r.append(passed("export.file_exists", C.TECHNICAL, f"{path.name} exists"))
    size = path.stat().st_size
    if not s.export_min_bytes <= size <= s.export_max_bytes:
        r.append(failed("export.size", C.TECHNICAL, Severity.MAJOR,
                        f"{size} bytes is outside {s.export_min_bytes}-{s.export_max_bytes}"))
    else:
        r.append(passed("export.size", C.TECHNICAL, f"{size / 1e6:.2f} MB"))
    probe = await ffmpeg.probe(path)
    if not probe.container_ok or probe.video_codec is None:
        r.append(failed("export.format", C.TECHNICAL, Severity.MAJOR,
                        "not a readable video container"))
        return r, probe.to_dict()
    described = f"{path.suffix or 'no extension'}, {probe.video_codec}, {probe.width}x{probe.height}"
    if path.suffix.lower() == ".mp4" and probe.video_codec in ("h264", "hevc"):
        r.append(passed("export.format", C.TECHNICAL, f"MP4 ({described})"))
    else:
        r.append(failed("export.format", C.TECHNICAL, Severity.MAJOR,
                        f"expected an H.264/HEVC MP4, got {described}"))
    want = 16 / 9 if brief["output"]["aspect_ratio"] == "16:9" else 9 / 16
    if probe.aspect_ratio is None or abs(probe.aspect_ratio - want) > 0.02:
        r.append(failed("export.aspect_ratio", C.TECHNICAL, Severity.MAJOR,
                        f"{probe.width}x{probe.height} is not {brief['output']['aspect_ratio']}"))
    else:
        r.append(passed("export.aspect_ratio", C.TECHNICAL, brief["output"]["aspect_ratio"]))
    duration = probe.duration_seconds or 0
    if abs(duration - expected) > max(1.0, s.duration_tolerance_seconds * 2):
        r.append(failed("export.duration", C.TECHNICAL, Severity.MAJOR,
                        f"export is {duration:.2f}s; the timeline is {expected:.2f}s "
                        "(truncated or incomplete export)"))
    else:
        r.append(passed("export.duration", C.TECHNICAL, f"{duration:.2f}s matches the timeline"))
    analysis = await ffmpeg.analyse(path, has_audio=probe.has_audio)
    if not analysis.decode_ok:
        r.append(failed("export.playable", C.TECHNICAL, Severity.MAJOR,
                        "the file does not decode end to end: "
                        + "; ".join(analysis.decode_errors[:2])))
    else:
        r.append(passed("export.playable", C.TECHNICAL, "decodes end to end without errors"))
    if brief["output"].get("voiceover_required", True) and not probe.has_audio:
        r.append(failed("export.audio", C.TECHNICAL, Severity.MAJOR, "the export has no audio track"))
    elif probe.has_audio:
        r.append(passed("export.audio", C.TECHNICAL, f"audio {probe.audio_codec} {probe.audio_rate} Hz"))
    r.append(passed("export.checksum", C.TECHNICAL, f"sha256 {sha256_file(path)[:16]}…"))
    return r, probe.to_dict()


# ======================================================================= final QA
class FinalQAStage:
    stage = StageId.FINAL_QA
    auto_retry = False  # a failed QA is reported, not re-run until someone fixes the cause

    async def run(self, ctx: StageContext) -> StageOutcome:
        found = await latest_verified_export(ctx)
        if found is None:
            return StageOutcome.done([not_run("qa.export", C.TECHNICAL, Severity.BLOCKER,
                                              "there is no verified export to inspect")])
        export, artifact = found
        path = ctx.s.store.resolve(artifact.uri)
        results: list[RuleResult] = []
        if sha256_file(path) != artifact.sha256:
            return StageOutcome.done([failed("qa.export_integrity", C.TECHNICAL, Severity.BLOCKER,
                                             "the stored export changed after verification")])
        results.append(passed("qa.export_integrity", C.TECHNICAL,
                              "stored export matches its verified checksum",
                              evidence=[str(artifact.id)]))
        project_ = _project(ctx)
        timeline = await ctx.s.adapter.read_timeline(project_)
        windows = scene_windows(timeline)
        probe = await ffmpeg.probe(path)
        analysis = await ffmpeg.analyse(path, has_audio=probe.has_audio)
        stamps = [round((a + b) / 2, 2) for a, b in windows.values()]
        if probe.duration_seconds:
            stamps.append(round(max(0.0, probe.duration_seconds - 0.4), 2))
        frame_paths = await ffmpeg.extract_frames(path, stamps, ctx.work_dir("frames"))
        frames = list(zip(stamps, frame_paths, strict=False))
        from vidsprod.vids.port import EvidenceFile

        stored = await ctx.store_evidence(
            [EvidenceFile(path=p, kind=ArtifactKind.FRAME, mime="image/jpeg",
                          label=f"export frame {t:.2f}s", meta={"t": t}) for t, p in frames],
            "final_qa", ctx.s.adapter.simulated)
        qa, report = await run_final_qa(
            ai=ctx.s.ai, settings=ctx.settings, probe=probe, analysis=analysis, export_path=path,
            timeline=timeline, windows=windows, scene_plan=await ctx.doc("SCENE_PLAN"),
            script=await ctx.doc("SCRIPT"), brief=await ctx.doc("CAMPAIGN_BRIEF"),
            locked=await locked_takes(ctx), frames=frames, brand_block=project_block(ctx),
            simulation_hints=(export.probe or {}).get("details", {}), job_id=ctx.job_id,
        )
        results += qa
        report["export_artifact_id"] = str(artifact.id)
        report["frame_artifact_ids"] = [str(a.id) for a in stored]
        report["simulated"] = ctx.s.adapter.simulated
        await ctx.save_doc("QA_REPORT", report)
        return StageOutcome.done(results, {"verdict": report["verdict"]})


# ======================================================================= delivery
class DeliveryStage:
    stage = StageId.DELIVERY
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        found = await latest_verified_export(ctx)
        if found is None:
            return StageOutcome.done([not_run("delivery.export", C.TECHNICAL, Severity.BLOCKER,
                                              "no verified export to deliver")])
        export, artifact = found
        source = ctx.s.store.resolve(artifact.uri)
        simulated = export.simulated
        async with ctx.s.db.session() as session:
            existing = await session.scalar(select(Delivery).where(
                Delivery.job_id == ctx.job_id, Delivery.export_id == export.id,
                Delivery.status == DeliveryStatus.STORED.value))
        results: list[RuleResult] = []
        if existing is None:
            deliverable = await ctx.store_file(source, ArtifactKind.DELIVERABLE, "delivery", simulated,
                                               meta={"export_id": str(export.id)})
            ok = deliverable.sha256 == artifact.sha256
            async with ctx.s.db.transaction() as session:
                session.add(Delivery(job_id=ctx.job_id, export_id=export.id,
                                     status=DeliveryStatus.STORED.value, target="artifact_store",
                                     location=deliverable.uri, sha256=deliverable.sha256,
                                     simulated=simulated,
                                     note="SIMULATED OUTPUT" if simulated else None))
            if not ok:
                return StageOutcome.done([failed("delivery.stored", C.TECHNICAL, Severity.BLOCKER,
                                                 "the stored deliverable does not match the export")])
        results.append(passed("delivery.stored", C.TECHNICAL,
                              "deliverable stored; checksum matches the verified export"
                              + (" (simulated output)" if simulated else ""),
                              evidence=[str(artifact.id)]))
        target = ctx.settings.delivery_submission_target
        if target == "none":
            results.append(passed("delivery.submission", C.OPERATIONAL,
                                  "no submission target configured; the deliverable is stored only"))
            return StageOutcome.done(results)
        if simulated:
            results.append(passed("delivery.submission", C.OPERATIONAL,
                                  "simulated output is never submitted", severity=Severity.INFO))
            return StageOutcome.done(results)
        decided = [a for a in await ctx.approvals() if a.kind == ApprovalKind.SUBMISSION.value]
        if not decided:
            return StageOutcome.wait([ApprovalSpec(
                kind=ApprovalKind.SUBMISSION, subject="submission",
                title="Authorise submission of the final video",
                summary=f"Submit the verified export to '{target}'.",
                payload={"export_id": str(export.id), "sha256": artifact.sha256},
            )], "waiting for submission authorisation", results)
        last = decided[-1]
        if last.status != ApprovalStatus.APPROVED.value:
            results.append(failed("delivery.submission", C.OPERATIONAL, Severity.BLOCKER,
                                  f"submission refused by {last.decided_by}", judge="human"))
            return StageOutcome.done(results)
        submitted = await ctx.store_file(source, ArtifactKind.DELIVERABLE, "submission", False,
                                         meta={"target": target, "approval_id": str(last.id)})
        async with ctx.s.db.transaction() as session:
            session.add(Delivery(job_id=ctx.job_id, export_id=export.id,
                                 status=DeliveryStatus.SUBMITTED.value, target=target,
                                 location=submitted.uri, sha256=submitted.sha256,
                                 approval_id=last.id, simulated=False))
        results.append(passed("delivery.submission", C.OPERATIONAL,
                              f"submitted to {target}, authorised by {last.decided_by}",
                              judge="human"))
        return StageOutcome.done(results)
