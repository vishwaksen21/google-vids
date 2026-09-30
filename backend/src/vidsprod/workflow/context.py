"""What a stage handler receives, and what it returns."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func, select

from vidsprod.brand.guidelines import BrandGuidelines
from vidsprod.config import Settings
from vidsprod.db.models import (
    ApprovalRequest,
    Artifact,
    BrandProfile,
    Campaign,
    CampaignBrief,
    JobDocument,
    ProductionJob,
    ValidationResult,
    WorkflowStage,
)
from vidsprod.db.session import Database
from vidsprod.domain.enums import ApprovalKind, ArtifactKind, StageId
from vidsprod.domain.validation import RuleResult
from vidsprod.llm.service import AIService
from vidsprod.storage.artifacts import ArtifactStore
from vidsprod.vids.port import EvidenceFile, GoogleVidsAdapter
from vidsprod.workflow import audit
from vidsprod.workflow.intents import IntentRunner


@dataclass
class Services:
    settings: Settings
    db: Database
    ai: AIService
    adapter: GoogleVidsAdapter
    store: ArtifactStore
    brand: BrandGuidelines
    intents: IntentRunner


class OutcomeKind(StrEnum):
    DONE = "DONE"  # finished; the gate decides from `results`
    CONTINUE = "CONTINUE"  # made progress; call again (pause/cancel are checked in between)
    WAIT_HUMAN = "WAIT_HUMAN"  # approvals were requested; resume when they are decided


@dataclass
class ApprovalSpec:
    kind: ApprovalKind
    subject: str
    title: str
    summary: str
    payload: dict[str, Any]
    options: list[str] = field(default_factory=lambda: ["approve", "reject"])


@dataclass
class StageOutcome:
    kind: OutcomeKind
    results: list[RuleResult] = field(default_factory=list)
    output: dict[str, Any] | None = None
    approvals: list[ApprovalSpec] = field(default_factory=list)
    message: str = ""

    @classmethod
    def done(cls, results: list[RuleResult], output: dict[str, Any] | None = None,
             message: str = "") -> StageOutcome:
        return cls(OutcomeKind.DONE, results=results, output=output, message=message)

    @classmethod
    def proceed(cls, message: str = "", results: list[RuleResult] | None = None) -> StageOutcome:
        return cls(OutcomeKind.CONTINUE, results=results or [], message=message)

    @classmethod
    def wait(cls, approvals: list[ApprovalSpec], message: str = "",
             results: list[RuleResult] | None = None) -> StageOutcome:
        return cls(OutcomeKind.WAIT_HUMAN, approvals=approvals, message=message,
                   results=results or [])


def content_hash(content: Any) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


class StageContext:
    def __init__(self, services: Services, job: ProductionJob, attempt: WorkflowStage) -> None:
        self.s = services
        self.settings = services.settings
        self.job = job
        self.attempt = attempt
        self.stage = StageId(attempt.stage)
        self.stage_results: list[RuleResult] = []  # accumulated across CONTINUE steps

    @property
    def job_id(self) -> uuid.UUID:
        return self.job.id

    # ------------------------------------------------------------------ loaders
    async def campaign(self) -> Campaign:
        async with self.s.db.session() as session:
            return await session.get(Campaign, self.job.campaign_id)

    async def brand_profile(self) -> BrandProfile:
        campaign = await self.campaign()
        async with self.s.db.session() as session:
            return await session.get(BrandProfile, campaign.brand_profile_id)

    async def brief_input(self) -> dict[str, Any]:
        async with self.s.db.session() as session:
            return (await session.get(CampaignBrief, self.job.brief_id)).content

    async def doc(self, kind: str) -> dict[str, Any] | None:
        async with self.s.db.session() as session:
            row = (await session.execute(
                select(JobDocument).where(JobDocument.job_id == self.job.id,
                                          JobDocument.kind == kind)
                .order_by(JobDocument.version.desc()).limit(1)
            )).scalar_one_or_none()
            return row.content if row else None

    async def doc_row(self, kind: str) -> JobDocument | None:
        async with self.s.db.session() as session:
            return (await session.execute(
                select(JobDocument).where(JobDocument.job_id == self.job.id,
                                          JobDocument.kind == kind)
                .order_by(JobDocument.version.desc()).limit(1)
            )).scalar_one_or_none()

    async def save_doc(self, kind: str, content: dict[str, Any]) -> JobDocument:
        async with self.s.db.transaction() as session:
            version = (await session.scalar(
                select(func.max(JobDocument.version)).where(JobDocument.job_id == self.job.id,
                                                            JobDocument.kind == kind)) or 0) + 1
            row = JobDocument(job_id=self.job.id, kind=kind, version=version, content=content,
                              content_hash=content_hash(content), stage_attempt_id=self.attempt.id)
            session.add(row)
            await audit.record(session, type="document.saved", job_id=self.job.id,
                               message=f"{kind} v{version}", data={"kind": kind, "version": version})
        return row

    async def previous_failures(self) -> list[dict[str, Any]]:
        """Failed rule results of earlier attempts of this stage: retry feedback."""
        async with self.s.db.session() as session:
            earlier = (await session.execute(
                select(WorkflowStage.id).where(WorkflowStage.job_id == self.job.id,
                                               WorkflowStage.stage == self.attempt.stage,
                                               WorkflowStage.attempt < self.attempt.attempt)
            )).scalars().all()
            if not earlier:
                return []
            rows = (await session.execute(
                select(ValidationResult).where(ValidationResult.stage_attempt_id.in_(earlier),
                                               ValidationResult.outcome != "PASS")
            )).scalars().all()
            return [{"rule_id": r.rule_id, "severity": r.severity, "message": r.message,
                     "fix_hint": r.fix_hint} for r in rows]

    async def approvals(self) -> list[ApprovalRequest]:
        async with self.s.db.session() as session:
            return list((await session.execute(
                select(ApprovalRequest).where(ApprovalRequest.job_id == self.job.id,
                                              ApprovalRequest.stage_attempt_id == self.attempt.id)
                .order_by(ApprovalRequest.created_at)
            )).scalars().all())

    # ------------------------------------------------------------------ side effects
    async def progress(self, **fields: Any) -> None:
        async with self.s.db.transaction() as session:
            attempt = await session.get(WorkflowStage, self.attempt.id)
            attempt.progress = {**(attempt.progress or {}), **fields}
            self.attempt.progress = attempt.progress

    async def event(self, type: str, message: str, **data: Any) -> None:
        async with self.s.db.transaction() as session:
            await audit.record(session, type=type, job_id=self.job.id,
                               campaign_id=self.job.campaign_id, message=message, data=data)

    def work_dir(self, *parts: str) -> Path:
        return self.s.store.work_dir(self.job.id, self.stage.value.lower(), *parts)

    async def store_evidence(self, files: list[EvidenceFile], producer: str,
                             simulated: bool) -> list[Artifact]:
        stored = []
        async with self.s.db.transaction() as session:
            for file in files:
                if file.path.exists():
                    stored.append(await self.s.store.put_file(
                        session, file.path, job_id=self.job.id, kind=file.kind, producer=producer,
                        simulated=simulated, mime=file.mime,
                        meta={"label": file.label, **file.meta},
                    ))
        return stored

    async def store_file(self, path: Path, kind: ArtifactKind, producer: str, simulated: bool,
                         meta: dict[str, Any] | None = None) -> Artifact:
        async with self.s.db.transaction() as session:
            return await self.s.store.put_file(session, path, job_id=self.job.id, kind=kind,
                                               producer=producer, simulated=simulated, meta=meta)


class StageHandler(Protocol):
    stage: StageId
    auto_retry: bool

    async def run(self, ctx: StageContext) -> StageOutcome: ...
