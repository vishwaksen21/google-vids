"""Human controls: start, pause, resume, cancel, retry, and approval decisions.

Each control is a short transaction on the job row (locked FOR UPDATE) plus an audit
event naming the actor. Controls never run stage work themselves; a paused or cancelled
job stops at the worker's next checkpoint, and a decided approval makes the job
claimable again.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from vidsprod.config import Settings
from vidsprod.db.models import (
    ApprovalRequest,
    Campaign,
    CampaignBrief,
    ProductionJob,
    WorkflowStage,
)
from vidsprod.db.session import Database
from vidsprod.domain.enums import (
    PLANNING_STAGES,
    STAGE_ORDER,
    ApprovalKind,
    ApprovalStatus,
    CampaignStatus,
    JobControl,
    JobStatus,
    StageId,
    StageStatus,
)
from vidsprod.errors import ConflictError, InvalidInputError, NotFoundError
from vidsprod.vids.port import GoogleVidsAdapter
from vidsprod.workflow import audit
from vidsprod.workflow.engine import cancel_job_rows, set_campaign_status
from vidsprod.workflow.intents import IntentRunner

SCENE_ACTIONS = ("accept", "regenerate", "edit")
INTERVENTION_ACTIONS = ("retry", "fail")


class JobControlService:
    def __init__(self, db: Database, settings: Settings, adapter: GoogleVidsAdapter,
                 intents: IntentRunner, brand_digest: str | None) -> None:
        self.db = db
        self.settings = settings
        self.adapter = adapter
        self.intents = intents
        self.brand_digest = brand_digest

    async def start(self, campaign_id: uuid.UUID, actor: str,
                    idempotency_key: str | None = None) -> ProductionJob:
        async with self.db.transaction() as session:
            if idempotency_key:
                existing = await session.scalar(select(ProductionJob).where(
                    ProductionJob.idempotency_key == idempotency_key))
                if existing is not None:
                    if existing.campaign_id != campaign_id:
                        raise ConflictError("idempotency key was used for another campaign")
                    return existing
            campaign = await session.get(Campaign, campaign_id, with_for_update=True)
            if campaign is None:
                raise NotFoundError("campaign not found")
            if campaign.archived_at is not None:
                raise ConflictError("the campaign is archived")
            if campaign.current_brief_version < 1:
                raise InvalidInputError("save a brief before starting production")
            # The campaign row is locked, so this check cannot race another start.
            active = await session.scalar(select(ProductionJob.id).where(
                ProductionJob.campaign_id == campaign_id,
                ProductionJob.status.in_([s.value for s in JobStatus if s.is_active])))
            if active is not None:
                raise ConflictError("this campaign already has an active production",
                                    details={"active_job_id": str(active)})
            brief = await session.scalar(select(CampaignBrief).where(
                CampaignBrief.campaign_id == campaign_id,
                CampaignBrief.version == campaign.current_brief_version))
            job = ProductionJob(
                campaign_id=campaign_id, brief_id=brief.id, status=JobStatus.QUEUED.value,
                adapter=self.adapter.name, vids_account=self.adapter.account_key,
                ai_provider=self.settings.ai_provider.value, simulation=self.settings.simulation,
                brand_digest=self.brand_digest, idempotency_key=idempotency_key,
                created_by=actor,
            )
            session.add(job)
            try:
                await session.flush()
            except IntegrityError as exc:  # backstop: the partial unique index
                raise ConflictError("this campaign already has an active production") from exc
            campaign.status = CampaignStatus.IN_PRODUCTION.value
            await audit.record(session, type="job.created", actor=actor, job_id=job.id,
                               campaign_id=campaign_id,
                               message=f"production started from brief v{brief.version}",
                               data={"simulation": job.simulation, "adapter": job.adapter,
                                     "ai_provider": job.ai_provider})
            return job

    async def _locked_job(self, session, job_id: uuid.UUID) -> ProductionJob:
        job = await session.get(ProductionJob, job_id, with_for_update=True)
        if job is None:
            raise NotFoundError("job not found")
        return job

    async def pause(self, job_id: uuid.UUID, actor: str) -> ProductionJob:
        async with self.db.transaction() as session:
            job = await self._locked_job(session, job_id)
            if job.status == JobStatus.RUNNING.value:
                job.control = JobControl.PAUSE.value
                message = "pause requested; takes effect at the next checkpoint"
            elif job.status in (JobStatus.QUEUED.value, JobStatus.WAITING_FOR_HUMAN.value):
                job.status = JobStatus.PAUSED.value
                message = "paused"
            else:
                raise ConflictError(f"a {job.status} job cannot be paused")
            await audit.record(session, type="job.pause", actor=actor, job_id=job.id,
                               campaign_id=job.campaign_id, message=message)
            return job

    async def resume(self, job_id: uuid.UUID, actor: str) -> ProductionJob:
        async with self.db.transaction() as session:
            job = await self._locked_job(session, job_id)
            if job.status != JobStatus.PAUSED.value and job.control != JobControl.PAUSE.value:
                raise ConflictError(f"a {job.status} job is not paused")
            job.control = None
            if job.status == JobStatus.PAUSED.value:
                job.status = JobStatus.QUEUED.value
            await set_campaign_status(session, job.campaign_id, CampaignStatus.IN_PRODUCTION)
            await audit.record(session, type="job.resume", actor=actor, job_id=job.id,
                               campaign_id=job.campaign_id, message="resumed")
            return job

    async def cancel(self, job_id: uuid.UUID, actor: str) -> ProductionJob:
        async with self.db.transaction() as session:
            job = await self._locked_job(session, job_id)
            if JobStatus(job.status).is_terminal:
                raise ConflictError(f"a {job.status} job cannot be cancelled")
            if job.status == JobStatus.RUNNING.value and job.lease_owner:
                job.control = JobControl.CANCEL.value
                await audit.record(session, type="job.cancel", actor=actor, job_id=job.id,
                                   campaign_id=job.campaign_id,
                                   message="cancel requested; takes effect at the next checkpoint")
            else:
                await cancel_job_rows(session, job, actor)
            return job

    async def retry(self, job_id: uuid.UUID, actor: str, stage: StageId | None = None) -> ProductionJob:
        """Run a stage again (and everything after it). History is kept; new attempts are
        added. Planning cannot be redone once scenes are being produced, because that would
        discard accepted scenes: start a new production instead."""
        async with self.db.transaction() as session:
            job = await self._locked_job(session, job_id)
            if job.status not in (JobStatus.FAILED.value, JobStatus.WAITING_FOR_HUMAN.value,
                                  JobStatus.PAUSED.value):
                raise ConflictError(f"a {job.status} job cannot be retried")
            latest = {}
            for row in (await session.execute(select(WorkflowStage).where(
                    WorkflowStage.job_id == job_id).order_by(WorkflowStage.attempt))).scalars():
                latest[row.stage] = row
            if stage is None:
                stage = next((StageId(s) for s in (x.value for x in STAGE_ORDER)
                              if s in latest and not StageStatus(latest[s].status).is_complete), None)
                if stage is None:
                    raise ConflictError("nothing to retry")
            production = latest.get(StageId.SCENE_PRODUCTION.value)
            if stage in PLANNING_STAGES and production is not None:
                raise ConflictError(
                    "planning cannot be redone after scene production started: it would "
                    "discard accepted scenes. Start a new production for a new plan."
                )
            index = STAGE_ORDER.index(stage)
            for later in STAGE_ORDER[index:]:
                row = latest.get(later.value)
                if row is None:
                    continue
                if row.status in (StageStatus.RUNNING.value, StageStatus.WAITING_FOR_HUMAN.value,
                                  StageStatus.PENDING.value, StageStatus.RETRYING.value):
                    row.status = StageStatus.FAILED.value if later == stage else StageStatus.SKIPPED.value
                    row.next_permitted_action = f"SUPERSEDED by retry from {actor}"
                session.add(WorkflowStage(job_id=job_id, stage=later.value, attempt=row.attempt + 1,
                                          status=StageStatus.PENDING.value))
            await session.execute(update(ApprovalRequest).where(
                ApprovalRequest.job_id == job_id,
                ApprovalRequest.status == ApprovalStatus.PENDING.value,
                ApprovalRequest.kind == ApprovalKind.INTERVENTION.value,
            ).values(status=ApprovalStatus.CANCELLED.value))
            job.status = JobStatus.QUEUED.value
            job.control = None
            job.error = None
            job.next_run_at = None
            job.finished_at = None
            await set_campaign_status(session, job.campaign_id, CampaignStatus.IN_PRODUCTION)
            await audit.record(session, type="job.retry", actor=actor, job_id=job.id,
                               campaign_id=job.campaign_id, message=f"retry from {stage.value}")
            return job

    async def decide(self, approval_id: uuid.UUID, *, approve: bool, actor: str,
                     reason: str, payload: dict[str, Any] | None = None) -> ApprovalRequest:
        payload = payload or {}
        async with self.db.transaction() as session:
            approval = await session.get(ApprovalRequest, approval_id, with_for_update=True)
            if approval is None:
                raise NotFoundError("approval not found")
            if approval.status != ApprovalStatus.PENDING.value:
                raise ConflictError(f"this approval is already {approval.status}")
            kind = ApprovalKind(approval.kind)
            if kind is ApprovalKind.SCENE_REVIEW and approve:
                action = payload.get("action")
                if action not in SCENE_ACTIONS:
                    raise InvalidInputError(f"scene review needs an action: {', '.join(SCENE_ACTIONS)}")
                if action == "edit" and not str(payload.get("instruction") or "").strip():
                    raise InvalidInputError("an edit needs an instruction")
            if kind is ApprovalKind.INTERVENTION and payload.get("intent_resolution"):
                if payload["intent_resolution"] not in ("happened", "not_happened"):
                    raise InvalidInputError("intent_resolution must be happened or not_happened")
            approval.status = (ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED).value
            approval.decision = {"approve": approve, "reason": reason, **payload}
            approval.decided_by = actor
            from vidsprod.workflow.engine import now

            approval.decided_at = now()
            job = await self._locked_job(session, approval.job_id)
            await audit.record(session, type="approval.decided", actor=actor, job_id=job.id,
                               campaign_id=job.campaign_id,
                               message=f"{approval.title}: {approval.status}",
                               data={"approval_id": str(approval.id), "kind": approval.kind,
                                     "reason": reason, **payload})
            intent_id = (approval.payload.get("error", {}).get("details", {}).get("intent_id")
                         if kind is ApprovalKind.INTERVENTION else None)
            pending = await session.scalar(select(ApprovalRequest.id).where(
                ApprovalRequest.job_id == job.id,
                ApprovalRequest.status == ApprovalStatus.PENDING.value,
                ApprovalRequest.id != approval.id))
            if pending is None and job.status == JobStatus.WAITING_FOR_HUMAN.value:
                job.status = JobStatus.QUEUED.value
                await set_campaign_status(session, job.campaign_id, CampaignStatus.IN_PRODUCTION)
        if intent_id and approve and payload.get("intent_resolution"):
            await self.intents.resolve_by_human(uuid.UUID(intent_id),
                                                payload["intent_resolution"] == "happened", actor)
        return approval
