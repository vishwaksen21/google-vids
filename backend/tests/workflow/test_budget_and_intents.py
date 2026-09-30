"""The generation budget and the intent log's less common paths."""

from __future__ import annotations

from datetime import UTC, datetime

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
from vidsprod.db.models import ActionIntent, ApprovalRequest, BudgetEntry, Scene
from vidsprod.domain.enums import (
    ApprovalKind,
    ApprovalStatus,
    BudgetEntryStatus,
    IntentKind,
    IntentStatus,
    JobStatus,
    SceneStatus,
)
from vidsprod.errors import AmbiguousIntentError, BudgetExhaustedError
from vidsprod.vids.mock import MockScenario
from vidsprod.vids.port import ReconcileResult, ReconcileStatus
from vidsprod.workflow import budget
from vidsprod.workflow.intents import BudgetClaim

pytestmark = pytest.mark.db


def test_the_budget_month_follows_pacific_time():
    # Vids resets at 00:00 Pacific. On 1 October California is on PDT (UTC-7): 06:30 UTC is
    # still 30 September there, 07:30 UTC is already the new month.
    assert budget.current_period(datetime(2026, 10, 1, 6, 30, tzinfo=UTC)) == "2026-09"
    assert budget.current_period(datetime(2026, 10, 1, 7, 30, tzinfo=UTC)) == "2026-10"


async def test_monthly_and_campaign_limits_are_enforced(tmp_path, clean_db):
    from tests.conftest import make_settings

    s = make_settings(tmp_path, generation_monthly_limit=3, generation_campaign_limit=2)
    c = await build_container(s, clean_db)
    try:
        a = await create_campaign(c, name="A")
        b = await create_campaign(c, name="B")
        job_a = await c.control.start(a.id, actor="p")
        job_b = await c.control.start(b.id, actor="p")
        async with c.db.transaction() as session:
            for _ in range(2):
                await budget.reserve(session, s, account_key="acct", job_id=job_a.id,
                                     campaign_id=a.id, scene_id=None, purpose="t")
        with pytest.raises(BudgetExhaustedError, match="campaign"):
            async with c.db.transaction() as session:
                await budget.reserve(session, s, account_key="acct", job_id=job_a.id,
                                     campaign_id=a.id, scene_id=None, purpose="t")
        async with c.db.transaction() as session:
            entry = await budget.reserve(session, s, account_key="acct", job_id=job_b.id,
                                         campaign_id=b.id, scene_id=None, purpose="t")
        with pytest.raises(BudgetExhaustedError, match="monthly"):
            async with c.db.transaction() as session:
                await budget.reserve(session, s, account_key="acct", job_id=job_b.id,
                                     campaign_id=b.id, scene_id=None, purpose="t")
        async with c.db.transaction() as session:  # a released reservation frees the slot
            await budget.settle(session, entry.id, BudgetEntryStatus.RELEASED, "did not happen")
        async with c.db.transaction() as session:
            await budget.reserve(session, s, account_key="acct", job_id=job_b.id,
                                 campaign_id=b.id, scene_id=None, purpose="t")
            u = await budget.usage(session, s, "acct", b.id)
        assert u["monthly_used"] == 3 and u["campaign_used"] == 1
    finally:
        await c.close()


async def test_a_generation_timeout_is_reconciled_as_not_happened_and_released(settings, clean_db):
    scenario = MockScenario(name="t", generate_errors={"S02": {1: "TIMEOUT"}})
    c = await build_container(settings, clean_db, scenario=scenario)
    try:
        campaign = await create_campaign(c)
        job = await c.control.start(campaign.id, actor="p")
        await run_to_rest(c)
        await approve_pending(c)
        await run_to_rest(c)
        assert (await job_row(c, job.id)).status == JobStatus.WAITING_FOR_HUMAN.value, await dump(c, job.id)
        async with c.db.session() as session:
            abandoned = (await session.execute(select(ActionIntent).where(
                ActionIntent.status == IntentStatus.ABANDONED.value))).scalars().all()
            released = await session.scalar(select(func.count()).select_from(BudgetEntry).where(
                BudgetEntry.status == BudgetEntryStatus.RELEASED.value))
        assert [i.target for i in abandoned] == ["S02:gen1"]
        assert "not found" in abandoned[0].error["reason"]
        assert released == 1, "a generation that verifiably did not happen is not charged"
        assert "SCENE_PRODUCTION#2" in await dump(c, job.id), "the stage was retried after the timeout"
    finally:
        await c.close()


async def test_a_refused_prompt_releases_the_budget_and_asks_a_person(settings, clean_db):
    scenario = MockScenario(name="r", generate_errors={"S01": {1: "REFUSED"}})
    c = await build_container(settings, clean_db, scenario=scenario)
    try:
        campaign = await create_campaign(c)
        job = await c.control.start(campaign.id, actor="p")
        await run_to_rest(c)
        await approve_pending(c)
        await run_to_rest(c)
        async with c.db.session() as session:
            s01 = await session.scalar(select(Scene).where(Scene.job_id == job.id, Scene.scene_key == "S01"))
            released = await session.scalar(select(func.count()).select_from(BudgetEntry).where(
                BudgetEntry.status == BudgetEntryStatus.RELEASED.value))
            review = await session.scalar(select(ApprovalRequest).where(
                ApprovalRequest.kind == ApprovalKind.SCENE_REVIEW.value))
        assert s01.status == SceneStatus.HUMAN_REVIEW.value and released == 1
        assert review.status == ApprovalStatus.PENDING.value
    finally:
        await c.close()


async def test_an_unknown_outcome_waits_for_a_person_who_records_what_they_saw(settings, clean_db):
    c = await build_container(settings, clean_db)
    try:
        campaign = await create_campaign(c)
        job = await c.control.start(campaign.id, actor="p")
        key = f"{job.id}:GENERATE_CLIP:S01:gen1#1"
        async with c.db.transaction() as session:
            entry = await budget.reserve(session, settings, account_key=c.adapter.account_key,
                                         job_id=job.id, campaign_id=campaign.id, scene_id=None,
                                         purpose="t")
            session.add(ActionIntent(job_id=job.id, kind=IntentKind.GENERATE_CLIP.value,
                                     target="S01:gen1", idempotency_key=key,
                                     request={"budget_entry_id": str(entry.id)}))

        async def unknown(*args, **kwargs):
            return ReconcileResult(status=ReconcileStatus.UNKNOWN, note="cannot tell")

        c.adapter.reconcile = unknown

        async def never(key):
            raise AssertionError("must not act while the outcome is unknown")

        async def noop(session, result):
            return None

        with pytest.raises(AmbiguousIntentError) as raised:
            await c.intents.run(job_id=job.id, kind=IntentKind.GENERATE_CLIP, target="S01:gen1",
                                request={}, action=never, on_confirm=noop,
                                claim=BudgetClaim(c.adapter.account_key, campaign.id, None, "t"))
        intent_id = raised.value.details["intent_id"]
        await c.intents.resolve_by_human(__import__("uuid").UUID(intent_id), happened=True, actor="lead")
        async with c.db.session() as session:
            intent = await session.scalar(select(ActionIntent).where(ActionIntent.idempotency_key == key))
            spent = await session.get(BudgetEntry, entry.id)
        assert intent.status == IntentStatus.ABANDONED.value and "lead" in intent.error["reason"]
        assert spent.status == BudgetEntryStatus.SPENT.value, "it happened, so it was paid for"
    finally:
        await c.close()
