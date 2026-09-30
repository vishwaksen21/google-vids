"""The audit trail. Every state change and every human action is an `audit_event` row."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from vidsprod.db.models import AuditEvent

SYSTEM = "system"


async def record(
    session: AsyncSession, *, type: str, message: str, actor: str = SYSTEM,
    job_id: uuid.UUID | None = None, campaign_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(job_id=job_id, campaign_id=campaign_id, actor=actor, type=type,
                       message=message[:2000], data=data or {})
    session.add(event)
    await session.flush()
    return event
