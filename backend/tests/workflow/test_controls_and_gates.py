"""Pause, resume, cancel, retry; approval rejection and invalidation; database guarantees."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import approve_pending, create_campaign, dump, job_row, run_to_rest
from vidsprod.db.models import (
    ApprovalRequest,
    AuditEvent,
    JobDocument,
    ProductionJob,
    SceneTake,
    WorkflowStage,
)
from vidsprod.domain.enums import ApprovalKind, JobStatus, StageId, StageStatus
from vidsprod.errors import ConflictError

pytestmark = pytest.mark.db


async def test_pause_takes_effect_at_the_next_checkpoint_and_resume_continues(container):
    c = container
    job = await c.control.start((await create_campaign(c)).id, actor="p")
    job_id = await c.engine.claim_next()
    assert job_id == job.id
    assert await c.engine.step(job_id)  # INTAKE ran
    await c.control.pause(job_id, actor="p")
    assert not await c.engine.step(job_id)  # the checkpoint honours the pause
    assert (await job_row(c, job_id)).status == JobStatus.PAUSED.value
    assert await c.engine.run_until_idle() == 0, "a paused job is not claimed"
    await c.control.resume(job_id, actor="p")
    await run_to_rest(c)
    assert (await job_row(c, job_id)).status == JobStatus.WAITING_FOR_HUMAN.value  # plan approval


async def test_cancel_closes_open_stages_and_approvals_and_keeps_history(container):
    c = container
    job = await c.control.start((await create_campaign(c)).id, actor="p")
    await run_to_rest(c)
    await c.control.cancel(job.id, actor="p")
    row = await job_row(c, job.id)
    assert row.status == JobStatus.CANCELLED.value
    async with c.db.session() as session:
        approvals = (await session.execute(select(ApprovalRequest))).scalars().all()
        stages = (await session.execute(select(WorkflowStage).where(
            WorkflowStage.job_id == job.id))).scalars().all()
    assert {a.status for a in approvals} == {"CANCELLED"}
    assert any(s.status == StageStatus.PASSED.value for s in stages), "history is kept"
    with pytest.raises(ConflictError):
        await c.control.cancel(job.id, actor="p")
    # A new production can start once the old one is cancelled.
    await c.control.start(row.campaign_id, actor="p")


async def test_a_rejected_plan_fails_the_job_and_names_the_reviewer(container):
    c = container
    job = await c.control.start((await create_campaign(c)).id, actor="p")
    await run_to_rest(c)
    async with c.db.session() as session:
        approval = await session.scalar(select(ApprovalRequest))
    await c.control.decide(approval.id, approve=False, actor="compliance lead", reason="tone is off")
    await run_to_rest(c)
    row = await job_row(c, job.id)
    assert row.status == JobStatus.WAITING_FOR_HUMAN.value, await dump(c, job.id)
    async with c.db.session() as session:
        attempt = await session.scalar(select(WorkflowStage).where(
            WorkflowStage.job_id == job.id, WorkflowStage.stage == StageId.PLAN_APPROVAL.value))
        assert attempt.status == StageStatus.WAITING_FOR_HUMAN.value
    assert "compliance lead" in await dump(c, job.id)


async def test_an_approval_is_invalid_once_what_it_approved_changes(container):
    c = container
    job = await c.control.start((await create_campaign(c)).id, actor="p")
    await run_to_rest(c)
    async with c.db.transaction() as session:  # the script is revised after the request
        doc = await session.scalar(select(JobDocument).where(JobDocument.kind == "SCRIPT"))
        session.add(JobDocument(job_id=job.id, kind="SCRIPT", version=doc.version + 1,
                                content={**doc.content, "lines": doc.content["lines"][:1]},
                                content_hash="changed"))
    await approve_pending(c)
    await run_to_rest(c)
    assert "no longer applies" in await dump(c, job.id)


async def test_retry_reruns_a_stage_and_everything_after_it_without_rewriting_history(container):
    c = container
    job = await c.control.start((await create_campaign(c)).id, actor="p")
    await run_to_rest(c)
    await approve_pending(c)
    await run_to_rest(c)  # to compliance
    await c.control.retry(job.id, actor="p", stage=StageId.FINAL_QA)
    await run_to_rest(c)
    async with c.db.session() as session:
        attempts = (await session.execute(select(WorkflowStage.attempt).where(
            WorkflowStage.job_id == job.id, WorkflowStage.stage == StageId.FINAL_QA.value))).scalars().all()
    assert sorted(attempts) == [1, 2]


async def test_planning_cannot_be_redone_after_production_started(container):
    c = container
    job = await c.control.start((await create_campaign(c)).id, actor="p")
    await run_to_rest(c)
    await approve_pending(c)
    await run_to_rest(c)
    with pytest.raises(ConflictError, match="discard accepted scenes"):
        await c.control.retry(job.id, actor="p", stage=StageId.SCRIPT)


async def test_the_database_refuses_a_second_active_production(container):
    c = container
    campaign = await create_campaign(c)
    await c.control.start(campaign.id, actor="p")
    with pytest.raises(ConflictError):
        await c.control.start(campaign.id, actor="p")


async def test_a_locked_take_cannot_be_changed_or_deleted_even_directly(container):
    c = container
    await c.control.start((await create_campaign(c)).id, actor="p")
    await run_to_rest(c)
    await approve_pending(c)
    await run_to_rest(c)
    async with c.db.session() as session:
        take = await session.scalar(select(SceneTake).where(SceneTake.locked_at.is_not(None)))
    with pytest.raises(DBAPIError, match="locked"):
        async with c.db.transaction() as session:
            await session.execute(update(SceneTake).where(SceneTake.id == take.id)
                                  .values(clip_ref={"clip_id": "swapped"}))
    with pytest.raises(DBAPIError, match="locked"):
        async with c.db.transaction() as session:
            await session.execute(text("DELETE FROM scene_take WHERE id = :id"), {"id": take.id})
    with pytest.raises((DBAPIError, IntegrityError)):
        async with c.db.transaction() as session:  # a second locked take for the same scene
            other = await session.scalar(select(SceneTake).where(
                SceneTake.scene_id == take.scene_id, SceneTake.id != take.id))
            if other is None:
                raise IntegrityError("no second take to lock", None, Exception())
            await session.execute(update(SceneTake).where(SceneTake.id == other.id)
                                  .values(locked_at=take.locked_at))


async def test_the_audit_trail_is_append_only(container):
    c = container
    await c.control.start((await create_campaign(c)).id, actor="p")
    with pytest.raises(DBAPIError, match="append-only"):
        async with c.db.transaction() as session:
            await session.execute(update(AuditEvent).values(message="rewritten"))


async def test_every_approval_gate_records_who_decided(container):
    c = container
    job = await c.control.start((await create_campaign(c)).id, actor="p")
    await run_to_rest(c)
    await approve_pending(c)
    await run_to_rest(c)
    await approve_pending(c)
    await run_to_rest(c)
    async with c.db.session() as session:
        approvals = (await session.execute(select(ApprovalRequest).where(
            ApprovalRequest.job_id == job.id))).scalars().all()
        events = (await session.execute(select(AuditEvent).where(
            AuditEvent.type == "approval.decided"))).scalars().all()
    assert {a.kind for a in approvals} == {ApprovalKind.PLAN.value, ApprovalKind.COMPLIANCE.value}
    assert all(a.decided_by == "reviewer" for a in approvals)
    assert len(events) == 2 and all(e.actor == "reviewer" for e in events)
    assert (await job_row(c, job.id)).status == JobStatus.COMPLETED.value


async def test_a_stale_lease_is_reclaimed_after_a_worker_dies(container):
    c = container
    job = await c.control.start((await create_campaign(c)).id, actor="p")
    async with c.db.transaction() as session:
        await session.execute(update(ProductionJob).where(ProductionJob.id == job.id).values(
            status="RUNNING", lease_owner="dead-worker",
            lease_expires_at=text("now() - interval '1 minute'")))
    assert await c.engine.claim_next() == job.id


async def test_a_brief_without_a_cta_still_plans(container):
    """Regression: 'Talk to us today.' failed the CTA check because the full stop stayed
    attached to 'today', so a brief without an explicit CTA could never pass SCRIPT."""
    from tests.conftest import BRIEF

    c = container
    brief = {k: v for k, v in BRIEF.items() if k != "cta"}
    job = await c.control.start((await create_campaign(c, brief=brief)).id, actor="p")
    await run_to_rest(c)
    assert [a.kind for a in await approve_pending(c)] == ["PLAN"], await dump(c, job.id)


async def test_the_conflict_names_the_active_production(container):
    c = container
    campaign = await create_campaign(c)
    job = await c.control.start(campaign.id, actor="p")
    with pytest.raises(ConflictError) as raised:
        await c.control.start(campaign.id, actor="p")
    assert raised.value.details["active_job_id"] == str(job.id)
