"""AI providers: the company gateway (default), direct Gemini (off by default), and a fake.

A provider does one thing: send a request and return text plus metadata. Timeouts,
retries, schema validation, repair and request tracking are the `AIService`'s job, so
every provider gets them identically.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Protocol

import httpx

from vidsprod.config import Settings
from vidsprod.errors import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderTransientError,
)
from vidsprod.llm.schema import to_gemini_schema
from vidsprod.llm.types import LLMRequest, ModelRole, ProviderReply, Usage
from vidsprod.logging import get_logger

log = get_logger(__name__)


class LLMProvider(Protocol):
    name: str

    async def generate(self, request: LLMRequest, model: str) -> ProviderReply: ...

    async def aclose(self) -> None: ...


def model_for(settings: Settings, role: ModelRole) -> str:
    return {
        ModelRole.CREATIVE: settings.ai_model_creative,
        ModelRole.REASONING: settings.ai_model_reasoning,
        ModelRole.VISION: settings.ai_model_vision,
        ModelRole.TRANSCRIBE: settings.ai_model_transcribe,
    }[role]


def schema_instruction(request: LLMRequest) -> str:
    """Stated in the prompt as well as in the API's schema field, because a model that
    ignores one usually honours the other, and the validator catches the rest."""
    if request.response_model is None:
        return ""
    schema = json.dumps(request.response_model.model_json_schema(), separators=(",", ":"))
    return (
        "\n\nRespond with a single JSON object only, no prose and no code fences, that "
        f"validates against this JSON Schema:\n{schema}"
    )


# ======================================================================= company gateway
class GatewayProvider:
    """The company AI gateway in front of Vertex AI.

    Contract as verified by phi-service-layer/media-generation (docs/UTILITIES_API.md):
    `POST /llm/generate {model, contents, system_instruction?, config, location}` returning
    `{text, usage, model, finish_reason}`; static key in `X-API-Key`; `config` is Google's
    own config object passed through (extra keys are rejected); media travels as
    `inline_data` parts; thinking consumes `max_output_tokens`, so the allowance is added
    on top of the caller's budget.
    """

    name = "gateway"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        if not settings.gateway_base_url or not settings.gateway_api_key:
            raise ProviderNotConfiguredError(
                "The company AI gateway is not configured (GATEWAY_BASE_URL, GATEWAY_API_KEY)"
            )
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.gateway_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.ai_timeout_seconds, connect=10.0),
        )
        self._headers = {"X-API-Key": settings.gateway_api_key.get_secret_value()}

    def payload(self, request: LLMRequest, model: str) -> dict[str, Any]:
        thinking = self._settings.gateway_thinking_budget
        config: dict[str, Any] = {
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens + thinking,
        }
        if thinking:
            config["thinking_config"] = {"thinking_budget": thinking}
        prompt = request.prompt
        if request.response_model is not None:
            config["response_mime_type"] = "application/json"
            if self._settings.gateway_structured_mode == "schema":
                config["response_schema"] = to_gemini_schema(request.response_model)
            prompt += schema_instruction(request)
        if request.media:
            parts: list[dict[str, Any]] = [{"text": prompt}]
            for media in request.media:
                if media.label:
                    parts.append({"text": media.label})
                parts.append({"inline_data": {
                    "mime_type": media.mime_type,
                    "data": base64.b64encode(media.data).decode("ascii"),
                }})
            contents: Any = [{"role": "user", "parts": parts}]
        else:
            contents = prompt
        body: dict[str, Any] = {"model": model, "contents": contents, "config": config,
                                "location": self._settings.gateway_location}
        if request.system:
            body["system_instruction"] = request.system
        return body

    async def generate(self, request: LLMRequest, model: str) -> ProviderReply:
        try:
            response = await self._client.post(
                self._settings.gateway_text_path, json=self.payload(request, model),
                headers=self._headers,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderTransientError(f"gateway unreachable: {type(exc).__name__}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderTransientError(
                f"gateway returned {response.status_code}",
                details={"status": response.status_code},
            )
        if response.status_code in (401, 403):
            raise ProviderError("gateway rejected the credentials (check GATEWAY_API_KEY)",
                                details={"status": response.status_code})
        if response.status_code >= 400:
            raise ProviderError(
                f"gateway rejected the request ({response.status_code})",
                details={"status": response.status_code, "body": response.text[:500]},
            )
        body = response.json()
        text = body.get("text") if isinstance(body, dict) else None
        finish = body.get("finish_reason") if isinstance(body, dict) else None
        if not text:
            if finish == "MAX_TOKENS":
                raise ProviderError(
                    "the model spent its output budget before answering (MAX_TOKENS); "
                    "raise max_output_tokens or GATEWAY_THINKING_BUDGET"
                )
            raise ProviderError(f"the gateway returned no text (finish_reason={finish})")
        usage = body.get("usage") or {}
        return ProviderReply(
            text=text,
            model=str(body.get("model") or model),
            usage=Usage(
                input_tokens=_first_int(usage, "prompt_token_count", "input_tokens",
                                        "promptTokenCount"),
                output_tokens=_first_int(usage, "candidates_token_count", "output_tokens",
                                         "candidatesTokenCount"),
            ),
            finish_reason=finish,
        )

    async def list_models(self) -> list[str]:
        response = await self._client.get(
            self._settings.gateway_models_path,
            params={"location": self._settings.gateway_location}, headers=self._headers,
        )
        response.raise_for_status()
        return [str(m.get("id") or m.get("name")) for m in response.json().get("models", [])]

    async def aclose(self) -> None:
        await self._client.aclose()


# ======================================================================= direct Gemini
class GeminiDirectProvider:
    """Direct Gemini API via `google-genai` (Interactions API).

    Disabled unless GEMINI_DIRECT_ENABLED=true, which must only be set once sending
    campaign content directly to the Gemini API has been approved. Interactions are sent
    with `store=False` unless GEMINI_STORE_INTERACTIONS=true.

    Status: implemented against the documented request shape (`input` parts, `response_format`
    with a JSON schema, `store`); not integration-tested, because no approved key exists.
    """

    name = "gemini"

    def __init__(self, settings: Settings, client: Any = None) -> None:
        if not settings.gemini_direct_enabled:
            raise ProviderNotConfiguredError(
                "Direct Gemini access is disabled (GEMINI_DIRECT_ENABLED=false)"
            )
        if client is None:
            if not settings.gemini_api_key:
                raise ProviderNotConfiguredError("GEMINI_API_KEY is not set")
            try:
                from google import genai  # optional dependency: `uv sync --extra gemini`
            except ImportError as exc:
                raise ProviderNotConfiguredError(
                    "google-genai is not installed (uv sync --extra gemini)"
                ) from exc
            client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self._client = client
        self._store = settings.gemini_store_interactions

    def payload(self, request: LLMRequest, model: str) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        for media in request.media:
            if media.label:
                parts.append({"type": "text", "text": media.label})
            kind = media.mime_type.split("/", 1)[0]
            parts.append({"type": kind, "mime_type": media.mime_type,
                          "data": base64.b64encode(media.data).decode("ascii")})
        kwargs: dict[str, Any] = {
            "model": model,
            "input": parts if len(parts) > 1 else request.prompt,
            "store": self._store,
            "generation_config": {"temperature": request.temperature,
                                  "max_output_tokens": request.max_output_tokens},
        }
        if request.system:
            kwargs["system_instruction"] = request.system
        if request.response_model is not None:
            kwargs["response_format"] = {
                "type": "text", "mime_type": "application/json",
                "schema": request.response_model.model_json_schema(),
            }
        return kwargs

    async def generate(self, request: LLMRequest, model: str) -> ProviderReply:
        kwargs = self.payload(request, model)
        try:
            aio = getattr(self._client, "aio", None)
            if aio is not None:
                interaction = await aio.interactions.create(**kwargs)
            else:  # pragma: no cover - sync client fallback
                import asyncio

                interaction = await asyncio.to_thread(self._client.interactions.create, **kwargs)
        except Exception as exc:  # the SDK raises its own hierarchy; classify by status
            status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            if status in (429, 500, 502, 503, 504) or isinstance(exc, TimeoutError):
                raise ProviderTransientError(f"Gemini transient error ({status})") from exc
            raise ProviderError(f"Gemini request failed: {type(exc).__name__}") from exc
        text = getattr(interaction, "output_text", None)
        if not text:
            raise ProviderError("Gemini returned no text")
        usage = getattr(interaction, "usage", None)
        return ProviderReply(
            text=text, model=model,
            usage=Usage(
                input_tokens=_attr_int(usage, "total_input_tokens", "input_tokens"),
                output_tokens=_attr_int(usage, "total_output_tokens", "output_tokens"),
            ),
            raw_id=getattr(interaction, "id", None),
        )

    async def aclose(self) -> None:
        return None


def _first_int(mapping: dict, *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int):
            return value
    return None


def _attr_int(obj: Any, *names: str) -> int | None:
    for name in names:
        value = getattr(obj, name, None) if obj is not None else None
        if isinstance(value, int):
            return value
    return None
