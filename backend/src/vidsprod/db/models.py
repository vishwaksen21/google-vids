"""ORM models.

History is append-only: a brief edit is a new `campaign_brief` version, a scene revision is
a new `scene_version`, a regeneration or edit is a new `scene_take`, a retried stage is a
new `workflow_stage` attempt. "Current" is always a pointer or "latest by number", never an
overwrite. Enumerations are `text` with CHECK constraints so adding a value is an ordinary
migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from vidsprod.db.ids import uuid7
from vidsprod.domain import enums as E


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB, uuid.UUID: UUID(as_uuid=True)}


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def _check(column: str, enum: type[StrEnum], name: str) -> CheckConstraint:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name)


def _fk(target: str, *, nullable: bool = False, ondelete: str = "RESTRICT") -> Mapped[Any]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True
    )


# ======================================================================= brand
class BrandProfile(Base):
    __tablename__ = "brand_profile"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text, unique=True)
    tone: Mapped[str] = mapped_column(Text, default="")
    regulated: Mapped[bool] = mapped_column(Boolean, default=True)
    design_digest: Mapped[str | None] = mapped_column(Text)
    palette: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    forbidden_colours: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    forbidden_terms: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    approved_facts: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    standard_disclosures: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    logo_artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    logo_position: Mapped[str] = mapped_column(Text, default="bottom-right")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# ======================================================================= campaigns
class Campaign(Base):
    __tablename__ = "campaign"
    __table_args__ = (_check("status", E.CampaignStatus, "campaign_status_ck"),)

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default=E.CampaignStatus.DRAFT.value)
    brand_profile_id: Mapped[uuid.UUID] = _fk("brand_profile.id")
    owner: Mapped[str] = mapped_column(Text)
    current_brief_version: Mapped[int] = mapped_column(Integer, default=0)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    briefs: Mapped[list[CampaignBrief]] = relationship(
        back_populates="campaign", order_by="CampaignBrief.version"
    )


class CampaignBrief(Base):
    __tablename__ = "campaign_brief"
    __table_args__ = (UniqueConstraint("campaign_id", "version"),)

    id: Mapped[uuid.UUID] = _pk()
    campaign_id: Mapped[uuid.UUID] = _fk("campaign.id", ondelete="CASCADE")
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()

    campaign: Mapped[Campaign] = relationship(back_populates="briefs")


# ======================================================================= jobs and stages
_ACTIVE_JOB = "status IN ('QUEUED','RUNNING','PAUSED','WAITING_FOR_HUMAN')"


class ProductionJob(Base):
    __tablename__ = "production_job"
    __table_args__ = (
        _check("status", E.JobStatus, "job_status_ck"),
        CheckConstraint("control IS NULL OR control IN ('PAUSE','CANCEL')", name="job_control_ck"),
        # One active production per campaign.
        Index("one_active_job_per_campaign", "campaign_id", unique=True,
              postgresql_where=text(_ACTIVE_JOB)),
        # One running job per Google account in the real browser: one editor, one hand.
        Index("one_running_browser_job_per_account", "vids_account", unique=True,
              postgresql_where=text("status = 'RUNNING' AND adapter = 'browser'")),
        Index("job_claimable", "status", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    campaign_id: Mapped[uuid.UUID] = _fk("campaign.id")
    brief_id: Mapped[uuid.UUID] = _fk("campaign_brief.id")
    status: Mapped[str] = mapped_column(Text, default=E.JobStatus.QUEUED.value)
    control: Mapped[str | None] = mapped_column(Text)
    current_stage: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    adapter: Mapped[str] = mapped_column(Text)
    vids_account: Mapped[str] = mapped_column(Text)
    ai_provider: Mapped[str] = mapped_column(Text)
    simulation: Mapped[bool] = mapped_column(Boolean)
    brand_digest: Mapped[str | None] = mapped_column(Text)
    vids_project_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowStage(Base):
    """One attempt at one stage. A retry is a new row; history is never rewritten."""

    __tablename__ = "workflow_stage"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", "attempt"),
        _check("status", E.StageStatus, "stage_status_ck"),
        _check("stage", E.StageId, "stage_id_ck"),
    )

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = _fk("production_job.id", ondelete="CASCADE")
    stage: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default=E.StageStatus.PENDING.value)
    input_hash: Mapped[str | None] = mapped_column(Text)
    input_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    next_permitted_action: Mapped[str | None] = mapped_column(Text)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class ValidationResult(Base):
    __tablename__ = "validation_result"
    __table_args__ = (
        _check("severity", E.Severity, "vr_severity_ck"),
        _check("outcome", E.RuleOutcome, "vr_outcome_ck"),
        _check("category", E.RuleCategory, "vr_category_ck"),
    )

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = _fk("production_job.id", ondelete="CASCADE")
    stage_attempt_id: Mapped[uuid.UUID | None] = _fk(
        "workflow_stage.id", nullable=True, ondelete="CASCADE"
    )
    take_id: Mapped[uuid.UUID | None] = _fk("scene_take.id", nullable=True, ondelete="CASCADE")
    subject: Mapped[str | None] = mapped_column(Text)
    rule_id: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    judge: Mapped[str] = mapped_column(Text, default="deterministic")
    evidence: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    fix_hint: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


class JobDocument(Base):
    """Versioned planning documents and reports for a job.

    kinds: CAMPAIGN_BRIEF (validated), CREATIVE_PLAN, SCRIPT, SCENE_PLAN, PROMPTS,
    TIMELINE_PLAN, QA_REPORT.
    """

    __tablename__ = "job_document"
    __table_args__ = (UniqueConstraint("job_id", "kind", "version"),)

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = _fk("production_job.id", ondelete="CASCADE")
    kind: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    stage_attempt_id: Mapped[uuid.UUID | None] = _fk("workflow_stage.id", nullable=True)
    created_at: Mapped[datetime] = _created()


# ======================================================================= scenes
class Scene(Base):
    __tablename__ = "scene"
    __table_args__ = (
        UniqueConstraint("job_id", "scene_key"),
        _check("status", E.SceneStatus, "scene_status_ck"),
    )

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = _fk("production_job.id", ondelete="CASCADE")
    scene_key: Mapped[str] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default=E.SceneStatus.PLANNED.value)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    locked_take_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    takes_used: Mapped[int] = mapped_column(Integer, default=0)
    edits_used: Mapped[int] = mapped_column(Integer, default=0)
    extra_takes_granted: Mapped[int] = mapped_column(Integer, default=0)
    pending_instruction: Mapped[str | None] = mapped_column(Text)
    resume_status: Mapped[str | None] = mapped_column(Text)  # status to return to when unblocked
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class SceneVersion(Base):
    """A revision of the written scene (spec and prompt). Not a generation."""

    __tablename__ = "scene_version"
    __table_args__ = (UniqueConstraint("scene_id", "version"),)

    id: Mapped[uuid.UUID] = _pk()
    scene_id: Mapped[uuid.UUID] = _fk("scene.id", ondelete="CASCADE")
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prompt: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prompt_hash: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class SceneTake(Base):
    """One clip produced in Vids for a scene version, by generation or by edit."""

    __tablename__ = "scene_take"
    __table_args__ = (
        UniqueConstraint("scene_id", "take_no"),
        _check("status", E.TakeStatus, "take_status_ck"),
        _check("origin", E.TakeOrigin, "take_origin_ck"),
        # At most one locked take per scene.
        Index("one_locked_take_per_scene", "scene_id", unique=True,
              postgresql_where=text("locked_at IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = _pk()
    scene_id: Mapped[uuid.UUID] = _fk("scene.id", ondelete="CASCADE")
    scene_version_id: Mapped[uuid.UUID] = _fk("scene_version.id")
    parent_take_id: Mapped[uuid.UUID | None] = _fk("scene_take.id", nullable=True)
    take_no: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default=E.TakeStatus.GENERATED.value)
    prompt_text: Mapped[str] = mapped_column(Text)
    edit_instruction: Mapped[str | None] = mapped_column(Text)
    clip_ref: Mapped[dict[str, Any]] = mapped_column(JSONB)
    verdict: Mapped[str | None] = mapped_column(Text)
    verdict_reasons: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    correction: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    fingerprint: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    budget_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    simulated: Mapped[bool] = mapped_column(Boolean)
    timeline_item_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


# ======================================================================= timeline
class TimelineOperation(Base):
    __tablename__ = "timeline_op"
    __table_args__ = (
        UniqueConstraint("job_id", "seq"),
        _check("kind", E.TimelineOpKind, "timeline_op_kind_ck"),
        CheckConstraint(
            "status IN ('PENDING','APPLIED','VERIFIED','FAILED','SKIPPED')", name="timeline_op_ck"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = _fk("production_job.id", ondelete="CASCADE")
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    affects_scenes: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(Text, default="PENDING")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# ======================================================================= intents and budget
class ActionIntent(Base):
    """A non-idempotent Vids action, recorded before it is attempted.

    After a crash, a PENDING intent is reconciled against what Vids shows before anything
    of the same kind is attempted again for the same target.
    """

    __tablename__ = "action_intent"
    __table_args__ = (
        _check("status", E.IntentStatus, "intent_status_ck"),
        _check("kind", E.IntentKind, "intent_kind_ck"),
        Index("one_pending_intent_per_target", "job_id", "kind", "target", unique=True,
              postgresql_where=text("status = 'PENDING'")),
    )

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = _fk("production_job.id", ondelete="CASCADE")
    kind: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, default=E.IntentStatus.PENDING.value)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class BudgetAccount(Base):
    __tablename__ = "budget_account"
    __table_args__ = (UniqueConstraint("account_key", "period"),)

    id: Mapped[uuid.UUID] = _pk()
    account_key: Mapped[str] = mapped_column(Text)
    period: Mapped[str] = mapped_column(Text)  # YYYY-MM, Pacific time (Vids resets at 00:00 PT)
    monthly_limit: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class BudgetEntry(Base):
    __tablename__ = "budget_entry"
    __table_args__ = (_check("status", E.BudgetEntryStatus, "budget_status_ck"),)

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = _fk("budget_account.id")
    job_id: Mapped[uuid.UUID] = _fk("production_job.id", ondelete="CASCADE")
    campaign_id: Mapped[uuid.UUID] = _fk("campaign.id")
    scene_id: Mapped[uuid.UUID | None] = _fk("scene.id", nullable=True)
    purpose: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default=E.BudgetEntryStatus.RESERVED.value)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ======================================================================= browser, artifacts
class BrowserSession(Base):
    __tablename__ = "browser_session"

    id: Mapped[uuid.UUID] = _pk()
    adapter: Mapped[str] = mapped_column(Text)
    account_key: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)  # READY / DISABLED / SIGNED_OUT / MISMATCH / ERROR
    authenticated: Mapped[bool] = mapped_column(Boolean, default=False)
    account_label: Mapped[str | None] = mapped_column(Text)  # masked
    ui_fingerprint: Mapped[str | None] = mapped_column(Text)
    fingerprint_matches: Mapped[bool | None] = mapped_column(Boolean)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    evidence_artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    checked_at: Mapped[datetime] = _created()


class Artifact(Base):
    __tablename__ = "artifact"
    __table_args__ = (_check("kind", E.ArtifactKind, "artifact_kind_ck"),)

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID | None] = _fk("production_job.id", nullable=True, ondelete="CASCADE")
    kind: Mapped[str] = mapped_column(Text)
    uri: Mapped[str] = mapped_column(Text)  # path relative to the artifact root
    sha256: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str] = mapped_column(Text)
    producer: Mapped[str] = mapped_column(Text)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


class Export(Base):
    __tablename__ = "export"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt"),
        _check("status", E.ExportStatus, "export_status_ck"),
    )

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = _fk("production_job.id", ondelete="CASCADE")
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default=E.ExportStatus.REQUESTED.value)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    format: Mapped[str] = mapped_column(Text, default="mp4")
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(10, 3))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    probe: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verification: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    simulated: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Delivery(Base):
    __tablename__ = "delivery"
    __table_args__ = (_check("status", E.DeliveryStatus, "delivery_status_ck"),)

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = _fk("production_job.id", ondelete="CASCADE")
    export_id: Mapped[uuid.UUID] = _fk("export.id")
    status: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    simulated: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


# ======================================================================= approvals, audit
class ApprovalRequest(Base):
    __tablename__ = "approval_request"
    __table_args__ = (
        _check("kind", E.ApprovalKind, "approval_kind_ck"),
        _check("status", E.ApprovalStatus, "approval_status_ck"),
        Index("one_pending_approval_per_subject", "job_id", "kind", "subject", unique=True,
              postgresql_where=text("status = 'PENDING'")),
    )

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = _fk("production_job.id", ondelete="CASCADE")
    stage_attempt_id: Mapped[uuid.UUID | None] = _fk("workflow_stage.id", nullable=True)
    kind: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default=E.ApprovalStatus.PENDING.value)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    options: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class AuditEvent(Base):
    """Append-only. Its sequential id doubles as the live-updates cursor."""

    __tablename__ = "audit_event"
    __table_args__ = (Index("audit_event_job_cursor", "job_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


class AIRequest(Base):
    __tablename__ = "ai_request"

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)  # OK / ERROR / INVALID_OUTPUT
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    request_hash: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
