"""Shared fixtures.

Database tests run against the real Postgres test database (TEST_DATABASE_URL), migrated
once per session with Alembic and truncated between tests. Nothing here talks to Google or
to an AI provider: the mock Vids adapter and the fake provider are used throughout.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from vidsprod.config import BACKEND_DIR, Settings
from vidsprod.container import Container
from vidsprod.db.models import ApprovalRequest, BrandProfile, Campaign, CampaignBrief, ProductionJob
from vidsprod.domain.enums import ApprovalKind, ApprovalStatus, CampaignStatus
from vidsprod.llm.fake import FakeProvider
from vidsprod.vids.mock import BUILTIN_SCENARIOS, MockScenario, MockVidsAdapter

_BASE = Settings()
TEST_DB_URL = _BASE.test_database_url.get_secret_value() if _BASE.test_database_url else None

TABLES = [
    "validation_result", "scene_take", "scene_version", "scene", "timeline_op", "action_intent",
    "budget_entry", "budget_account", "delivery", "export", "approval_request", "job_document",
    "workflow_stage", "artifact", "production_job", "campaign_brief", "campaign",
    "brand_profile", "browser_session", "audit_event", "ai_request",
]


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        app_env="test", ai_provider="fake", google_vids_mode="mock",
        artifact_dir=tmp_path / "artifacts", mock_vids_state_dir=tmp_path / "mock-vids",
        browser_profile_dir=tmp_path / "profile", stage_backoff_base_seconds=0,
        stage_backoff_max_seconds=0, ai_backoff_base_seconds=0, worker_lease_seconds=30,
        api_token="test-token", locator_catalogue_path=tmp_path / "recon" / "locators.json",
    )
    values.update(overrides)
    # Isolated from backend/.env: tests never inherit a developer's local configuration.
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="session")
def migrated_db() -> str:
    if TEST_DB_URL is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.cmd_opts = type("o", (), {"x": [f"db_url={TEST_DB_URL}"]})()
    command.upgrade(cfg, "head")
    return TEST_DB_URL


@pytest.fixture
async def clean_db(migrated_db: str) -> str:
    engine = create_async_engine(migrated_db)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    return migrated_db


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


async def build_container(settings: Settings, db_url: str, *, scenario: MockScenario | None = None,
                          provider: FakeProvider | None = None, adapter=None,
                          worker_id: str = "test-worker") -> Container:
    adapter = adapter or MockVidsAdapter(settings.mock_vids_state_dir,
                                         scenario=scenario or BUILTIN_SCENARIOS["clean"],
                                         logo_path=settings.logo_path)
    return await Container.build(settings, db_url=db_url, adapter=adapter,
                                 provider=provider or FakeProvider(), worker_id=worker_id)


@pytest.fixture
async def container(settings: Settings, clean_db: str):
    c = await build_container(settings, clean_db)
    yield c
    await c.close()


BRIEF = {
    "raw_brief": "Reassure young families that one Prudential family floater policy looks "
                 "after everyone at home, and invite them to talk to an advisor.",
    "objective": "Reassure families that one policy covers everyone",
    "audience": "Parents aged 30 to 45 in Indian cities",
    "product": "Prudential Family Floater health cover",
    "key_message": "One policy for the whole family",
    "cta": "Talk to a Prudential advisor today",
    "tone": "Warm and reassuring",
    "required_claims": [{"text": "One policy covers every member of the family named on it.",
                         "fact_id": "PRU-HEALTH-FF-001"}],
    "output": {"aspect_ratio": "16:9", "min_duration_seconds": 15, "max_duration_seconds": 45},
}


async def create_campaign(c: Container, brief: dict | None = None, name: str = "Family floater launch"):
    import hashlib
    import json

    async with c.db.transaction() as session:
        profile = await session.scalar(select(BrandProfile))
        campaign = Campaign(name=name, brand_profile_id=profile.id, owner="tester",
                            status=CampaignStatus.READY.value, current_brief_version=1)
        session.add(campaign)
        await session.flush()
        content = brief or BRIEF
        session.add(CampaignBrief(campaign_id=campaign.id, version=1, content=content,
                                  content_hash=hashlib.sha256(json.dumps(content).encode()).hexdigest(),
                                  created_by="tester"))
    return campaign


async def approve_pending(c: Container, *, kinds=None, scene_action: str = "accept",
                          reason: str = "looks right") -> list[ApprovalRequest]:
    async with c.db.session() as session:
        pending = (await session.execute(select(ApprovalRequest).where(
            ApprovalRequest.status == ApprovalStatus.PENDING.value))).scalars().all()
    decided = []
    for approval in pending:
        if kinds and ApprovalKind(approval.kind) not in kinds:
            continue
        payload = {"action": scene_action} if approval.kind == ApprovalKind.SCENE_REVIEW.value else {}
        if approval.kind == ApprovalKind.INTERVENTION.value:
            payload = {}
        decided.append(await c.control.decide(approval.id, approve=True, actor="reviewer",
                                              reason=reason, payload=payload))
    return decided


async def run_to_rest(c: Container, max_rounds: int = 50) -> None:
    for _ in range(max_rounds):
        if await c.engine.run_until_idle() == 0:
            return
    raise AssertionError("the engine did not come to rest")


async def job_row(c: Container, job_id: uuid.UUID) -> ProductionJob:
    async with c.db.session() as session:
        return await session.get(ProductionJob, job_id)




async def dump(c: Container, job_id: uuid.UUID) -> str:
    """Human-readable state of a job, for assertion messages."""
    from vidsprod.db.models import ValidationResult, WorkflowStage

    lines = []
    async with c.db.session() as session:
        stages = (await session.execute(select(WorkflowStage).where(
            WorkflowStage.job_id == job_id).order_by(WorkflowStage.created_at))).scalars().all()
        for s in stages:
            lines.append(f"{s.stage}#{s.attempt} {s.status} {s.next_permitted_action or ''} "
                         f"{(s.error or {}).get('message', '')}")
            fails = (await session.execute(select(ValidationResult).where(
                ValidationResult.stage_attempt_id == s.id,
                ValidationResult.outcome != "PASS"))).scalars().all()
            lines += [f"    {v.severity} {v.rule_id}: {v.message[:160]}" for v in fails]
            if s.error and s.error.get("traceback"):
                lines.append(s.error["traceback"][-1500:])
    return "\n".join(lines)
