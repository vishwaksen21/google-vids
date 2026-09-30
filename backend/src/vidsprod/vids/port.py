"""The `GoogleVidsAdapter` contract.

The workflow talks to Google Vids only through this interface. Two implementations exist:
`MockVidsAdapter` (deterministic simulation, real media files, scriptable defects) and
`BrowserVidsAdapter` (Playwright on a dedicated Chrome profile, gated by
GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED and by the capability registry).

Every mutating method takes an idempotency key. After a crash the workflow calls
`reconcile` with that key before it will attempt the same action again.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from vidsprod.domain.enums import ArtifactKind, AspectRatio, IntentKind, TimelineOpKind
from vidsprod.vids.capabilities import CapabilityRegistry


class SessionStateName(StrEnum):
    READY = "READY"  # signed in as the allowed account, editor UI verified by recon
    SIGNED_IN = "SIGNED_IN"  # signed in as the allowed account; editor UI not verified yet
    ACCOUNT_UNCONFIRMED = "ACCOUNT_UNCONFIRMED"  # on Vids, but the account could not be read
    SIGNED_OUT = "SIGNED_OUT"
    WRONG_ACCOUNT = "WRONG_ACCOUNT"
    UI_CHANGED = "UI_CHANGED"
    CHALLENGE = "CHALLENGE"  # Google asks for verification; stop, never bypass
    DISABLED = "DISABLED"  # the browser is not enabled in this mode
    NOT_RUNNING = "NOT_RUNNING"
    ERROR = "ERROR"


class EvidenceFile(BaseModel):
    path: Path
    kind: ArtifactKind
    mime: str
    label: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    state: SessionStateName
    authenticated: bool = False
    account_confirmed: bool = False
    page: str | None = None  # host and path only; never query strings
    account_label: str | None = None
    ui_fingerprint: str | None = None
    fingerprint_matches: bool | None = None
    simulated: bool = False
    message: str = ""
    evidence: list[EvidenceFile] = Field(default_factory=list)


class ProjectHandle(BaseModel):
    project_id: str
    title: str
    url: str | None = None
    created_by_us: bool = True
    simulated: bool = False


class ClipRequest(BaseModel):
    scene_key: str
    take_no: int
    prompt: str
    duration_seconds: float
    aspect_ratio: AspectRatio
    spoken_lines: list[str] = Field(default_factory=list)
    ingredient_paths: list[Path] = Field(default_factory=list)
    idempotency_key: str


class ClipCandidate(BaseModel):
    clip_id: str
    scene_key: str
    take_no: int
    requested_duration_seconds: float
    locator: dict[str, Any] = Field(default_factory=dict)  # how to find it again in Vids
    simulated: bool = False
    idempotency_key: str


class ClipEvidence(BaseModel):
    clip_id: str
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    has_audio: bool | None = None
    frames: list[EvidenceFile] = Field(default_factory=list)
    media: EvidenceFile | None = None  # the clip itself, when Vids lets us have it
    audio: EvidenceFile | None = None
    screenshots: list[EvidenceFile] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    simulated: bool = False
    simulation_hints: dict[str, Any] = Field(default_factory=dict)


class TimelineItem(BaseModel):
    item_id: str
    clip_id: str
    scene_key: str
    position: int
    duration_seconds: float
    fingerprint: str | None = None


class TimelineOpSpec(BaseModel):
    op_key: str
    kind: TimelineOpKind
    params: dict[str, Any]


class TimelineState(BaseModel):
    items: list[TimelineItem] = Field(default_factory=list)
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    text_overlays: list[dict[str, Any]] = Field(default_factory=list)
    logos: list[dict[str, Any]] = Field(default_factory=list)
    voiceover: list[dict[str, Any]] = Field(default_factory=list)
    music: list[dict[str, Any]] = Field(default_factory=list)
    captions_enabled: bool = False
    total_duration_seconds: float = 0.0
    applied_ops: list[str] = Field(default_factory=list)


class ExportSpec(BaseModel):
    format: str = "mp4"
    idempotency_key: str


class ExportResult(BaseModel):
    path: Path
    simulated: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ReconcileStatus(StrEnum):
    FOUND = "FOUND"  # the action happened; here is its result
    NOT_FOUND = "NOT_FOUND"  # it verifiably did not happen; safe to retry
    UNKNOWN = "UNKNOWN"  # cannot tell; a human must look


class ReconcileResult(BaseModel):
    status: ReconcileStatus
    result: dict[str, Any] | None = None
    note: str = ""


class GoogleVidsAdapter(Protocol):
    name: str
    simulated: bool
    capabilities: CapabilityRegistry
    account_key: str

    async def check_session(self, evidence_dir: Path) -> SessionState: ...

    async def open_or_create_project(
        self, title: str, existing: ProjectHandle | None, idempotency_key: str
    ) -> ProjectHandle: ...

    async def upload_reference(self, project: ProjectHandle, path: Path, label: str) -> dict[str, Any]: ...

    async def generate_clip(self, project: ProjectHandle, request: ClipRequest) -> ClipCandidate: ...

    async def capture_clip(
        self, project: ProjectHandle, clip: ClipCandidate, evidence_dir: Path
    ) -> ClipEvidence: ...

    async def edit_clip(
        self, project: ProjectHandle, clip: ClipCandidate, instruction: str, take_no: int,
        idempotency_key: str,
    ) -> ClipCandidate: ...

    async def insert_clip(
        self, project: ProjectHandle, clip: ClipCandidate, position: int, idempotency_key: str
    ) -> TimelineItem: ...

    async def read_timeline(self, project: ProjectHandle) -> TimelineState: ...

    async def apply_timeline_op(self, project: ProjectHandle, op: TimelineOpSpec) -> TimelineState: ...

    async def capture_preview(self, project: ProjectHandle, evidence_dir: Path) -> list[EvidenceFile]: ...

    async def export(self, project: ProjectHandle, spec: ExportSpec, out_dir: Path) -> ExportResult: ...

    async def reconcile(
        self, project: ProjectHandle | None, kind: IntentKind, idempotency_key: str,
        request: dict[str, Any],
    ) -> ReconcileResult: ...

    async def close(self) -> None: ...
