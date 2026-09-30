"""Async engine and session factory.

Transactions are kept short: no external call (browser, AI provider) is ever made while a
transaction holds row locks. Services open a session per unit of work.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from vidsprod.config import Settings


def make_engine(settings: Settings, url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        url or settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        pool_pre_ping=True,  # survive a database restart without failing the next request
        pool_recycle=1800,
        connect_args={
            "server_settings": {
                "statement_timeout": str(settings.db_statement_timeout_ms),
                "idle_in_transaction_session_timeout": "60000",
                "application_name": "vidsprod",
            }
        },
    )


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session, session.begin():
            yield session

    async def ping(self) -> bool:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self.engine.dispose()
