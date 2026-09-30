"""Campaigns: create, list, view, edit, brief versions, archive, reference images, start."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile, status
from sqlalchemy import func, select

from vidsprod.api.deps import ActorDep, ContainerDep, require_token
from vidsprod.api.schemas import (
    BrandProfileOut,
    BrandProfileUpdate,
    BriefOut,
    BriefUpdate,
    CampaignCreate,
    CampaignOut,
    CampaignUpdate,
    JobSummary,
    job_summary,
)
from vidsprod.db.models import (
    Artifact,
    BrandProfile,
    Campaign,
    CampaignBrief,
    ProductionJob,
    WorkflowStage,
)
from vidsprod.domain.documents import BriefInput
from vidsprod.domain.enums import ArtifactKind, CampaignStatus, JobStatus
from vidsprod.errors import ConflictError, InvalidInputError, NotFoundError
from vidsprod.workflow import audit
from vidsprod.workflow.context import content_hash

router = APIRouter(prefix="/api", tags=["campaigns"], dependencies=[Depends(require_token)])

_ACTIVE = [s.value for s in JobStatus if s.is_active]
_IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


async def _campaign_out(session, campaign: Campaign) -> CampaignOut:
    # Server-set columns (updated_at) are expired after a flush; load them now rather than
    # lazily, which an async session cannot do.
    await session.refresh(campaign)
    brief = await session.scalar(select(CampaignBrief).where(
        CampaignBrief.campaign_id == campaign.id,
        CampaignBrief.version == campaign.current_brief_version))
    job = await session.scalar(select(ProductionJob).where(
        ProductionJob.campaign_id == campaign.id).order_by(ProductionJob.created_at.desc()).limit(1))
    latest = None
    if job is not None:
        stages = (await session.execute(select(WorkflowStage).where(
            WorkflowStage.job_id == job.id))).scalars().all()
        latest = job_summary(job, list(stages), campaign.name)
    return CampaignOut(
        id=campaign.id, name=campaign.name, status=campaign.status, owner=campaign.owner,
        brand_profile_id=campaign.brand_profile_id,
        current_brief_version=campaign.current_brief_version, row_version=campaign.row_version,
        archived_at=campaign.archived_at, created_at=campaign.created_at,
        updated_at=campaign.updated_at, brief=brief.content if brief else None, latest_job=latest,
    )


async def _check_references(session, brief: BriefInput) -> None:
    for asset_id in brief.reference_asset_ids:
        try:
            artifact = await session.get(Artifact, uuid.UUID(asset_id))
        except ValueError:
            artifact = None
        if artifact is None or artifact.kind != ArtifactKind.BRAND_ASSET.value:
            raise InvalidInputError(f"reference image {asset_id} does not exist")


# ------------------------------------------------------------------ brand profiles
@router.get("/brand-profiles")
async def list_brand_profiles(c: ContainerDep) -> list[BrandProfileOut]:
    async with c.db.session() as session:
        rows = (await session.execute(select(BrandProfile).order_by(BrandProfile.name))).scalars().all()
        return [BrandProfileOut.model_validate(r) for r in rows]


@router.patch("/brand-profiles/{profile_id}")
async def update_brand_profile(profile_id: uuid.UUID, body: BrandProfileUpdate, c: ContainerDep,
                               actor: ActorDep) -> BrandProfileOut:
    async with c.db.transaction() as session:
        profile = await session.get(BrandProfile, profile_id, with_for_update=True)
        if profile is None:
            raise NotFoundError("brand profile not found")
        changes = body.model_dump(exclude_none=True)
        for fact in changes.get("approved_facts", []):
            if not fact.get("fact_id") or not fact.get("text") or not fact.get("source"):
                raise InvalidInputError("every approved fact needs fact_id, text and source")
        for key, value in changes.items():
            setattr(profile, key, value)
        await audit.record(session, type="brand.updated", actor=actor,
                           message=f"brand profile updated: {', '.join(changes)}",
                           data={"fields": list(changes)})
        await session.flush()
        await session.refresh(profile)
        return BrandProfileOut.model_validate(profile)


# ------------------------------------------------------------------ campaigns
@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def create_campaign(body: CampaignCreate, c: ContainerDep, actor: ActorDep) -> CampaignOut:
    async with c.db.transaction() as session:
        profile = await session.scalar(select(BrandProfile).order_by(BrandProfile.created_at).limit(1))
        if profile is None:
            raise ConflictError("no brand profile exists")
        await _check_references(session, body.brief)
        campaign = Campaign(name=body.name, brand_profile_id=profile.id, owner=actor,
                            status=CampaignStatus.READY.value, current_brief_version=1)
        session.add(campaign)
        await session.flush()
        content = body.brief.model_dump(mode="json")
        session.add(CampaignBrief(campaign_id=campaign.id, version=1, content=content,
                                  content_hash=content_hash(content), created_by=actor))
        await audit.record(session, type="campaign.created", actor=actor, campaign_id=campaign.id,
                           message=f"campaign '{body.name}' created")
        await session.flush()
        return await _campaign_out(session, campaign)


@router.get("/campaigns")
async def list_campaigns(
    c: ContainerDep, include_archived: bool = False,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[CampaignOut]:
    async with c.db.session() as session:
        stmt = select(Campaign).order_by(Campaign.updated_at.desc())
        if not include_archived:
            stmt = stmt.where(Campaign.archived_at.is_(None))
        if status_filter:
            stmt = stmt.where(Campaign.status == status_filter)
        rows = (await session.execute(stmt.limit(200))).scalars().all()
        return [await _campaign_out(session, r) for r in rows]


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: uuid.UUID, c: ContainerDep) -> CampaignOut:
    async with c.db.session() as session:
        campaign = await session.get(Campaign, campaign_id)
        if campaign is None:
            raise NotFoundError("campaign not found")
        return await _campaign_out(session, campaign)


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: uuid.UUID, body: CampaignUpdate, c: ContainerDep,
                          actor: ActorDep) -> CampaignOut:
    async with c.db.transaction() as session:
        campaign = await session.get(Campaign, campaign_id, with_for_update=True)
        if campaign is None:
            raise NotFoundError("campaign not found")
        if campaign.row_version != body.expected_row_version:
            raise ConflictError("the campaign was changed by someone else; reload and retry")
        campaign.name = body.name
        campaign.row_version += 1
        await audit.record(session, type="campaign.updated", actor=actor, campaign_id=campaign.id,
                           message=f"renamed to '{body.name}'")
        await session.flush()
        return await _campaign_out(session, campaign)


@router.put("/campaigns/{campaign_id}/brief")
async def save_brief(campaign_id: uuid.UUID, body: BriefUpdate, c: ContainerDep,
                     actor: ActorDep) -> CampaignOut:
    """Saves a new brief version. A running production keeps the version it started with."""
    async with c.db.transaction() as session:
        campaign = await session.get(Campaign, campaign_id, with_for_update=True)
        if campaign is None:
            raise NotFoundError("campaign not found")
        if campaign.archived_at is not None:
            raise ConflictError("the campaign is archived")
        await _check_references(session, body.brief)
        content = body.brief.model_dump(mode="json")
        version = campaign.current_brief_version + 1
        session.add(CampaignBrief(campaign_id=campaign.id, version=version, content=content,
                                  content_hash=content_hash(content), created_by=actor))
        campaign.current_brief_version = version
        campaign.row_version += 1
        await audit.record(session, type="campaign.brief_saved", actor=actor,
                           campaign_id=campaign.id, message=f"brief v{version} saved")
        await session.flush()
        return await _campaign_out(session, campaign)


@router.get("/campaigns/{campaign_id}/briefs")
async def list_briefs(campaign_id: uuid.UUID, c: ContainerDep) -> list[BriefOut]:
    async with c.db.session() as session:
        rows = (await session.execute(select(CampaignBrief).where(
            CampaignBrief.campaign_id == campaign_id).order_by(CampaignBrief.version.desc()))).scalars().all()
        return [BriefOut.model_validate(r) for r in rows]


@router.post("/campaigns/{campaign_id}/archive")
async def archive_campaign(campaign_id: uuid.UUID, c: ContainerDep, actor: ActorDep) -> CampaignOut:
    async with c.db.transaction() as session:
        campaign = await session.get(Campaign, campaign_id, with_for_update=True)
        if campaign is None:
            raise NotFoundError("campaign not found")
        active = await session.scalar(select(func.count()).select_from(ProductionJob).where(
            ProductionJob.campaign_id == campaign_id, ProductionJob.status.in_(_ACTIVE)))
        if active:
            raise ConflictError("cancel the active production before archiving")
        campaign.archived_at = datetime.now(UTC)
        campaign.status = CampaignStatus.ARCHIVED.value
        campaign.row_version += 1
        await audit.record(session, type="campaign.archived", actor=actor,
                           campaign_id=campaign.id, message="archived")
        await session.flush()
        return await _campaign_out(session, campaign)


@router.post("/campaigns/{campaign_id}/unarchive")
async def unarchive_campaign(campaign_id: uuid.UUID, c: ContainerDep, actor: ActorDep) -> CampaignOut:
    async with c.db.transaction() as session:
        campaign = await session.get(Campaign, campaign_id, with_for_update=True)
        if campaign is None:
            raise NotFoundError("campaign not found")
        campaign.archived_at = None
        campaign.status = CampaignStatus.READY.value
        campaign.row_version += 1
        await audit.record(session, type="campaign.unarchived", actor=actor,
                           campaign_id=campaign.id, message="restored")
        await session.flush()
        return await _campaign_out(session, campaign)


@router.post("/references", status_code=status.HTTP_201_CREATED)
async def upload_reference(c: ContainerDep, actor: ActorDep,
                           file: Annotated[UploadFile, File()]) -> dict:
    """A reference image (PNG/JPEG, ≤10 MB) for briefs to cite in reference_asset_ids."""
    suffix = _IMAGE_TYPES.get(file.content_type or "")
    if suffix is None:
        raise InvalidInputError("reference images must be PNG or JPEG")
    data = await file.read(_MAX_IMAGE_BYTES + 1)
    if len(data) > _MAX_IMAGE_BYTES:
        raise InvalidInputError("reference images are limited to 10 MB")
    signatures = {".png": b"\x89PNG", ".jpg": b"\xff\xd8\xff"}
    if not data.startswith(signatures[suffix]):
        raise InvalidInputError("the file content is not the image type it claims to be")
    async with c.db.transaction() as session:
        artifact = await c.store.put_bytes(
            session, data, suffix, job_id=None, kind=ArtifactKind.BRAND_ASSET, producer="upload",
            mime=file.content_type, meta={"name": (file.filename or "reference")[:120],
                                          "uploaded_by": actor})
        await audit.record(session, type="reference.uploaded", actor=actor,
                           message=f"reference image {artifact.meta['name']}")
        return {"id": str(artifact.id), "name": artifact.meta["name"], "size_bytes": artifact.size_bytes}


@router.post("/campaigns/{campaign_id}/jobs", status_code=status.HTTP_201_CREATED)
async def start_production(campaign_id: uuid.UUID, c: ContainerDep, actor: ActorDep,
                           idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
                           ) -> JobSummary:
    job = await c.control.start(campaign_id, actor, idempotency_key)
    return job_summary(job)


@router.get("/campaigns/{campaign_id}/jobs")
async def list_campaign_jobs(campaign_id: uuid.UUID, c: ContainerDep) -> list[JobSummary]:
    async with c.db.session() as session:
        jobs = (await session.execute(select(ProductionJob).where(
            ProductionJob.campaign_id == campaign_id).order_by(ProductionJob.created_at.desc()))).scalars().all()
        out = []
        for job in jobs:
            stages = (await session.execute(select(WorkflowStage).where(
                WorkflowStage.job_id == job.id))).scalars().all()
            out.append(job_summary(job, list(stages)))
        return out
