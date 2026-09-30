"""A full production on the mock adapter and fake provider: every stage runs, validates
and passes, with the two human gates approved along the way."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from tests.conftest import approve_pending, create_campaign, dump, job_row, run_to_rest
from vidsprod.db.models import (
    Delivery,
    Export,
    JobDocument,
    Scene,
    SceneTake,
    ValidationResult,
    WorkflowStage,
)
from vidsprod.domain.enums import ApprovalKind, JobStatus, SceneStatus, StageStatus

pytestmark = pytest.mark.db


async def test_a_clean_production_completes_with_evidence_at_every_stage(container):
    c = container
    campaign = await create_campaign(c)
    job = await c.control.start(campaign.id, actor="producer")

    await run_to_rest(c)
    assert (await job_row(c, job.id)).status == JobStatus.WAITING_FOR_HUMAN.value
    assert [a.kind for a in await approve_pending(c)] == [ApprovalKind.PLAN.value], await dump(c, job.id)

    await run_to_rest(c)
    row = await job_row(c, job.id)
    assert row.status == JobStatus.WAITING_FOR_HUMAN.value, await dump(c, job.id)
    assert [a.kind for a in await approve_pending(c)] == [ApprovalKind.COMPLIANCE.value], await dump(c, job.id)

    await run_to_rest(c)
    row = await job_row(c, job.id)
    assert row.status == JobStatus.COMPLETED.value, await dump(c, job.id)

    async with c.db.session() as session:
        stages = (await session.execute(select(WorkflowStage).where(
            WorkflowStage.job_id == job.id))).scalars().all()
        assert {s.status for s in stages} <= {StageStatus.PASSED.value,
                                              StageStatus.PASSED_WITH_WARNINGS.value}
        # No stage passed without evidence.
        for stage in stages:
            n = await session.scalar(select(func.count()).select_from(ValidationResult).where(
                ValidationResult.stage_attempt_id == stage.id))
            assert n > 0, f"{stage.stage} passed with no validation results"
        scenes = (await session.execute(select(Scene).where(Scene.job_id == job.id))).scalars().all()
        assert scenes and all(s.status == SceneStatus.ACCEPTED.value for s in scenes)
        takes = (await session.execute(select(SceneTake))).scalars().all()
        assert len(takes) == len(scenes), "a clean run regenerates nothing"
        assert all(t.locked_at is not None for t in takes)
        export = await session.scalar(select(Export).where(Export.job_id == job.id))
        assert export.status == "VERIFIED" and export.simulated
        delivery = await session.scalar(select(Delivery).where(Delivery.job_id == job.id))
        assert delivery.status == "STORED" and delivery.simulated
        kinds = set((await session.execute(select(JobDocument.kind).where(
            JobDocument.job_id == job.id))).scalars())
        assert {"CAMPAIGN_BRIEF", "CREATIVE_PLAN", "SCRIPT", "SCENE_PLAN", "PROMPTS",
                "TIMELINE_PLAN", "QA_REPORT"} <= kinds
