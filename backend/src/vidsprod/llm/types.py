"""Provider-independent request and response types.

Agents build an `LLMRequest` and receive an `LLMResponse`; they never import a provider
SDK. The `role` picks the configured model (creative, reasoning, vision, transcribe), so
model identifiers live in configuration only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ModelRole(StrEnum):
    CREATIVE = "creative"
    REASONING = "reasoning"
    VISION = "vision"
    TRANSCRIBE = "transcribe"


@dataclass(frozen=True)
class MediaPart:
    mime_type: str  # image/png, image/jpeg, audio/wav, audio/mpeg, video/mp4
    data: bytes
    label: str = ""  # e.g. "frame at 2.0s", placed as text before the media


@dataclass
class LLMRequest:
    purpose: str  # "intake", "creative", "scene_inspection", ...: for tracking and fakes
    role: ModelRole
    prompt: str
    system: str | None = None
    media: list[MediaPart] = field(default_factory=list)
    response_model: type[BaseModel] | None = None
    max_output_tokens: int = 4096
    temperature: float = 0.4
    job_id: Any = None
    # Read only by the fake provider. The mock Vids adapter records what it simulated
    # (e.g. the words its clip "says") so a simulated run can be inspected end to end.
    # Real providers ignore this field, and the real browser adapter never sets it.
    simulation_hints: dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class ProviderReply:
    """What a provider returns before schema validation."""

    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    raw_id: str | None = None


@dataclass
class LLMResponse:
    text: str
    parsed: BaseModel | None
    provider: str
    model: str
    usage: Usage
    latency_ms: int
    attempts: int
    request_id: Any = None
    cost_estimate: float | None = None
