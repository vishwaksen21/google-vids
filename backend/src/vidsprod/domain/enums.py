"""State vocabularies shared by the database, the workflow engine and the API."""

from __future__ import annotations

from enum import StrEnum


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    PASSED = "PASSED"
    PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"

    @property
    def is_complete(self) -> bool:
        return self in (StageStatus.PASSED, StageStatus.PASSED_WITH_WARNINGS, StageStatus.SKIPPED)


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        return self in (
            JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED, JobStatus.WAITING_FOR_HUMAN
        )


class JobControl(StrEnum):
    PAUSE = "PAUSE"
    CANCEL = "CANCEL"


class StageId(StrEnum):
    """The production pipeline, in order."""

    INTAKE = "INTAKE"
    CREATIVE = "CREATIVE"
    SCRIPT = "SCRIPT"
    SCENE_PLAN = "SCENE_PLAN"
    PROMPTS = "PROMPTS"
    PLAN_APPROVAL = "PLAN_APPROVAL"
    VIDS_SESSION = "VIDS_SESSION"
    VIDS_PROJECT = "VIDS_PROJECT"
    SCENE_PRODUCTION = "SCENE_PRODUCTION"
    TIMELINE_ASSEMBLY = "TIMELINE_ASSEMBLY"
    TIMELINE_EDITS = "TIMELINE_EDITS"
    PREVIEW = "PREVIEW"
    EXPORT = "EXPORT"
    FINAL_QA = "FINAL_QA"
    COMPLIANCE_APPROVAL = "COMPLIANCE_APPROVAL"
    DELIVERY = "DELIVERY"


STAGE_ORDER: tuple[StageId, ...] = tuple(StageId)
PLANNING_STAGES = frozenset(
    {StageId.INTAKE, StageId.CREATIVE, StageId.SCRIPT, StageId.SCENE_PLAN, StageId.PROMPTS}
)


class CampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    IN_PRODUCTION = "IN_PRODUCTION"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    DELIVERED = "DELIVERED"
    ARCHIVED = "ARCHIVED"


class SceneStatus(StrEnum):
    PLANNED = "PLANNED"  # in the scene plan; no prompt yet
    READY = "READY"  # prompt prepared, nothing submitted
    SUBMITTED = "SUBMITTED"  # generation recorded as an intent and handed to Vids
    GENERATING = "GENERATING"  # Vids confirmed the generation started
    GENERATED = "GENERATED"  # a take exists, not yet inspected
    INSPECTING = "INSPECTING"
    NEEDS_EDIT = "NEEDS_EDIT"
    REGENERATION_REQUIRED = "REGENERATION_REQUIRED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    ACCEPTED = "ACCEPTED"  # locked
    BLOCKED = "BLOCKED"  # waiting on budget, quota or a capability; resumes where it was
    FAILED = "FAILED"


class TakeStatus(StrEnum):
    GENERATED = "GENERATED"
    INSPECTED = "INSPECTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class TakeOrigin(StrEnum):
    GENERATE = "GENERATE"
    EDIT = "EDIT"


class Verdict(StrEnum):
    PASS = "PASS"  # noqa: S105 - not a password
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"  # noqa: S105
    NEEDS_EDIT = "NEEDS_EDIT"
    NEEDS_REGENERATION = "NEEDS_REGENERATION"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class CorrectionAction(StrEnum):
    ACCEPT_AND_LOCK = "ACCEPT_AND_LOCK"
    EDIT = "EDIT"
    REGENERATE = "REGENERATE"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class Severity(StrEnum):
    BLOCKER = "BLOCKER"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    INFO = "INFO"


class RuleCategory(StrEnum):
    OPERATIONAL = "OPERATIONAL"
    CONTENT = "CONTENT"
    BRAND_POLICY = "BRAND_POLICY"
    TECHNICAL = "TECHNICAL"


class RuleOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


class CapabilityStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    UNSUPPORTED = "UNSUPPORTED"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"


class ApprovalKind(StrEnum):
    PLAN = "PLAN"
    SCENE_REVIEW = "SCENE_REVIEW"
    COMPLIANCE = "COMPLIANCE"
    SUBMISSION = "SUBMISSION"
    INTERVENTION = "INTERVENTION"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"


class IntentStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    RECONCILED = "RECONCILED"
    ABANDONED = "ABANDONED"


class IntentKind(StrEnum):
    CREATE_PROJECT = "CREATE_PROJECT"
    GENERATE_CLIP = "GENERATE_CLIP"
    EDIT_CLIP = "EDIT_CLIP"
    INSERT_CLIP = "INSERT_CLIP"
    TIMELINE_OP = "TIMELINE_OP"
    EXPORT = "EXPORT"


class BudgetEntryStatus(StrEnum):
    RESERVED = "RESERVED"
    SPENT = "SPENT"
    RELEASED = "RELEASED"


class ArtifactKind(StrEnum):
    SCREENSHOT = "SCREENSHOT"
    FRAME = "FRAME"
    CLIP = "CLIP"
    EXPORT = "EXPORT"
    REPORT = "REPORT"
    DOM_SNAPSHOT = "DOM_SNAPSHOT"
    TRANSCRIPT = "TRANSCRIPT"
    DELIVERABLE = "DELIVERABLE"
    BRAND_ASSET = "BRAND_ASSET"


class ExportStatus(StrEnum):
    REQUESTED = "REQUESTED"
    DOWNLOADED = "DOWNLOADED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class DeliveryStatus(StrEnum):
    STORED = "STORED"
    SUBMITTED = "SUBMITTED"
    BLOCKED = "BLOCKED"


class ScriptLineKind(StrEnum):
    VOICEOVER = "VOICEOVER"
    DIALOGUE = "DIALOGUE"


class TransitionKind(StrEnum):
    CUT = "CUT"
    DISSOLVE = "DISSOLVE"
    FADE = "FADE"


class TimelineOpKind(StrEnum):
    ARRANGE = "ARRANGE"
    TRANSITION = "TRANSITION"
    VOICEOVER = "VOICEOVER"
    MUSIC = "MUSIC"
    CAPTIONS = "CAPTIONS"
    TEXT_OVERLAY = "TEXT_OVERLAY"
    LOGO = "LOGO"


class AspectRatio(StrEnum):
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
