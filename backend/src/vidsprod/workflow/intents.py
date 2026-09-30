"""Crash-safe execution of non-idempotent Vids actions.

    reserve budget + write intent(PENDING)      ← one transaction, committed first
    perform the action in Vids                  ← no transaction open
    confirm intent + write domain rows + spend  ← one transaction

If the process dies between the second and third steps, the intent is still PENDING when
the job resumes. Before anything is attempted again for the same target, the adapter is
asked to reconcile: FOUND (it happened — use the result, write the domain rows now),
NOT_FOUND (it verifiably did not — release the budget, try again), or UNKNOWN (a person
must look). An action is therefore never repeated just because its outcome was lost.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vidsprod.config import Settings
from vidsprod.db.models import ActionIntent
from vidsprod.db.session import Database
from vidsprod.domain.enums import BudgetEntryStatus, IntentKind, IntentStatus
from vidsprod.errors import (
    AmbiguousIntentError,
    BlockedError,
    GenerationRefusedError,
    UIMismatchError,
)
from vidsprod.vids.port import GoogleVidsAdapter, ProjectHandle, ReconcileStatus
from vidsprod.workflow import audit, budget

# Errors raised before the adapter touched anything: the action verifiably did not happen.
_DID_NOT_HAPPEN = (BlockedError, GenerationRefusedError)


@dataclass
class BudgetClaim:
    account_key: str
    campaign_id: uuid.UUID
    scene_id: uuid.UUID | None
    purpose: str


OnConfirm = Callable[[AsyncSession, dict[str, Any]], Awaitable[None]]


class IntentRunner:
    def __init__(self, db: Database, settings: Settings, adapter: GoogleVidsAdapter) -> None:
        self.db = db
        self.settings = settings
        self.adapter = adapter

    async def run(
        self,
        *,
        job_id: uuid.UUID,
        kind: IntentKind,
        target: str,
        request: dict[str, Any],
        action: Callable[[str], Awaitable[BaseModel]],
        on_confirm: OnConfirm,
        project: ProjectHandle | None = None,
        claim: BudgetClaim | None = None,
    ) -> dict[str, Any]:
        replay = await self._resolve_previous(job_id, kind, target, request, project, on_confirm)
        if replay is not None:
            return replay

        async with self.db.transaction() as session:
            n = await session.scalar(select(func.count()).select_from(ActionIntent).where(
                ActionIntent.job_id == job_id, ActionIntent.kind == kind.value,
                ActionIntent.target == target))
            key = f"{job_id}:{kind.value}:{target}#{(n or 0) + 1}"
            entry_id = None
            if claim is not None:
                entry = await budget.reserve(
                    session, self.settings, account_key=claim.account_key, job_id=job_id,
                    campaign_id=claim.campaign_id, scene_id=claim.scene_id, purpose=claim.purpose,
                )
                entry.note = key
                entry_id = entry.id
            intent = ActionIntent(job_id=job_id, kind=kind.value, target=target,
                                  idempotency_key=key,
                                  request={**request, "budget_entry_id": str(entry_id) if entry_id else None})
            session.add(intent)
            await session.flush()
            intent_id = intent.id
            await audit.record(session, type="intent.pending", job_id=job_id,
                               message=f"{kind.value} {target}", data={"key": key})

        try:
            outcome = await action(key)
        except _DID_NOT_HAPPEN as exc:
            await self._abandon(intent_id, entry_id, f"{type(exc).__name__}: {exc.message}")
            raise
        except UIMismatchError as exc:
            # A precondition mismatch is detected before clicking; one detected after the
            # click leaves the outcome unknown. The adapter says which in `details`.
            if exc.details.get("before_action"):
                await self._abandon(intent_id, entry_id, exc.message)
            raise
        # Any other failure leaves the intent PENDING on purpose: the next attempt reconciles.

        result = outcome.model_dump(mode="json")
        async with self.db.transaction() as session:
            intent = await session.get(ActionIntent, intent_id, with_for_update=True)
            intent.status = IntentStatus.CONFIRMED.value
            intent.result = result
            if entry_id:
                await budget.settle(session, entry_id, BudgetEntryStatus.SPENT)
            await on_confirm(session, result)
            await audit.record(session, type="intent.confirmed", job_id=job_id,
                               message=f"{kind.value} {target}", data={"key": key})
        return result

    async def _abandon(self, intent_id: uuid.UUID, entry_id: uuid.UUID | None, why: str) -> None:
        async with self.db.transaction() as session:
            intent = await session.get(ActionIntent, intent_id, with_for_update=True)
            intent.status = IntentStatus.ABANDONED.value
            intent.error = {"reason": why[:500]}
            if entry_id:
                await budget.settle(session, entry_id, BudgetEntryStatus.RELEASED, why[:200])

    async def _resolve_previous(
        self, job_id: uuid.UUID, kind: IntentKind, target: str, request: dict[str, Any],
        project: ProjectHandle | None, on_confirm: OnConfirm,
    ) -> dict[str, Any] | None:
        async with self.db.session() as session:
            rows = (await session.execute(
                select(ActionIntent).where(ActionIntent.job_id == job_id,
                                           ActionIntent.kind == kind.value,
                                           ActionIntent.target == target)
                .order_by(ActionIntent.created_at.desc())
            )).scalars().all()
        for intent in rows:
            if intent.status in (IntentStatus.CONFIRMED.value, IntentStatus.RECONCILED.value):
                return intent.result  # already done; domain rows were written with it
            if intent.status != IntentStatus.PENDING.value:
                continue
            outcome = await self.adapter.reconcile(project, kind, intent.idempotency_key,
                                                   intent.request)
            entry_id = intent.request.get("budget_entry_id")
            if outcome.status is ReconcileStatus.FOUND and outcome.result is not None:
                async with self.db.transaction() as session:
                    locked = await session.get(ActionIntent, intent.id, with_for_update=True)
                    locked.status = IntentStatus.RECONCILED.value
                    locked.result = outcome.result
                    if entry_id:
                        await budget.settle(session, uuid.UUID(entry_id), BudgetEntryStatus.SPENT)
                    await on_confirm(session, outcome.result)
                    await audit.record(session, type="intent.reconciled", job_id=job_id,
                                       message=f"{kind.value} {target}: found after restart",
                                       data={"key": intent.idempotency_key, "note": outcome.note})
                return outcome.result
            if outcome.status is ReconcileStatus.NOT_FOUND:
                await self._abandon(intent.id, uuid.UUID(entry_id) if entry_id else None,
                                    f"reconciled: not found ({outcome.note})")
                async with self.db.transaction() as session:
                    await audit.record(session, type="intent.abandoned", job_id=job_id,
                                       message=f"{kind.value} {target}: did not happen; retrying",
                                       data={"key": intent.idempotency_key})
                return None
            raise AmbiguousIntentError(
                f"after a restart it cannot be determined whether {kind.value} for {target} "
                "happened in Vids",
                details={"intent_id": str(intent.id), "key": intent.idempotency_key,
                         "note": outcome.note},
            )
        return None

    async def resolve_by_human(self, intent_id: uuid.UUID, happened: bool, actor: str) -> None:
        """A person looked at the Vid: record what they saw."""
        async with self.db.transaction() as session:
            intent = await session.get(ActionIntent, intent_id, with_for_update=True)
            if intent is None or intent.status != IntentStatus.PENDING.value:
                return
            entry_id = intent.request.get("budget_entry_id")
            intent.status = IntentStatus.ABANDONED.value
            intent.error = {"reason": f"resolved by {actor}: "
                                      f"{'happened (result unusable)' if happened else 'did not happen'}"}
            if entry_id:
                # If it happened, the generation was spent even though we cannot use it.
                await budget.settle(session, uuid.UUID(entry_id),
                                    BudgetEntryStatus.SPENT if happened else BudgetEntryStatus.RELEASED)
            await audit.record(session, type="intent.resolved_by_human", actor=actor,
                               job_id=intent.job_id, message=intent.error["reason"],
                               data={"intent_id": str(intent.id)})
