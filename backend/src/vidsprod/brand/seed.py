"""The Prudential brand profile, seeded from DESIGN.md and the approved fact catalogue.

Approved facts are the two catalogue facts marked `verified: true` and
`customer_facing_allowed: true` in phi-service-layer/media-generation/prompts/products/
health-cover/family-floater.yaml (prompt status ACTIVE, approval_status APPROVED). Forbidden
language follows that project's shared compliance guardrails. Both lists are editable in
the dashboard; changes are audited.
"""

from __future__ import annotations

from sqlalchemy import select

from vidsprod.brand.guidelines import BrandGuidelines
from vidsprod.db.models import BrandProfile
from vidsprod.db.session import Database
from vidsprod.domain.enums import ArtifactKind
from vidsprod.storage.artifacts import ArtifactStore
from vidsprod.workflow import audit

BRAND_NAME = "Prudential"

APPROVED_FACTS = [
    {"fact_id": "PRU-HEALTH-FF-001",
     "text": "One policy covers every member of the family named on it.",
     "source": "PRU-HEALTH-FF product catalogue, family floater section"},
    {"fact_id": "PRU-HEALTH-FF-002",
     "text": "Cashless treatment is available at hospitals in the network.",
     "source": "PRU-HEALTH-FF product catalogue, network hospitals section"},
]

FORBIDDEN_TERMS = [
    "guarantee", "guaranteed", "guarantees", "best", "number one", "no. 1", "cheapest",
    "lowest price", "risk-free", "risk free", "100%", "instant approval", "no questions asked",
    "ruin", "bankrupt", "never worry",
]

STANDARD_DISCLOSURES = [
    "Insurance is the subject matter of solicitation. For more details on benefits, "
    "exclusions, limitations, terms and conditions, please read the sales brochure and "
    "policy wording carefully before concluding a sale.",
]


async def ensure_brand_profile(db: Database, store: ArtifactStore, guidelines: BrandGuidelines,
                               logo_path) -> BrandProfile:
    async with db.transaction() as session:
        profile = await session.scalar(select(BrandProfile).where(BrandProfile.name == BRAND_NAME))
        if profile is not None:
            if guidelines.source_digest and profile.design_digest != guidelines.source_digest:
                profile.design_digest = guidelines.source_digest
                profile.palette = [t.describe() for t in guidelines.palette]
                profile.forbidden_colours = list(guidelines.forbidden_colours)
                await audit.record(session, type="brand.design_updated",
                                   message="DESIGN.md changed; brand profile refreshed",
                                   data={"digest": guidelines.source_digest})
            return profile
        logo_id = None
        if logo_path and logo_path.exists():
            logo = await store.put_file(session, logo_path, job_id=None,
                                        kind=ArtifactKind.BRAND_ASSET, producer="brand_seed",
                                        mime="image/png", meta={"name": "logo"})
            logo_id = logo.id
        profile = BrandProfile(
            name=BRAND_NAME, tone="Warm, reassuring, credible; restraint over hype",
            regulated=True, design_digest=guidelines.source_digest,
            palette=[t.describe() for t in guidelines.palette],
            forbidden_colours=list(guidelines.forbidden_colours),
            forbidden_terms=FORBIDDEN_TERMS, approved_facts=APPROVED_FACTS,
            standard_disclosures=STANDARD_DISCLOSURES, logo_artifact_id=logo_id,
            logo_position="bottom-right",
        )
        session.add(profile)
        await session.flush()
        await audit.record(session, type="brand.seeded", message="Prudential brand profile created",
                           data={"facts": len(APPROVED_FACTS), "digest": guidelines.source_digest})
        return profile
