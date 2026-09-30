"""Production jobs: status, controls, plan, scenes, timeline, QA, exports, logs, live events."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import func, select

from vidsprod.api.deps import ActorDep, ContainerDep, require_token
from vidsprod.api.schemas import (
    AIRequestOut,
    ApprovalOut,
    AuditOut,
    DeliveryOut,
    ExportOut,
    JobDetail,
    JobSummary,
    RetryRequest,
    SceneOut,
    SceneReview,
    SceneVersionOut,
    ValidationOut,
    job_summary,
    stage_list,
    take_out,
)
from vidsprod.db.models import (
    AIRequest,
    ApprovalRequest,
    AuditEvent,
    Campaign,
    Delivery,
    Export,
    JobDocument,
    ProductionJob,
    Scene,
    SceneTake,
    SceneVersion,
    TimelineOperation,
    ValidationResult,
    WorkflowStage,
)
from vidsprod.domain.enums import ApprovalKind, ApprovalStatus, JobStatus, StageId
from vidsprod.errors import ConflictError, InvalidInputError, NotFoundError
from vidsprod.workflow import budget

router = APIRouter(prefix="/api", tags=["jobs"], dependencies=[Depends(require_token)])

_DOC_KINDS = {"CAMPAIGN_BRIEF", "CREATIVE_PLAN", "SCRIPT", "SCENE_PLAN", "PROMPTS",
              "TIMELINE_PLAN", "TIMELINE_STATE", "QA_REPORT"}


async def _job(session, job_id: uuid.UUID) -> ProductionJob:
    job = await session.get(ProductionJob, job_id)
    if job is None:
        raise NotFoundError("job not found")
    return job


async def _detail(c, session, job: ProductionJob) -> JobDetail:
    stages = list((await session.execute(select(WorkflowStage).where(
        WorkflowStage.job_id == job.id))).scalars().all())
    failures = dict((await session.execute(
        select(ValidationResult.stage_attempt_id, func.count()).where(
            ValidationResult.job_id == job.id, ValidationResult.outcome != "PASS")
        .group_by(ValidationResult.stage_attempt_id))).all())
    approvals = (await session.execute(select(ApprovalRequest).where(
        ApprovalRequest.job_id == job.id).order_by(ApprovalRequest.created_at.desc()))).scalars().all()
    campaign = await session.get(Campaign, job.campaign_id)
    usage = await budget.usage(session, c.settings, job.vids_account, job.campaign_id)
    summary = job_summary(job, stages, campaign.name if campaign else None)
    current = next((s for s in stage_list(stages, failures) if s.stage == job.current_stage), None)
    progress = (current.progress or {}) if current else {}
    last_event = await session.scalar(select(AuditEvent).where(AuditEvent.job_id == job.id)
                                      .order_by(AuditEvent.id.desc()).limit(1))
    return JobDetail(
        **summary.model_dump(), stages=stage_list(stages, failures),
        approvals=[ApprovalOut.model_validate(a) for a in approvals], budget=usage,
        active_scene=progress.get("active_scene"),
        current_action=last_event.message if last_event else None,
        vids_project=(job.vids_project_ref or {}).get("project"),
    )


@router.get("/jobs")
async def list_jobs(c: ContainerDep, active: bool = False, limit: Annotated[int, Query(le=200)] = 50
                    ) -> list[JobSummary]:
    async with c.db.session() as session:
        stmt = select(ProductionJob).order_by(ProductionJob.created_at.desc()).limit(limit)
        if active:
            stmt = stmt.where(ProductionJob.status.in_([s.value for s in JobStatus if s.is_active]))
        out = []
        for job in (await session.execute(stmt)).scalars().all():
            stages = (await session.execute(select(WorkflowStage).where(
                WorkflowStage.job_id == job.id))).scalars().all()
            campaign = await session.get(Campaign, job.campaign_id)
            out.append(job_summary(job, list(stages), campaign.name if campaign else None))
        return out


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, c: ContainerDep) -> JobDetail:
    async with c.db.session() as session:
        return await _detail(c, session, await _job(session, job_id))


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: uuid.UUID, c: ContainerDep, actor: ActorDep) -> JobDetail:
    await c.control.pause(job_id, actor)
    return await get_job(job_id, c)


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: uuid.UUID, c: ContainerDep, actor: ActorDep) -> JobDetail:
    await c.control.resume(job_id, actor)
    return await get_job(job_id, c)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: uuid.UUID, c: ContainerDep, actor: ActorDep) -> JobDetail:
    await c.control.cancel(job_id, actor)
    return await get_job(job_id, c)


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: uuid.UUID, body: RetryRequest, c: ContainerDep, actor: ActorDep) -> JobDetail:
    stage = None
    if body.stage:
        try:
            stage = StageId(body.stage)
        except ValueError as exc:
            raise InvalidInputError(f"unknown stage {body.stage}") from exc
    await c.control.retry(job_id, actor, stage)
    return await get_job(job_id, c)


@router.get("/jobs/{job_id}/stages/{stage}")
async def stage_attempts(job_id: uuid.UUID, stage: str, c: ContainerDep) -> list[dict[str, Any]]:
    async with c.db.session() as session:
        attempts = (await session.execute(select(WorkflowStage).where(
            WorkflowStage.job_id == job_id, WorkflowStage.stage == stage)
            .order_by(WorkflowStage.attempt))).scalars().all()
        out = []
        for a in attempts:
            results = (await session.execute(select(ValidationResult).where(
                ValidationResult.stage_attempt_id == a.id).order_by(ValidationResult.created_at))).scalars().all()
            out.append({
                "id": str(a.id), "attempt": a.attempt, "status": a.status,
                "next_permitted_action": a.next_permitted_action, "output": a.output,
                "error": {k: v for k, v in (a.error or {}).items() if k != "traceback"} or None,
                "progress": a.progress, "input_hash": a.input_hash,
                "started_at": a.started_at, "validated_at": a.validated_at,
                "finished_at": a.finished_at,
                "validations": [ValidationOut.model_validate(r).model_dump(mode="json") for r in results],
            })
        return out


@router.get("/jobs/{job_id}/documents/{kind}")
async def get_document(job_id: uuid.UUID, kind: str, c: ContainerDep,
                       version: int | None = None) -> dict[str, Any]:
    if kind not in _DOC_KINDS:
        raise InvalidInputError(f"unknown document kind {kind}")
    async with c.db.session() as session:
        stmt = select(JobDocument).where(JobDocument.job_id == job_id, JobDocument.kind == kind)
        stmt = (stmt.where(JobDocument.version == version) if version
                else stmt.order_by(JobDocument.version.desc()).limit(1))
        row = await session.scalar(stmt)
        if row is None:
            raise NotFoundError(f"{kind} has not been produced yet")
        return {"kind": row.kind, "version": row.version, "content": row.content,
                "content_hash": row.content_hash, "created_at": row.created_at}


@router.get("/jobs/{job_id}/plan")
async def get_plan(job_id: uuid.UUID, c: ContainerDep) -> dict[str, Any]:
    out: dict[str, Any] = {}
    async with c.db.session() as session:
        await _job(session, job_id)
        for kind in ("CAMPAIGN_BRIEF", "CREATIVE_PLAN", "SCRIPT", "SCENE_PLAN", "PROMPTS"):
            row = await session.scalar(select(JobDocument).where(
                JobDocument.job_id == job_id, JobDocument.kind == kind)
                .order_by(JobDocument.version.desc()).limit(1))
            out[kind] = {"version": row.version, "content": row.content} if row else None
    return out


async def _scene_out(session, scene: Scene) -> SceneOut:
    versions = (await session.execute(select(SceneVersion).where(
        SceneVersion.scene_id == scene.id).order_by(SceneVersion.version))).scalars().all()
    takes = (await session.execute(select(SceneTake).where(
        SceneTake.scene_id == scene.id).order_by(SceneTake.take_no))).scalars().all()
    take_views = []
    for take in takes:
        validations = (await session.execute(select(ValidationResult).where(
            ValidationResult.take_id == take.id).order_by(ValidationResult.created_at))).scalars().all()
        take_views.append(take_out(take, list(validations)))
    current = next((v for v in versions if v.id == scene.current_version_id), None)
    return SceneOut(
        id=scene.id, scene_key=scene.scene_key, order_index=scene.order_index, status=scene.status,
        takes_used=scene.takes_used, edits_used=scene.edits_used,
        extra_takes_granted=scene.extra_takes_granted, locked_take_id=scene.locked_take_id,
        pending_instruction=scene.pending_instruction,
        current_version=SceneVersionOut.model_validate(current) if current else None,
        versions=[SceneVersionOut.model_validate(v) for v in versions], takes=take_views,
    )


@router.get("/jobs/{job_id}/scenes")
async def list_scenes(job_id: uuid.UUID, c: ContainerDep) -> list[SceneOut]:
    async with c.db.session() as session:
        scenes = (await session.execute(select(Scene).where(Scene.job_id == job_id)
                                        .order_by(Scene.order_index))).scalars().all()
        return [await _scene_out(session, s) for s in scenes]


@router.get("/jobs/{job_id}/scenes/{scene_key}")
async def get_scene(job_id: uuid.UUID, scene_key: str, c: ContainerDep) -> SceneOut:
    async with c.db.session() as session:
        scene = await session.scalar(select(Scene).where(Scene.job_id == job_id,
                                                         Scene.scene_key == scene_key))
        if scene is None:
            raise NotFoundError("scene not found")
        return await _scene_out(session, scene)


@router.post("/jobs/{job_id}/scenes/{scene_key}/review")
async def review_scene(job_id: uuid.UUID, scene_key: str, body: SceneReview, c: ContainerDep,
                       actor: ActorDep) -> ApprovalOut:
    """Decide a scene waiting for review: accept (lock), regenerate (this scene only), edit
    (with an instruction), or reject. Only scenes that inspection handed to a person."""
    async with c.db.session() as session:
        approval = await session.scalar(select(ApprovalRequest).where(
            ApprovalRequest.job_id == job_id, ApprovalRequest.kind == ApprovalKind.SCENE_REVIEW.value,
            ApprovalRequest.status == ApprovalStatus.PENDING.value,
            ApprovalRequest.subject.like(f"scene:{scene_key}:%")))
    if approval is None:
        raise ConflictError(f"scene {scene_key} is not waiting for review")
    payload = {} if body.action == "reject" else {"action": body.action}
    if body.instruction:
        payload["instruction"] = body.instruction
    decided = await c.control.decide(approval.id, approve=body.action != "reject", actor=actor,
                                     reason=body.reason, payload=payload)
    return ApprovalOut.model_validate(decided)


@router.get("/jobs/{job_id}/timeline")
async def get_timeline(job_id: uuid.UUID, c: ContainerDep) -> dict[str, Any]:
    async with c.db.session() as session:
        state = await session.scalar(select(JobDocument).where(
            JobDocument.job_id == job_id, JobDocument.kind == "TIMELINE_STATE")
            .order_by(JobDocument.version.desc()).limit(1))
        ops = (await session.execute(select(TimelineOperation).where(
            TimelineOperation.job_id == job_id).order_by(TimelineOperation.seq))).scalars().all()
        return {
            "state": state.content if state else None,
            "operations": [{"seq": o.seq, "kind": o.kind, "status": o.status,
                            "params": o.params.get("params"), "required": o.params.get("required"),
                            "affects_scenes": o.affects_scenes, "result": o.result} for o in ops],
        }


@router.get("/jobs/{job_id}/qa")
async def get_qa(job_id: uuid.UUID, c: ContainerDep) -> dict[str, Any]:
    return await get_document(job_id, "QA_REPORT", c)


@router.get("/jobs/{job_id}/exports")
async def get_exports(job_id: uuid.UUID, c: ContainerDep) -> dict[str, Any]:
    async with c.db.session() as session:
        exports = (await session.execute(select(Export).where(Export.job_id == job_id)
                                         .order_by(Export.attempt))).scalars().all()
        deliveries = (await session.execute(select(Delivery).where(Delivery.job_id == job_id)
                                            .order_by(Delivery.created_at))).scalars().all()
        return {"exports": [ExportOut.model_validate(e).model_dump(mode="json") for e in exports],
                "deliveries": [DeliveryOut.model_validate(d).model_dump(mode="json") for d in deliveries]}


@router.get("/jobs/{job_id}/audit")
async def job_audit(job_id: uuid.UUID, c: ContainerDep, after: int = 0,
                    limit: Annotated[int, Query(le=1000)] = 200) -> list[AuditOut]:
    async with c.db.session() as session:
        rows = (await session.execute(select(AuditEvent).where(
            AuditEvent.job_id == job_id, AuditEvent.id > after).order_by(AuditEvent.id).limit(limit))).scalars().all()
        return [AuditOut.model_validate(r) for r in rows]


@router.get("/audit")
async def all_audit(c: ContainerDep, before: int | None = None,
                    limit: Annotated[int, Query(le=500)] = 100) -> list[AuditOut]:
    async with c.db.session() as session:
        stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
        if before:
            stmt = stmt.where(AuditEvent.id < before)
        return [AuditOut.model_validate(r) for r in (await session.execute(stmt)).scalars().all()]


@router.get("/jobs/{job_id}/ai-requests")
async def ai_requests(job_id: uuid.UUID, c: ContainerDep) -> list[AIRequestOut]:
    async with c.db.session() as session:
        rows = (await session.execute(select(AIRequest).where(AIRequest.job_id == job_id)
                                      .order_by(AIRequest.created_at))).scalars().all()
        return [AIRequestOut.model_validate(r) for r in rows]


@router.get("/jobs/{job_id}/events", response_class=EventSourceResponse)
async def job_events(job_id: uuid.UUID, request: Request, c: ContainerDep,
                     after: int = 0) -> AsyncIterable[ServerSentEvent]:
    """Live updates: audit events as they are written, plus the job's status. Read from
    Postgres, so they survive restarts and never show anything that was not recorded."""
    cursor = after
    last_status = None
    while not await request.is_disconnected():
        async with c.db.session() as session:
            job = await session.get(ProductionJob, job_id)
            if job is None:
                yield ServerSentEvent(data={"error": "job not found"}, event="error")
                return
            rows = (await session.execute(select(AuditEvent).where(
                AuditEvent.job_id == job_id, AuditEvent.id > cursor)
                .order_by(AuditEvent.id).limit(100))).scalars().all()
        for row in rows:
            cursor = row.id
            yield ServerSentEvent(data=AuditOut.model_validate(row).model_dump(mode="json"),
                                  event="audit", id=str(row.id))
        status = (job.status, job.current_stage, job.control)
        if status != last_status:
            last_status = status
            yield ServerSentEvent(data={"status": job.status, "current_stage": job.current_stage,
                                        "control": job.control}, event="status")
        await asyncio.sleep(1.0)
