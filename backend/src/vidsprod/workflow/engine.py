"""The workflow engine: a persistent state machine over `production_job` and
`workflow_stage`.

One step = one call to the current stage's handler, with every state change written to
Postgres before and after. The engine never holds a transaction open while a handler
works (handlers call the browser and the AI provider), and it re-reads the job between
steps, so pause, cancel and approvals take effect at the next checkpoint.

Claiming uses `FOR UPDATE SKIP LOCKED` with a renewable lease. A worker that dies simply
stops renewing; once the lease expires another worker (or the same one after a restart)
resumes the job from its last persisted stage. Handlers are written to resume from
database state, and non-idempotent Vids actions go through the intent log, so resuming
never repeats a generation.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import traceback
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from vidsprod.db.models import (
    ApprovalRequest,
    Campaign,
    ProductionJob,
    ValidationResult,
    WorkflowStage,
)
from vidsprod.domain.enums import (
    STAGE_ORDER,
    ApprovalKind,
    ApprovalStatus,
    CampaignStatus,
    JobControl,
    JobStatus,
    StageId,
    StageStatus,
)
from vidsprod.domain.validation import RuleResult
from vidsprod.errors import BlockedError, HumanRequiredError, TransientError, VidsProdError
from vidsprod.logging import bind, get_logger, reset
from vidsprod.workflow import audit, gates
from vidsprod.workflow.context import (
    ApprovalSpec,
    OutcomeKind,
    Services,
    StageContext,
    StageHandler,
    StageOutcome,
)

log = get_logger(__name__)


def now() -> datetime:
    return datetime.now(UTC)


async def persist_results(
    session: AsyncSession, job_id: uuid.UUID, attempt_id: uuid.UUID | None,
    results: list[RuleResult], take_id: uuid.UUID | None = None,
) -> None:
    for r in results:
        session.add(ValidationResult(
            job_id=job_id, stage_attempt_id=attempt_id, take_id=take_id, subject=r.subject,
            rule_id=r.rule_id, category=r.category.value, severity=r.severity.value,
            outcome=r.outcome.value, message=r.message[:2000], judge=r.judge,
            evidence=r.evidence, fix_hint=r.fix_hint, data=r.data,
        ))
    await session.flush()


async def set_campaign_status(session: AsyncSession, campaign_id: uuid.UUID,
                              status: CampaignStatus) -> None:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is not None and campaign.status != CampaignStatus.ARCHIVED.value:
        campaign.status = status.value


class Engine:
    def __init__(self, services: Services, handlers: dict[StageId, StageHandler],
                 worker_id: str | None = None) -> None:
        self.s = services
        self.handlers = handlers
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        missing = [s for s in STAGE_ORDER if s not in handlers]
        if missing:
            raise ValueError(f"no handler for stages: {missing}")

    @property
    def lease(self) -> timedelta:
        return timedelta(seconds=self.s.settings.worker_lease_seconds)

    # ------------------------------------------------------------------ claiming
    async def claim_next(self) -> uuid.UUID | None:
        async with self.s.db.transaction() as session:
            job_id = await session.scalar(text("""
                SELECT id FROM production_job
                WHERE status IN ('QUEUED', 'RUNNING')
                  AND (next_run_at IS NULL OR next_run_at <= now())
                  AND (lease_expires_at IS NULL OR lease_expires_at < now() OR lease_owner = :me)
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """), {"me": self.worker_id})
            if job_id is None:
                return None
            try:
                async with session.begin_nested():
                    job = await session.get(ProductionJob, job_id)
                    resumed = job.status == JobStatus.RUNNING.value
                    job.status = JobStatus.RUNNING.value
                    job.lease_owner = self.worker_id
                    job.lease_expires_at = now() + self.lease
                    job.next_run_at = None
                    job.started_at = job.started_at or now()
                    await session.flush()
            except IntegrityError:
                # Another job already holds this Google account's browser.
                return None
            await audit.record(session, type="job.resumed" if resumed else "job.claimed",
                               job_id=job_id, campaign_id=job.campaign_id,
                               message=f"{'resumed' if resumed else 'claimed'} by {self.worker_id}")
            return job_id

    async def _heartbeat(self, job_id: uuid.UUID) -> None:
        interval = max(1.0, self.s.settings.worker_lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            async with self.s.db.transaction() as session:
                await session.execute(
                    update(ProductionJob)
                    .where(ProductionJob.id == job_id, ProductionJob.lease_owner == self.worker_id)
                    .values(lease_expires_at=now() + self.lease)
                )

    async def run_job(self, job_id: uuid.UUID, max_steps: int = 10_000) -> None:
        token = bind(job_id=str(job_id), worker=self.worker_id)
        beat = asyncio.create_task(self._heartbeat(job_id))
        try:
            for _ in range(max_steps):
                if not await self.step(job_id):
                    break
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
            reset(token)

    async def run_until_idle(self, max_jobs: int = 100) -> int:
        """Process claimable jobs until none remain (tests and the CLI use this)."""
        processed = 0
        while processed < max_jobs:
            job_id = await self.claim_next()
            if job_id is None:
                return processed
            await self.run_job(job_id)
            processed += 1
        return processed

    # ------------------------------------------------------------------ one step
    async def step(self, job_id: uuid.UUID) -> bool:
        """Advance the job by one handler call. Returns True if the caller should keep going."""
        async with self.s.db.transaction() as session:
            job = await session.get(ProductionJob, job_id, with_for_update=True)
            if job is None:
                return False
            if job.control == JobControl.CANCEL.value:
                await self._finalise_cancel(session, job)
                return False
            if job.control == JobControl.PAUSE.value:
                job.status = JobStatus.PAUSED.value
                job.control = None
                self._release(job)
                await audit.record(session, type="job.paused", job_id=job.id,
                                   campaign_id=job.campaign_id, message="paused at a checkpoint")
                return False
            if job.status != JobStatus.RUNNING.value or job.lease_owner != self.worker_id:
                return False
            job.lease_expires_at = now() + self.lease

            attempt = await self._current_attempt(session, job)
            if attempt is None:
                await self._complete(session, job)
                return False

            if attempt.status == StageStatus.WAITING_FOR_HUMAN.value:
                keep_going = await self._resolve_waiting(session, job, attempt)
                if keep_going is not None:
                    return keep_going
            if attempt.status == StageStatus.CANCELLED.value:
                await cancel_job_rows(session, job)
                return False
            if attempt.status == StageStatus.FAILED.value:
                job.status = JobStatus.FAILED.value
                self._release(job)
                await set_campaign_status(session, job.campaign_id, CampaignStatus.NEEDS_ATTENTION)
                return False
            if (attempt.status == StageStatus.RETRYING.value and attempt.retry_at
                    and attempt.retry_at > now()):
                job.status = JobStatus.QUEUED.value
                job.next_run_at = attempt.retry_at
                self._release(job)
                return False

            first_run = attempt.status in (StageStatus.PENDING.value, StageStatus.RETRYING.value)
            attempt.status = StageStatus.RUNNING.value
            attempt.started_at = attempt.started_at or now()
            job.current_stage = attempt.stage
            if first_run:
                await audit.record(session, type="stage.started", job_id=job.id,
                                   campaign_id=job.campaign_id,
                                   message=f"{attempt.stage} attempt {attempt.attempt}",
                                   data={"stage": attempt.stage, "attempt": attempt.attempt})
            session.expunge(job)
            session.expunge(attempt)

        stage = StageId(attempt.stage)
        handler = self.handlers[stage]
        ctx = StageContext(self.s, job, attempt)
        token = bind(stage=stage.value, attempt=attempt.attempt)
        try:
            outcome = await handler.run(ctx)
        except TransientError as exc:
            return await self._on_transient(job, attempt, exc)
        except HumanRequiredError as exc:
            return await self._on_human(job, attempt, exc)
        except BlockedError as exc:
            return await self._on_blocked(job, attempt, exc)
        except Exception as exc:
            log.exception("stage handler failed", stage=stage.value)
            return await self._on_bug(job, attempt, exc)
        finally:
            reset(token)
        return await self._apply(job, attempt, handler, outcome)

    # ------------------------------------------------------------------ attempts
    async def _current_attempt(self, session: AsyncSession, job: ProductionJob) -> WorkflowStage | None:
        rows = (await session.execute(
            select(WorkflowStage).where(WorkflowStage.job_id == job.id)
            .order_by(WorkflowStage.stage, WorkflowStage.attempt.desc())
        )).scalars().all()
        latest: dict[str, WorkflowStage] = {}
        for row in rows:
            latest.setdefault(row.stage, row)
        for stage in STAGE_ORDER:
            row = latest.get(stage.value)
            if row is None:
                attempt = WorkflowStage(job_id=job.id, stage=stage.value, attempt=1,
                                        status=StageStatus.PENDING.value)
                session.add(attempt)
                await session.flush()
                return attempt
            if not StageStatus(row.status).is_complete:
                return await session.get(WorkflowStage, row.id, with_for_update=True)
        return None

    async def _new_attempt(self, session: AsyncSession, job_id: uuid.UUID, stage: str,
                           status: StageStatus, retry_at: datetime | None = None) -> WorkflowStage:
        n = await session.scalar(select(func.max(WorkflowStage.attempt)).where(
            WorkflowStage.job_id == job_id, WorkflowStage.stage == stage)) or 0
        attempt = WorkflowStage(job_id=job_id, stage=stage, attempt=n + 1, status=status.value,
                                retry_at=retry_at)
        session.add(attempt)
        await session.flush()
        return attempt

    def _backoff(self, attempt_no: int) -> timedelta:
        s = self.s.settings
        seconds = min(s.stage_backoff_max_seconds, s.stage_backoff_base_seconds * 2 ** (attempt_no - 1))
        return timedelta(seconds=seconds + random.uniform(0, seconds / 4))  # noqa: S311

    def _release(self, job: ProductionJob) -> None:
        job.lease_owner = None
        job.lease_expires_at = None

    async def _resolve_waiting(self, session: AsyncSession, job: ProductionJob,
                               attempt: WorkflowStage) -> bool | None:
        approvals = (await session.execute(
            select(ApprovalRequest).where(ApprovalRequest.stage_attempt_id == attempt.id)
            .order_by(ApprovalRequest.created_at)
        )).scalars().all()
        if any(a.status == ApprovalStatus.PENDING.value for a in approvals):
            job.status = JobStatus.WAITING_FOR_HUMAN.value
            self._release(job)
            await set_campaign_status(session, job.campaign_id, CampaignStatus.NEEDS_ATTENTION)
            return False
        interventions = [a for a in approvals if a.kind == ApprovalKind.INTERVENTION.value]
        if interventions:
            last = interventions[-1]
            if last.status == ApprovalStatus.APPROVED.value:
                # This attempt stays on record as failed; a person asked for another one.
                attempt.status = StageStatus.FAILED.value
                attempt.next_permitted_action = f"RETRIED by {last.decided_by}"
                await self._new_attempt(session, job.id, attempt.stage, StageStatus.PENDING)
                await audit.record(session, type="stage.retry_approved", job_id=job.id,
                                   campaign_id=job.campaign_id, actor=last.decided_by or "human",
                                   message=f"{attempt.stage} will be attempted again")
                return True
            job.status = JobStatus.FAILED.value
            job.error = {"code": "REJECTED_BY_HUMAN",
                         "message": f"{attempt.stage}: intervention rejected"}
            attempt.status = StageStatus.FAILED.value
            self._release(job)
            await set_campaign_status(session, job.campaign_id, CampaignStatus.NEEDS_ATTENTION)
            return False
        # Gate approvals decided: run the handler again to read the decision.
        return None

    # ------------------------------------------------------------------ outcomes
    async def _apply(self, job: ProductionJob, attempt: WorkflowStage, handler: StageHandler,
                     outcome: StageOutcome) -> bool:
        async with self.s.db.transaction() as session:
            job = await session.get(ProductionJob, job.id, with_for_update=True)
            attempt = await session.get(WorkflowStage, attempt.id, with_for_update=True)
            if outcome.results:
                await persist_results(session, job.id, attempt.id, outcome.results)
            if outcome.kind is OutcomeKind.CONTINUE:
                return job.status == JobStatus.RUNNING.value

            if outcome.kind is OutcomeKind.WAIT_HUMAN:
                await self._request_approvals(session, job, attempt, outcome.approvals)
                attempt.status = StageStatus.WAITING_FOR_HUMAN.value
                attempt.next_permitted_action = "AWAIT_APPROVAL"
                job.status = JobStatus.WAITING_FOR_HUMAN.value
                self._release(job)
                await set_campaign_status(session, job.campaign_id, CampaignStatus.NEEDS_ATTENTION)
                await audit.record(session, type="stage.waiting", job_id=job.id,
                                   campaign_id=job.campaign_id,
                                   message=outcome.message or f"{attempt.stage} awaits a decision")
                return False

            attempt.status = StageStatus.VALIDATING.value
            await session.flush()
            all_results = await self._attempt_results(session, attempt.id)
            decision = gates.decide(all_results)
            attempt.status = decision.status.value
            attempt.output = outcome.output
            attempt.validated_at = attempt.finished_at = now()
            attempt.next_permitted_action = decision.action.value
            await audit.record(
                session, type="stage.validated", job_id=job.id, campaign_id=job.campaign_id,
                message=f"{attempt.stage}: {decision.status.value} — {decision.reason}",
                data={"stage": attempt.stage, "status": decision.status.value,
                      "failures": len(decision.failures)},
            )
            if decision.action is gates.GateAction.PROCEED:
                return True
            if (decision.action is gates.GateAction.RETRY and handler.auto_retry
                    and attempt.attempt < self.s.settings.stage_max_attempts):
                retry_at = now() + self._backoff(attempt.attempt)
                await self._new_attempt(session, job.id, attempt.stage, StageStatus.RETRYING,
                                        retry_at=retry_at)
                job.status = JobStatus.QUEUED.value
                job.next_run_at = retry_at
                self._release(job)
                await audit.record(session, type="stage.retry_scheduled", job_id=job.id,
                                   campaign_id=job.campaign_id,
                                   message=f"{attempt.stage} retry at {retry_at.isoformat()}",
                                   data={"reason": decision.reason})
                return False
            await self._intervene(session, job, attempt, decision.reason,
                                  {"failures": [f.model_dump(mode="json") for f in decision.failures[:20]]})
            return False

    async def _attempt_results(self, session: AsyncSession, attempt_id: uuid.UUID) -> list[RuleResult]:
        rows = (await session.execute(select(ValidationResult).where(
            ValidationResult.stage_attempt_id == attempt_id))).scalars().all()
        return [RuleResult(rule_id=r.rule_id, category=r.category, severity=r.severity,
                           outcome=r.outcome, message=r.message, judge=r.judge,
                           evidence=r.evidence, subject=r.subject, fix_hint=r.fix_hint,
                           data=r.data) for r in rows]

    async def _request_approvals(self, session: AsyncSession, job: ProductionJob,
                                 attempt: WorkflowStage, specs: list[ApprovalSpec]) -> None:
        for spec in specs:
            exists = await session.scalar(select(ApprovalRequest.id).where(
                ApprovalRequest.job_id == job.id, ApprovalRequest.kind == spec.kind.value,
                ApprovalRequest.subject == spec.subject,
                ApprovalRequest.status == ApprovalStatus.PENDING.value))
            if exists:
                continue
            session.add(ApprovalRequest(
                job_id=job.id, stage_attempt_id=attempt.id, kind=spec.kind.value,
                subject=spec.subject, title=spec.title, summary=spec.summary,
                payload=spec.payload, options=spec.options,
            ))
            await audit.record(session, type="approval.requested", job_id=job.id,
                               campaign_id=job.campaign_id, message=spec.title,
                               data={"kind": spec.kind.value, "subject": spec.subject})
        await session.flush()

    async def _intervene(self, session: AsyncSession, job: ProductionJob, attempt: WorkflowStage,
                         reason: str, payload: dict) -> None:
        attempt.status = StageStatus.WAITING_FOR_HUMAN.value
        attempt.next_permitted_action = "HUMAN"
        await self._request_approvals(session, job, attempt, [ApprovalSpec(
            kind=ApprovalKind.INTERVENTION, subject=f"stage:{attempt.stage}:{attempt.attempt}",
            title=f"{attempt.stage} needs a person",
            summary=reason[:1000], payload={**payload, "stage": attempt.stage,
                                            "attempt": attempt.attempt},
            options=["retry", "fail"],
        )])
        job.status = JobStatus.WAITING_FOR_HUMAN.value
        self._release(job)
        await set_campaign_status(session, job.campaign_id, CampaignStatus.NEEDS_ATTENTION)

    async def _record_error(self, session: AsyncSession, attempt: WorkflowStage,
                            exc: Exception) -> dict:
        error = exc.to_dict() if isinstance(exc, VidsProdError) else {
            "code": "INTERNAL", "type": type(exc).__name__, "message": str(exc)[:500],
            "traceback": traceback.format_exc()[-3000:],
        }
        attempt.error = error
        attempt.finished_at = now()
        return error

    async def _on_transient(self, job, attempt, exc: TransientError) -> bool:
        async with self.s.db.transaction() as session:
            job = await session.get(ProductionJob, job.id, with_for_update=True)
            attempt = await session.get(WorkflowStage, attempt.id, with_for_update=True)
            error = await self._record_error(session, attempt, exc)
            attempt.status = StageStatus.FAILED.value
            await audit.record(session, type="stage.error", job_id=job.id,
                               campaign_id=job.campaign_id,
                               message=f"{attempt.stage}: {exc.message}", data=error)
            if attempt.attempt < self.s.settings.stage_max_attempts:
                retry_at = now() + self._backoff(attempt.attempt)
                attempt.next_permitted_action = f"RETRY_AT {retry_at.isoformat()}"
                await self._new_attempt(session, job.id, attempt.stage, StageStatus.RETRYING,
                                        retry_at=retry_at)
                job.status = JobStatus.QUEUED.value
                job.next_run_at = retry_at
                self._release(job)
            else:
                await self._intervene(session, job, attempt,
                                      f"{exc.message} (after {attempt.attempt} attempts)",
                                      {"error": error})
        return False

    async def _on_human(self, job, attempt, exc: HumanRequiredError) -> bool:
        async with self.s.db.transaction() as session:
            job = await session.get(ProductionJob, job.id, with_for_update=True)
            attempt = await session.get(WorkflowStage, attempt.id, with_for_update=True)
            error = await self._record_error(session, attempt, exc)
            await audit.record(session, type="stage.needs_human", job_id=job.id,
                               campaign_id=job.campaign_id,
                               message=f"{attempt.stage}: {exc.message}", data=error)
            await self._intervene(session, job, attempt, exc.message, {"error": error})
        return False

    async def _on_blocked(self, job, attempt, exc: BlockedError) -> bool:
        async with self.s.db.transaction() as session:
            job = await session.get(ProductionJob, job.id, with_for_update=True)
            attempt = await session.get(WorkflowStage, attempt.id, with_for_update=True)
            error = await self._record_error(session, attempt, exc)
            attempt.status = StageStatus.FAILED.value
            attempt.next_permitted_action = f"BLOCKED {exc.code}"
            job.status = JobStatus.FAILED.value
            job.error = error
            self._release(job)
            await set_campaign_status(session, job.campaign_id, CampaignStatus.NEEDS_ATTENTION)
            await audit.record(session, type="stage.blocked", job_id=job.id,
                               campaign_id=job.campaign_id,
                               message=f"{attempt.stage} blocked: {exc.message}", data=error)
        return False

    async def _on_bug(self, job, attempt, exc: Exception) -> bool:
        async with self.s.db.transaction() as session:
            job = await session.get(ProductionJob, job.id, with_for_update=True)
            attempt = await session.get(WorkflowStage, attempt.id, with_for_update=True)
            error = await self._record_error(session, attempt, exc)
            attempt.status = StageStatus.FAILED.value
            attempt.next_permitted_action = "RETRY_AFTER_FIX"
            job.status = JobStatus.FAILED.value
            job.error = {k: v for k, v in error.items() if k != "traceback"}
            self._release(job)
            await set_campaign_status(session, job.campaign_id, CampaignStatus.NEEDS_ATTENTION)
            await audit.record(session, type="stage.crashed", job_id=job.id,
                               campaign_id=job.campaign_id,
                               message=f"{attempt.stage} failed unexpectedly: {type(exc).__name__}",
                               data={"type": type(exc).__name__, "message": str(exc)[:500]})
        return False

    async def _complete(self, session: AsyncSession, job: ProductionJob) -> None:
        job.status = JobStatus.COMPLETED.value
        job.current_stage = None
        job.finished_at = now()
        self._release(job)
        await set_campaign_status(session, job.campaign_id, CampaignStatus.DELIVERED)
        await audit.record(session, type="job.completed", job_id=job.id,
                           campaign_id=job.campaign_id, message="every stage passed validation")

    async def _finalise_cancel(self, session: AsyncSession, job: ProductionJob) -> None:
        await cancel_job_rows(session, job)


async def cancel_job_rows(session: AsyncSession, job: ProductionJob, actor: str = "system") -> None:
    """Cancel a job: open stage attempts and pending approvals are closed, history kept."""
    open_states = (StageStatus.PENDING.value, StageStatus.RUNNING.value,
                   StageStatus.VALIDATING.value, StageStatus.RETRYING.value,
                   StageStatus.WAITING_FOR_HUMAN.value)
    await session.execute(update(WorkflowStage).where(
        WorkflowStage.job_id == job.id, WorkflowStage.status.in_(open_states)
    ).values(status=StageStatus.CANCELLED.value, finished_at=now()))
    await session.execute(update(ApprovalRequest).where(
        ApprovalRequest.job_id == job.id, ApprovalRequest.status == ApprovalStatus.PENDING.value
    ).values(status=ApprovalStatus.CANCELLED.value))
    job.status = JobStatus.CANCELLED.value
    job.control = None
    job.finished_at = now()
    job.lease_owner = None
    job.lease_expires_at = None
    await set_campaign_status(session, job.campaign_id, CampaignStatus.READY)
    await audit.record(session, type="job.cancelled", actor=actor, job_id=job.id,
                       campaign_id=job.campaign_id, message="production cancelled")
