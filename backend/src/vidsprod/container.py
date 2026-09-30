"""Wires settings, database, AI service, Vids adapter, storage and the workflow engine."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from vidsprod.brand.design_document import load_brand_guidelines
from vidsprod.brand.guidelines import BrandGuidelines
from vidsprod.brand.seed import ensure_brand_profile
from vidsprod.config import Settings
from vidsprod.db.session import Database, make_engine
from vidsprod.errors import ProviderNotConfiguredError
from vidsprod.llm.providers import LLMProvider
from vidsprod.llm.service import AIService, build_provider
from vidsprod.llm.types import LLMRequest, ProviderReply
from vidsprod.logging import get_logger
from vidsprod.storage.artifacts import ArtifactStore
from vidsprod.vids.factory import build_adapter
from vidsprod.vids.port import GoogleVidsAdapter
from vidsprod.workflow.context import Services
from vidsprod.workflow.control import JobControlService
from vidsprod.workflow.engine import Engine
from vidsprod.workflow.intents import IntentRunner
from vidsprod.workflow.stages import build_handlers

log = get_logger(__name__)


class UnavailableProvider:
    """Stands in when the configured provider cannot be built, so the application still
    starts and reports why. Any AI call fails with the configuration problem, which blocks
    the stage with that message instead of pretending to work."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    async def generate(self, request: LLMRequest, model: str) -> ProviderReply:
        raise ProviderNotConfiguredError(self.reason)

    async def aclose(self) -> None:
        return None


@dataclass
class Container:
    settings: Settings
    db: Database
    ai: AIService
    adapter: GoogleVidsAdapter
    store: ArtifactStore
    brand: BrandGuidelines
    intents: IntentRunner
    engine: Engine
    control: JobControlService
    provider_error: str | None = None
    _worker: asyncio.Task | None = None
    _stop: asyncio.Event | None = None

    @classmethod
    async def build(
        cls, settings: Settings, *, db_url: str | None = None,
        adapter: GoogleVidsAdapter | None = None, provider: LLMProvider | None = None,
        worker_id: str | None = None, seed: bool = True,
    ) -> Container:
        db = Database(make_engine(settings, db_url))
        store = ArtifactStore(settings.artifact_dir)
        brand = load_brand_guidelines(settings.design_md_path)
        provider_error = None
        if provider is None:
            try:
                provider = build_provider(settings)
            except ProviderNotConfiguredError as exc:
                provider_error = exc.message
                provider = UnavailableProvider(settings.ai_provider.value, exc.message)
        ai = AIService(settings, provider, db)
        adapter = adapter or build_adapter(settings)
        intents = IntentRunner(db, settings, adapter)
        services = Services(settings=settings, db=db, ai=ai, adapter=adapter, store=store,
                            brand=brand, intents=intents)
        engine = Engine(services, build_handlers(), worker_id)
        control = JobControlService(db, settings, adapter, intents, brand.source_digest)
        container = cls(settings, db, ai, adapter, store, brand, intents, engine, control,
                        provider_error)
        if seed:
            await ensure_brand_profile(db, store, brand, settings.logo_path)
        return container

    # ------------------------------------------------------------------ worker
    def start_worker(self) -> None:
        self._stop = asyncio.Event()
        self._worker = asyncio.create_task(self._run_worker(), name="vidsprod-worker")

    async def _run_worker(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                job_id = await self.engine.claim_next()
                if job_id is not None:
                    await self.engine.run_job(job_id)
                    continue
            except Exception:  # the worker must survive one bad job; the job records why
                log.exception("worker iteration failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.settings.worker_poll_seconds)

    async def close(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._worker
        await self.adapter.close()
        await self.ai.aclose()
        await self.db.dispose()
