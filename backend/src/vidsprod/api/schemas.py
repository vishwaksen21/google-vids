"""API request and response models, and the functions that build them from rows."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from vidsprod.db import models as M
from vidsprod.domain.documents import BriefInput
from vidsprod.domain.enums import STAGE_ORDER, StageStatus


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ requests
class CampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    brief: BriefInput


class CampaignUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    expected_row_version: int


class BriefUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief: BriefInput


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str | None = None


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    reason: str = Field(min_length=1, max_length=2000)
    action: Literal["accept", "regenerate", "edit"] | None = None
    instruction: str | None = Field(default=None, max_length=1000)
    intent_resolution: Literal["happened", "not_happened"] | None = None


class SceneReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "regenerate", "edit", "reject"]
    reason: str = Field(min_length=1, max_length=2000)
    instruction: str | None = Field(default=None, max_length=1000)


class BrandProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tone: str | None = Field(default=None, max_length=300)
    forbidden_terms: list[str] | None = Field(default=None, max_length=200)
    approved_facts: list[dict[str, str]] | None = Field(default=None, max_length=200)
    standard_disclosures: list[str] | None = Field(default=None, max_length=20)
    logo_position: Literal["bottom-right", "bottom-left", "top-right", "top-left"] | None = None


# ------------------------------------------------------------------ responses
class BrandProfileOut(Out):
    id: uuid.UUID
    name: str
    tone: str
    regulated: bool
    design_digest: str | None
    palette: list[Any]
    forbidden_colours: list[Any]
    forbidden_terms: list[Any]
    approved_facts: list[Any]
    standard_disclosures: list[Any]
    logo_artifact_id: uuid.UUID | None
    logo_position: str
    updated_at: datetime


class BriefOut(Out):
    id: uuid.UUID
    version: int
    content: dict[str, Any]
    created_by: str
    created_at: datetime


class JobSummary(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    campaign_name: str | None = None
    status: str
    control: str | None
    current_stage: str | None
    simulation: bool
    adapter: str
    ai_provider: str
    progress: float
    stages_passed: int
    stages_total: int
    error: dict[str, Any] | None
    created_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    owner: str
    brand_profile_id: uuid.UUID
    current_brief_version: int
    row_version: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    brief: dict[str, Any] | None = None
    latest_job: JobSummary | None = None


class StageOut(BaseModel):
    stage: str
    status: str
    attempts: int
    attempt_id: uuid.UUID | None
    next_permitted_action: str | None
    error: dict[str, Any] | None
    progress: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None
    failures: int = 0


class ApprovalOut(Out):
    id: uuid.UUID
    job_id: uuid.UUID
    kind: str
    subject: str
    status: str
    title: str
    summary: str
    payload: dict[str, Any]
    options: list[Any]
    decision: dict[str, Any] | None
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


class JobDetail(JobSummary):
    stages: list[StageOut]
    approvals: list[ApprovalOut]
    budget: dict[str, Any]
    active_scene: str | None
    current_action: str | None
    vids_project: dict[str, Any] | None


class ValidationOut(Out):
    id: uuid.UUID
    rule_id: str
    category: str
    severity: str
    outcome: str
    message: str
    judge: str
    subject: str | None
    evidence: list[Any]
    fix_hint: str | None
    data: dict[str, Any]
    created_at: datetime


class TakeOut(BaseModel):
    id: uuid.UUID
    take_no: int
    origin: str
    status: str
    verdict: str | None
    verdict_reasons: list[Any]
    correction: str | None
    edit_instruction: str | None
    evidence: list[Any]
    locked: bool
    simulated: bool
    prompt_text: str
    created_at: datetime
    validations: list[ValidationOut] = []


class SceneVersionOut(Out):
    id: uuid.UUID
    version: int
    reason: str
    spec: dict[str, Any]
    prompt: dict[str, Any]
    created_at: datetime


class SceneOut(BaseModel):
    id: uuid.UUID
    scene_key: str
    order_index: int
    status: str
    takes_used: int
    edits_used: int
    extra_takes_granted: int
    locked_take_id: uuid.UUID | None
    pending_instruction: str | None
    current_version: SceneVersionOut | None
    versions: list[SceneVersionOut]
    takes: list[TakeOut]


class ArtifactOut(Out):
    id: uuid.UUID
    job_id: uuid.UUID | None
    kind: str
    mime: str
    size_bytes: int
    sha256: str
    producer: str
    simulated: bool
    meta: dict[str, Any]
    created_at: datetime


class AuditOut(Out):
    id: int
    job_id: uuid.UUID | None
    campaign_id: uuid.UUID | None
    actor: str
    type: str
    message: str
    data: dict[str, Any]
    created_at: datetime


class ExportOut(Out):
    id: uuid.UUID
    attempt: int
    status: str
    artifact_id: uuid.UUID | None
    format: str
    duration_seconds: float | None
    size_bytes: int | None
    probe: dict[str, Any] | None
    verification: list[Any]
    simulated: bool
    created_at: datetime


class DeliveryOut(Out):
    id: uuid.UUID
    export_id: uuid.UUID
    status: str
    target: str
    location: str | None
    sha256: str
    simulated: bool
    note: str | None
    created_at: datetime


class AIRequestOut(Out):
    id: uuid.UUID
    purpose: str
    provider: str
    model: str
    status: str
    attempts: int
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost_estimate: float | None
    error: str | None
    created_at: datetime


# ------------------------------------------------------------------ builders
def job_summary(job: M.ProductionJob, stages: list[M.WorkflowStage] | None = None,
                campaign_name: str | None = None) -> JobSummary:
    latest = latest_attempts(stages or [])
    passed = sum(1 for s in latest.values() if StageStatus(s.status).is_complete)
    total = len(STAGE_ORDER)
    return JobSummary(
        id=job.id, campaign_id=job.campaign_id, campaign_name=campaign_name, status=job.status,
        control=job.control, current_stage=job.current_stage, simulation=job.simulation,
        adapter=job.adapter, ai_provider=job.ai_provider,
        progress=round(passed / total, 3), stages_passed=passed, stages_total=total,
        error=job.error, created_by=job.created_by, created_at=job.created_at,
        started_at=job.started_at, finished_at=job.finished_at,
    )


def latest_attempts(stages: list[M.WorkflowStage]) -> dict[str, M.WorkflowStage]:
    latest: dict[str, M.WorkflowStage] = {}
    for s in sorted(stages, key=lambda x: x.attempt):
        latest[s.stage] = s
    return latest


def stage_list(stages: list[M.WorkflowStage], failures: dict[uuid.UUID, int]) -> list[StageOut]:
    latest = latest_attempts(stages)
    counts: dict[str, int] = {}
    for s in stages:
        counts[s.stage] = counts.get(s.stage, 0) + 1
    out = []
    for stage in STAGE_ORDER:
        row = latest.get(stage.value)
        out.append(StageOut(
            stage=stage.value, status=row.status if row else StageStatus.PENDING.value,
            attempts=counts.get(stage.value, 0), attempt_id=row.id if row else None,
            next_permitted_action=row.next_permitted_action if row else None,
            error=_public_error(row.error) if row else None,
            progress=row.progress if row else None,
            started_at=row.started_at if row else None,
            finished_at=row.finished_at if row else None,
            failures=failures.get(row.id, 0) if row else 0,
        ))
    return out


def _public_error(error: dict[str, Any] | None) -> dict[str, Any] | None:
    if not error:
        return None
    return {k: v for k, v in error.items() if k != "traceback"}


def take_out(take: M.SceneTake, validations: list[M.ValidationResult]) -> TakeOut:
    return TakeOut(
        id=take.id, take_no=take.take_no, origin=take.origin, status=take.status,
        verdict=take.verdict, verdict_reasons=take.verdict_reasons, correction=take.correction,
        edit_instruction=take.edit_instruction, evidence=take.evidence,
        locked=take.locked_at is not None, simulated=take.simulated,
        prompt_text=take.prompt_text, created_at=take.created_at,
        validations=[ValidationOut.model_validate(v) for v in validations],
    )
