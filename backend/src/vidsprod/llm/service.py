"""The AI service every agent calls.

It owns the behaviour that must not differ between providers: a hard timeout per call,
bounded retries with exponential backoff for transient errors, structured-output parsing
and validation against the requested Pydantic model (with a bounded repair round that
tells the model exactly what was wrong), and a tracking row per request (provider, model,
latency, tokens, cost where known, a hash of the request — never the content itself).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal

from pydantic import ValidationError

from vidsprod.config import AIProviderName, Settings
from vidsprod.db.models import AIRequest
from vidsprod.db.session import Database
from vidsprod.errors import (
    ProviderError,
    ProviderTransientError,
    StructuredOutputError,
)
from vidsprod.llm.providers import GatewayProvider, GeminiDirectProvider, LLMProvider, model_for
from vidsprod.llm.types import LLMRequest, LLMResponse, ProviderReply
from vidsprod.logging import get_logger

log = get_logger(__name__)

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def build_provider(settings: Settings) -> LLMProvider:
    if settings.ai_provider is AIProviderName.GATEWAY:
        return GatewayProvider(settings)
    if settings.ai_provider is AIProviderName.GEMINI:
        return GeminiDirectProvider(settings)
    from vidsprod.llm.fake import FakeProvider

    return FakeProvider()


def extract_json(text: str) -> object:
    """JSON from a model answer that may be fenced or wrapped in a sentence."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


class AIService:
    def __init__(
        self,
        settings: Settings,
        provider: LLMProvider,
        db: Database | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.db = db
        self._sleep = sleep

    @property
    def provider_name(self) -> str:
        return self.provider.name

    async def generate(self, request: LLMRequest) -> LLMResponse:
        model = model_for(self.settings, request.role)
        started = time.monotonic()
        attempts = 0
        repairs = 0
        last_error: Exception | None = None
        prompt = request.prompt
        usage_in = usage_out = 0
        reply: ProviderReply | None = None
        status = "ERROR"
        try:
            while True:
                attempts += 1
                try:
                    current = LLMRequest(**{**request.__dict__, "prompt": prompt})
                    reply = await asyncio.wait_for(
                        self.provider.generate(current, model),
                        timeout=self.settings.ai_timeout_seconds,
                    )
                except (TimeoutError, ProviderTransientError) as exc:
                    last_error = exc
                    if attempts >= self.settings.ai_max_attempts:
                        raise ProviderTransientError(
                            f"{self.provider.name} failed after {attempts} attempts: {exc}"
                        ) from exc
                    await self._sleep(self._backoff(attempts))
                    continue
                usage_in += reply.usage.input_tokens or 0
                usage_out += reply.usage.output_tokens or 0
                if request.response_model is None:
                    status = "OK"
                    return self._response(request, reply, None, started, attempts, usage_in,
                                          usage_out)
                try:
                    parsed = request.response_model.model_validate(extract_json(reply.text))
                except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                    last_error = exc
                    if repairs >= self.settings.ai_structured_repair_attempts:
                        status = "INVALID_OUTPUT"
                        raise StructuredOutputError(
                            f"{request.purpose}: the model's answer did not match "
                            f"{request.response_model.__name__} after {repairs} repair(s)",
                            details={"errors": _error_summary(exc)},
                        ) from exc
                    repairs += 1
                    prompt = (
                        f"{request.prompt}\n\nYour previous answer was not valid. Problems:\n"
                        f"{_error_summary(exc)}\nReturn the corrected JSON object only."
                    )
                    continue
                status = "OK"
                return self._response(request, reply, parsed, started, attempts, usage_in,
                                      usage_out)
        except ProviderError as exc:
            last_error = exc
            raise
        finally:
            await self._track(request, model, status, attempts, started, usage_in, usage_out,
                              None if status == "OK" else last_error)

    def _backoff(self, attempt: int) -> float:
        base = self.settings.ai_backoff_base_seconds * (2 ** (attempt - 1))
        return base + random.uniform(0, base / 2)  # noqa: S311 - jitter, not security

    def _cost(self, tokens_in: int, tokens_out: int) -> float | None:
        s = self.settings
        if s.ai_cost_per_mtok_input is None or s.ai_cost_per_mtok_output is None:
            return None
        return (tokens_in * s.ai_cost_per_mtok_input + tokens_out * s.ai_cost_per_mtok_output) / 1e6

    def _response(self, request, reply, parsed, started, attempts, usage_in, usage_out):
        from vidsprod.llm.types import Usage

        return LLMResponse(
            text=reply.text, parsed=parsed, provider=self.provider.name, model=reply.model,
            usage=Usage(usage_in or None, usage_out or None),
            latency_ms=int((time.monotonic() - started) * 1000), attempts=attempts,
            cost_estimate=self._cost(usage_in, usage_out),
        )

    async def _track(self, request, model, status, attempts, started, usage_in, usage_out, error):
        if self.db is None:
            return
        digest = hashlib.sha256(
            (request.purpose + model + request.prompt).encode()
            + b"".join(hashlib.sha256(m.data).digest() for m in request.media)
        ).hexdigest()
        cost = self._cost(usage_in, usage_out)
        try:
            async with self.db.transaction() as session:
                session.add(AIRequest(
                    job_id=request.job_id, purpose=request.purpose, provider=self.provider.name,
                    model=model, status=status, attempts=attempts,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    input_tokens=usage_in or None, output_tokens=usage_out or None,
                    cost_estimate=Decimal(str(round(cost, 6))) if cost is not None else None,
                    request_hash=digest, error=str(error)[:500] if error else None,
                ))
        except Exception:  # tracking must never mask the real outcome, but is logged
            log.exception("could not record ai_request", purpose=request.purpose)

    async def aclose(self) -> None:
        await self.provider.aclose()


def _error_summary(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "\n".join(
            f"- {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:15]
        )
    return f"- {type(exc).__name__}: {str(exc)[:300]}"
