"""Selective regeneration, edits, bounded retries, human review, crash recovery, duplicate
prevention, export and timeline verification, budget blocking."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from tests.conftest import (
    approve_pending,
    build_container,
    create_campaign,
    dump,
    job_row,
    run_to_rest,
)
from vidsprod.db.models import (
    ActionIntent,
    ApprovalRequest,
    BudgetEntry,
    Export,
    Scene,
    SceneTake,
    SceneVersion,
    TimelineOperation,
)
from vidsprod.domain.enums import (
    ApprovalKind,
    ApprovalStatus,
    IntentStatus,
    JobStatus,
    SceneStatus,
)
from vidsprod.vids.mock import BUILTIN_SCENARIOS, MockScenario, SimulatedCrash

pytestmark = pytest.mark.db


async def _through_plan(c, campaign=None):
    campaign = campaign or await create_campaign(c)
    job = await c.control.start(campaign.id, actor="producer")
    await run_to_rest(c)
    assert [a.kind for a in await approve_pending(c)] == ["PLAN"], await dump(c, job.id)
    return job


async def _scene_takes(c, job_id):
    async with c.db.session() as session:
        scenes = (await session.execute(select(Scene).where(Scene.job_id == job_id)
                                        .order_by(Scene.order_index))).scalars().all()
        out = {}
        for s in scenes:
            takes = (await session.execute(select(SceneTake).where(SceneTake.scene_id == s.id)
                                           .order_by(SceneTake.take_no))).scalars().all()
            out[s.scene_key] = (s, list(takes))
        return out


async def test_only_the_defective_scene_is_regenerated_and_a_minor_defect_is_edited(settings, clean_db):
    c = await build_container(settings, clean_db, scenario=BUILTIN_SCENARIOS["realistic"])
    try:
        job = await _through_plan(c)
        await run_to_rest(c)
        scenes = await _scene_takes(c, job.id)
        s02, t02 = scenes["S02"]
        assert [(t.origin, t.verdict, t.status) for t in t02] == [
            ("GENERATE", "NEEDS_REGENERATION", "REJECTED"), ("GENERATE", "PASS", "ACCEPTED")]
        s03, t03 = scenes["S03"]
        assert [(t.origin, t.verdict) for t in t03] == [("GENERATE", "NEEDS_EDIT"), ("EDIT", "PASS")]
        assert t03[1].parent_take_id == t03[0].id and t03[1].edit_instruction
        for key in ("S01", "S04", "S05"):
            scene, takes = scenes[key]
            assert len(takes) == 1 and takes[0].locked_at is not None, f"{key} was touched"
        # The regeneration was steered, and the earlier version is kept.
        async with c.db.session() as session:
            versions = (await session.execute(select(SceneVersion).where(
                SceneVersion.scene_id == s02.id).order_by(SceneVersion.version))).scalars().all()
        assert len(versions) == 2
        assert "Make sure that:" in versions[1].prompt["prompt"]
        assert "black" in versions[1].reason.lower() or "lit" in versions[1].reason.lower()
        assert c.adapter._global["generations"] == 7  # 5 scenes + 1 regeneration + 1 edit
    finally:
        await c.close()


async def test_persistent_failure_stops_at_the_take_limit_and_asks_a_person(settings, clean_db):
    scenario = MockScenario(name="persistent", clip_defects={"S01": ["VISUAL_MAJOR"] * 10})
    c = await build_container(settings, clean_db, scenario=scenario)
    try:
        job = await _through_plan(c)
        await run_to_rest(c)
        row = await job_row(c, job.id)
        assert row.status == JobStatus.WAITING_FOR_HUMAN.value, await dump(c, job.id)
        scenes = await _scene_takes(c, job.id)
        s01, takes = scenes["S01"]
        assert s01.status == SceneStatus.HUMAN_REVIEW.value
        assert len(takes) == settings.max_takes_per_scene  # bounded: 3, not more
        # Every other scene was still produced while S01 waited.
        assert all(scenes[k][0].status == SceneStatus.ACCEPTED.value for k in scenes if k != "S01")
        async with c.db.session() as session:
            review = await session.scalar(select(ApprovalRequest).where(
                ApprovalRequest.kind == ApprovalKind.SCENE_REVIEW.value))
        assert review.status == ApprovalStatus.PENDING.value and review.payload["evidence"]
        # A person accepts the last take; production continues to compliance.
        await c.control.decide(review.id, approve=True, actor="lead", reason="acceptable",
                               payload={"action": "accept"})
        await run_to_rest(c)
        scenes = await _scene_takes(c, job.id)
        assert scenes["S01"][0].status == SceneStatus.ACCEPTED.value
        assert (await job_row(c, job.id)).status == JobStatus.WAITING_FOR_HUMAN.value
        assert [a.kind for a in await approve_pending(c)] == ["COMPLIANCE"]
        await run_to_rest(c)
        assert (await job_row(c, job.id)).status == JobStatus.COMPLETED.value
    finally:
        await c.close()


async def test_a_human_can_ask_for_one_more_take_with_an_instruction(settings, clean_db):
    scenario = MockScenario(name="p", clip_defects={"S01": ["VISUAL_MAJOR"] * 3})
    c = await build_container(settings, clean_db, scenario=scenario)
    try:
        job = await _through_plan(c)
        await run_to_rest(c)
        async with c.db.session() as session:
            review = await session.scalar(select(ApprovalRequest).where(
                ApprovalRequest.kind == ApprovalKind.SCENE_REVIEW.value))
        await c.control.decide(review.id, approve=True, actor="lead", reason="one more",
                               payload={"action": "regenerate", "instruction": "Frame the mother on the left"})
        await run_to_rest(c)
        scenes = await _scene_takes(c, job.id)
        s01, takes = scenes["S01"]
        assert len(takes) == 4 and takes[-1].locked_at is not None
        assert "Frame the mother on the left" in takes[-1].prompt_text
    finally:
        await c.close()


async def test_a_crash_after_generation_does_not_generate_twice(settings, clean_db):
    scenario = MockScenario(name="crash", crash_after=["GENERATE_CLIP:S02:1"])
    settings = settings.model_copy(update={"worker_lease_seconds": 2})
    c = await build_container(settings, clean_db, scenario=scenario, worker_id="worker-a")
    job = await _through_plan(c)
    with pytest.raises(SimulatedCrash):
        await run_to_rest(c)
    generations_before = c.adapter._global["generations"]
    await c.db.dispose()  # the process "died": its lease is never released

    # A restarted worker, with the same persistent mock project on disk.
    c2 = await build_container(settings, clean_db, scenario=MockScenario(name="after"),
                               worker_id="worker-b")
    try:
        async with c2.db.session() as session:
            pending = await session.scalar(select(func.count()).select_from(ActionIntent).where(
                ActionIntent.status == IntentStatus.PENDING.value))
        assert pending == 1, "the lost generation is still an open intent"
        await asyncio.sleep(2.5)  # worker-a stopped renewing; let its lease expire
        await run_to_rest(c2)
        async with c2.db.session() as session:
            reconciled = await session.scalar(select(func.count()).select_from(ActionIntent).where(
                ActionIntent.status == IntentStatus.RECONCILED.value))
            s02 = await session.scalar(select(Scene).where(Scene.job_id == job.id, Scene.scene_key == "S02"))
            takes = await session.scalar(select(func.count()).select_from(SceneTake).where(
                SceneTake.scene_id == s02.id))
            spent = await session.scalar(select(func.count()).select_from(BudgetEntry).where(
                BudgetEntry.status == "SPENT"))
        assert reconciled == 1
        assert takes == 1, "the clip made before the crash was used, not made again"
        n_scenes = len(await _scene_takes(c2, job.id))
        assert c2.adapter._global["generations"] == generations_before + (n_scenes - 2)
        assert spent == n_scenes
        assert (await job_row(c2, job.id)).status == JobStatus.WAITING_FOR_HUMAN.value  # compliance
    finally:
        await c2.close()


async def test_two_workers_never_run_the_same_job(settings, clean_db):
    c1 = await build_container(settings, clean_db, worker_id="w1")
    c2 = await build_container(settings, clean_db, worker_id="w2")
    try:
        campaign = await create_campaign(c1)
        await c1.control.start(campaign.id, actor="p")
        claims = await asyncio.gather(c1.engine.claim_next(), c2.engine.claim_next())
        assert sum(1 for x in claims if x is not None) == 1
    finally:
        await c1.close()
        await c2.close()


async def test_a_truncated_export_is_caught_and_exported_again(settings, clean_db):
    scenario = MockScenario(name="t", export_defects=["TRUNCATED"])
    c = await build_container(settings, clean_db, scenario=scenario)
    try:
        job = await _through_plan(c)
        await run_to_rest(c)
        async with c.db.session() as session:
            exports = (await session.execute(select(Export).where(Export.job_id == job.id)
                                             .order_by(Export.attempt))).scalars().all()
        assert [e.status for e in exports] == ["FAILED", "VERIFIED"], await dump(c, job.id)
        failed = [v for v in exports[0].verification if v["outcome"] != "PASS"]
        assert any(v["rule_id"] == "export.duration" for v in failed)
    finally:
        await c.close()


async def test_a_timeline_edit_that_did_not_persist_is_detected_and_applied_again(settings, clean_db):
    scenario = MockScenario(name="t", timeline_op_failures={"TEXT_OVERLAY": 1})
    c = await build_container(settings, clean_db, scenario=scenario)
    try:
        job = await _through_plan(c)
        await run_to_rest(c)
        async with c.db.session() as session:
            ops = (await session.execute(select(TimelineOperation).where(
                TimelineOperation.job_id == job.id, TimelineOperation.kind == "TEXT_OVERLAY"))).scalars().all()
        assert all(o.status == "VERIFIED" for o in ops)
        assert any(o.result["attempts"] == 2 for o in ops), "the lost edit was re-applied"
    finally:
        await c.close()


async def test_an_exhausted_budget_blocks_the_scene_and_resumes_after_a_person_acts(tmp_path, clean_db):
    from tests.conftest import make_settings

    tight = make_settings(tmp_path, generation_campaign_limit=3)
    c = await build_container(tight, clean_db)
    try:
        job = await _through_plan(c)
        await run_to_rest(c)
        assert (await job_row(c, job.id)).status == JobStatus.WAITING_FOR_HUMAN.value
        scenes = await _scene_takes(c, job.id)
        blocked = [k for k, (s, _) in scenes.items() if s.status == SceneStatus.BLOCKED.value]
        assert blocked == ["S04"], await dump(c, job.id)
        async with c.db.session() as session:
            intervention = await session.scalar(select(ApprovalRequest).where(
                ApprovalRequest.kind == ApprovalKind.INTERVENTION.value,
                ApprovalRequest.status == ApprovalStatus.PENDING.value))
        assert intervention.payload["error"]["code"] == "BUDGET_EXHAUSTED"
    finally:
        await c.close()
    # The limit is raised (a configuration change by a person), then retry.
    c = await build_container(tight.model_copy(update={"generation_campaign_limit": 20}), clean_db)
    try:
        await approve_pending(c, kinds={ApprovalKind.INTERVENTION})
        await run_to_rest(c)
        scenes = await _scene_takes(c, job.id)
        assert all(s.status == SceneStatus.ACCEPTED.value for s, _ in scenes.values())
        assert (await job_row(c, job.id)).status == JobStatus.WAITING_FOR_HUMAN.value  # compliance
    finally:
        await c.close()


async def test_an_expired_session_waits_for_sign_in_then_continues(settings, clean_db):
    c = await build_container(settings, clean_db, scenario=MockScenario(name="s", session="EXPIRED_ONCE"))
    try:
        job = await _through_plan(c)
        await run_to_rest(c)
        async with c.db.session() as session:
            waiting = await session.scalar(select(ApprovalRequest).where(
                ApprovalRequest.kind == ApprovalKind.INTERVENTION.value,
                ApprovalRequest.status == ApprovalStatus.PENDING.value))
        assert waiting.payload["error"]["code"] == "SESSION_EXPIRED"
        await approve_pending(c, kinds={ApprovalKind.INTERVENTION})  # "I've signed in"
        await run_to_rest(c)
        assert (await job_row(c, job.id)).status == JobStatus.WAITING_FOR_HUMAN.value
        assert [a.kind for a in await approve_pending(c)] == ["COMPLIANCE"]
    finally:
        await c.close()
