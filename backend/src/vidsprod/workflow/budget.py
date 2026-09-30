"""The generation budget.

Vids allows a limited number of AI generations per user per month (documented: about 50,
reset at 00:00 Pacific on the 1st). Every generation is reserved *before* it is attempted,
inside a transaction that locks the account's row, and settled after: SPENT when it
happened, RELEASED only when it verifiably did not. A per-campaign cap stops one campaign
from consuming the month.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vidsprod.config import Settings
from vidsprod.db.models import BudgetAccount, BudgetEntry
from vidsprod.domain.enums import BudgetEntryStatus
from vidsprod.errors import BudgetExhaustedError

_PACIFIC = ZoneInfo("America/Los_Angeles")
_COUNTED = (BudgetEntryStatus.RESERVED.value, BudgetEntryStatus.SPENT.value)


def current_period(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(_PACIFIC).strftime("%Y-%m")


async def _account(session: AsyncSession, settings: Settings, account_key: str) -> BudgetAccount:
    period = current_period()
    stmt = (select(BudgetAccount)
            .where(BudgetAccount.account_key == account_key, BudgetAccount.period == period)
            .with_for_update())
    account = (await session.execute(stmt)).scalar_one_or_none()
    if account is None:
        session.add(BudgetAccount(account_key=account_key, period=period,
                                  monthly_limit=settings.generation_monthly_limit))
        await session.flush()
        account = (await session.execute(stmt)).scalar_one()
    return account


async def usage(session: AsyncSession, settings: Settings, account_key: str,
                campaign_id: uuid.UUID | None = None) -> dict:
    account = await _account(session, settings, account_key)
    month = await session.scalar(select(func.count()).select_from(BudgetEntry).where(
        BudgetEntry.account_id == account.id, BudgetEntry.status.in_(_COUNTED)))
    result = {"period": account.period, "monthly_limit": account.monthly_limit,
              "monthly_used": month or 0}
    if campaign_id is not None:
        result["campaign_used"] = await session.scalar(
            select(func.count()).select_from(BudgetEntry).where(
                BudgetEntry.campaign_id == campaign_id, BudgetEntry.status.in_(_COUNTED)))
        result["campaign_limit"] = settings.generation_campaign_limit
    return result


async def reserve(
    session: AsyncSession, settings: Settings, *, account_key: str, job_id: uuid.UUID,
    campaign_id: uuid.UUID, scene_id: uuid.UUID | None, purpose: str,
) -> BudgetEntry:
    account = await _account(session, settings, account_key)  # row lock held to commit
    month = await session.scalar(select(func.count()).select_from(BudgetEntry).where(
        BudgetEntry.account_id == account.id, BudgetEntry.status.in_(_COUNTED)))
    if (month or 0) >= account.monthly_limit:
        raise BudgetExhaustedError(
            f"the monthly generation budget ({account.monthly_limit}) for this account is used "
            f"up for {account.period}",
            details={"period": account.period, "limit": account.monthly_limit},
        )
    used = await session.scalar(select(func.count()).select_from(BudgetEntry).where(
        BudgetEntry.campaign_id == campaign_id, BudgetEntry.status.in_(_COUNTED)))
    if (used or 0) >= settings.generation_campaign_limit:
        raise BudgetExhaustedError(
            f"this campaign has used its {settings.generation_campaign_limit} generations",
            details={"campaign_limit": settings.generation_campaign_limit},
        )
    entry = BudgetEntry(account_id=account.id, job_id=job_id, campaign_id=campaign_id,
                        scene_id=scene_id, purpose=purpose)
    session.add(entry)
    await session.flush()
    return entry


async def settle(session: AsyncSession, entry_id: uuid.UUID, status: BudgetEntryStatus,
                 note: str | None = None) -> None:
    entry = await session.get(BudgetEntry, entry_id, with_for_update=True)
    if entry is None or entry.status != BudgetEntryStatus.RESERVED.value:
        return
    entry.status = status.value
    entry.note = note
    entry.settled_at = datetime.now(UTC)


async def available(session: AsyncSession, settings: Settings, *, account_key: str,
                    campaign_id: uuid.UUID) -> bool:
    """Whether one more generation could be reserved (without reserving it)."""
    u = await usage(session, settings, account_key, campaign_id)
    return (u["monthly_used"] < u["monthly_limit"]
            and u["campaign_used"] < u["campaign_limit"])
