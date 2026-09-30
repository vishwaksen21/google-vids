"""The provider-independent AI service and the providers' wire contracts."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
from pydantic import BaseModel

from tests.conftest import make_settings
from vidsprod.errors import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderTransientError,
    StructuredOutputError,
)
from vidsprod.llm.fake import FakeProvider
from vidsprod.llm.providers import GatewayProvider, GeminiDirectProvider
from vidsprod.llm.schema import to_gemini_schema
from vidsprod.llm.service import AIService, extract_json
from vidsprod.llm.types import LLMRequest, MediaPart, ModelRole


class Answer(BaseModel):
    name: str
    score: int
    tags: list[str] = []
    note: str | None = None


async def _no_sleep(_):
    return None


def _svc(tmp_path, script, **settings):
    s = make_settings(tmp_path, **settings)
    return AIService(s, FakeProvider(script=script, simulate=False), None, sleep=_no_sleep)


def _req(**kw):
    return LLMRequest(purpose="p", role=ModelRole.REASONING, prompt="q", response_model=Answer, **kw)


async def test_structured_output_is_parsed_and_validated(tmp_path):
    svc = _svc(tmp_path, {"p": [{"name": "a", "score": 3}]})
    reply = await svc.generate(_req())
    assert reply.parsed == Answer(name="a", score=3) and reply.provider == "fake"


async def test_fenced_and_wrapped_json_is_accepted():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": 1} hope it helps') == {"a": 1}


async def test_invalid_output_gets_one_repair_round_with_the_errors(tmp_path):
    provider_script = {"p": [{"name": "a"}, {"name": "a", "score": 2}]}
    svc = _svc(tmp_path, provider_script)
    reply = await svc.generate(_req())
    assert reply.parsed.score == 2 and reply.attempts == 2
    repair_prompt = svc.provider.calls[1].prompt
    assert "previous answer was not valid" in repair_prompt and "score" in repair_prompt


async def test_output_that_stays_invalid_is_an_error_not_a_guess(tmp_path):
    svc = _svc(tmp_path, {"p": ["not json at all", "still not json"]})
    with pytest.raises(StructuredOutputError):
        await svc.generate(_req())


async def test_transient_errors_are_retried_with_a_bound(tmp_path):
    svc = _svc(tmp_path, {"p": [ProviderTransientError("503"), {"name": "a", "score": 1}]})
    assert (await svc.generate(_req())).parsed.score == 1
    always = _svc(tmp_path, {"p": [ProviderTransientError("503")]}, ai_max_attempts=3)
    with pytest.raises(ProviderTransientError, match="after 3 attempts"):
        await always.generate(_req())
    assert len(always.provider.calls) == 3


async def test_a_hung_provider_times_out(tmp_path):
    class Slow(FakeProvider):
        async def generate(self, request, model):
            await asyncio.sleep(5)

    s = make_settings(tmp_path, ai_timeout_seconds=0.05, ai_max_attempts=2)
    svc = AIService(s, Slow(), None, sleep=_no_sleep)
    with pytest.raises(ProviderTransientError):
        await svc.generate(_req())


async def test_model_selection_comes_from_configuration(tmp_path):
    svc = _svc(tmp_path, {"p": [{"name": "a", "score": 1}]}, ai_model_reasoning="model-x")
    assert (await svc.generate(_req())).model == "fake:model-x"


async def test_requests_are_tracked_without_their_content(clean_db, settings):
    from sqlalchemy import select

    from tests.conftest import build_container
    from vidsprod.db.models import AIRequest

    c = await build_container(settings, clean_db, provider=FakeProvider(
        script={"p": [{"name": "secret campaign name", "score": 1}]}, simulate=False))
    try:
        await c.ai.generate(_req())
        async with c.db.session() as session:
            row = await session.scalar(select(AIRequest))
        assert row.status == "OK" and row.provider == "fake" and row.output_tokens
        assert "secret" not in json.dumps({k: str(v) for k, v in row.__dict__.items()})
    finally:
        await c.close()


# ------------------------------------------------------------------ gateway contract
def _gateway(tmp_path, handler):
    s = make_settings(tmp_path, ai_provider="gateway", gateway_base_url="https://gw.example",
                      gateway_api_key="k-123", gateway_thinking_budget=512)
    client = httpx.AsyncClient(base_url="https://gw.example", transport=httpx.MockTransport(handler))
    return s, GatewayProvider(s, client=client)


async def test_gateway_sends_the_verified_request_shape(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"text": '{"name":"a","score":1}', "model": "m",
                                         "usage": {"prompt_token_count": 10,
                                                   "candidates_token_count": 5}})

    s, provider = _gateway(tmp_path, handler)
    reply = await provider.generate(LLMRequest(
        purpose="p", role=ModelRole.VISION, prompt="look", system="sys", response_model=Answer,
        media=[MediaPart("image/png", b"\x89PNG", "frame 1")]), "gemini-x")
    body = seen["body"]
    assert seen["headers"]["x-api-key"] == "k-123"
    assert "k-123" not in json.dumps(body), "the key travels only as a header"
    assert body["model"] == "gemini-x" and body["system_instruction"] == "sys"
    assert body["config"]["max_output_tokens"] == 4096 + 512
    assert body["config"]["thinking_config"] == {"thinking_budget": 512}
    assert body["config"]["response_mime_type"] == "application/json"
    assert "$defs" not in json.dumps(body["config"]["response_schema"])
    parts = body["contents"][0]["parts"]
    assert parts[1] == {"text": "frame 1"}
    assert parts[2]["inline_data"] == {"mime_type": "image/png",
                                       "data": base64.b64encode(b"\x89PNG").decode()}
    assert reply.usage.input_tokens == 10 and reply.usage.output_tokens == 5


@pytest.mark.parametrize("status,error", [(503, ProviderTransientError), (429, ProviderTransientError),
                                          (401, ProviderError), (422, ProviderError)])
async def test_gateway_errors_are_classified(tmp_path, status, error):
    _, provider = _gateway(tmp_path, lambda r: httpx.Response(status, json={"detail": "x"}))
    with pytest.raises(error):
        await provider.generate(LLMRequest(purpose="p", role=ModelRole.CREATIVE, prompt="q"), "m")


async def test_gateway_explains_a_budget_spent_on_thinking(tmp_path):
    _, provider = _gateway(tmp_path, lambda r: httpx.Response(200, json={"finish_reason": "MAX_TOKENS"}))
    with pytest.raises(ProviderError, match="MAX_TOKENS"):
        await provider.generate(LLMRequest(purpose="p", role=ModelRole.CREATIVE, prompt="q"), "m")


def test_the_gateway_refuses_to_start_unconfigured(tmp_path):
    with pytest.raises(ProviderNotConfiguredError):
        GatewayProvider(make_settings(tmp_path, ai_provider="gateway"))


# ------------------------------------------------------------------ direct Gemini
def test_direct_gemini_is_off_by_default(tmp_path):
    with pytest.raises(ProviderNotConfiguredError, match="disabled"):
        GeminiDirectProvider(make_settings(tmp_path))
    with pytest.raises(ValueError, match="GEMINI_DIRECT_ENABLED"):
        make_settings(tmp_path, ai_provider="gemini")


async def test_direct_gemini_request_shape_and_no_storage(tmp_path):
    captured = {}

    class Interactions:
        async def create(self, **kw):
            captured.update(kw)
            return type("I", (), {"output_text": '{"name":"a","score":1}', "id": "i1",
                                  "usage": type("U", (), {"total_input_tokens": 3,
                                                          "total_output_tokens": 4})()})()

    client = type("C", (), {"aio": type("A", (), {"interactions": Interactions()})()})()
    s = make_settings(tmp_path, gemini_direct_enabled=True)
    provider = GeminiDirectProvider(s, client=client)
    reply = await provider.generate(LLMRequest(
        purpose="p", role=ModelRole.VISION, prompt="look", response_model=Answer,
        media=[MediaPart("image/jpeg", b"\xff\xd8\xff")]), "gemini-3.8-flash")
    assert captured["store"] is False
    assert captured["response_format"]["mime_type"] == "application/json"
    assert captured["input"][1] == {"type": "image", "mime_type": "image/jpeg",
                                    "data": base64.b64encode(b"\xff\xd8\xff").decode()}
    assert reply.usage.output_tokens == 4


def test_pydantic_schema_converts_to_the_gemini_subset():
    schema = to_gemini_schema(Answer)
    text = json.dumps(schema)
    assert "$ref" not in text and "anyOf" not in text and "additionalProperties" not in text
    assert schema["properties"]["note"]["nullable"] is True
