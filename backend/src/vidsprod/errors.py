"""Exception hierarchy.

The workflow engine decides what to do with a failure from its *type*, so the classes
here encode the recovery policy:

* `TransientError`: the same action may succeed if repeated (timeout, disconnect,
  provider 5xx). Retried with bounded exponential backoff.
* `HumanRequiredError`: repeating it cannot help; a person has to act (sign in again,
  approve, inspect a changed UI, raise a quota). The stage waits for a human.
* `BlockedError`: a configured boundary refuses the action (live automation disabled,
  capability unverified, budget exhausted). Not retried; reported with the reason.
* `ValidationFailure`: the action ran but its result is wrong.

Anything else is a bug and fails the stage with its traceback recorded.
"""

from __future__ import annotations

from typing import Any


class VidsProdError(Exception):
    code: str = "ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "type": type(self).__name__, "message": self.message,
                "details": self.details}


# ---------------------------------------------------------------- domain / API
class NotFoundError(VidsProdError):
    code = "NOT_FOUND"


class ConflictError(VidsProdError):
    code = "CONFLICT"


class InvalidInputError(VidsProdError):
    code = "INVALID_INPUT"


# ---------------------------------------------------------------- recovery classes
class TransientError(VidsProdError):
    code = "TRANSIENT"


class HumanRequiredError(VidsProdError):
    code = "HUMAN_REQUIRED"


class BlockedError(VidsProdError):
    code = "BLOCKED"


class ValidationFailure(VidsProdError):
    code = "VALIDATION_FAILED"


# ---------------------------------------------------------------- AI provider
class ProviderError(VidsProdError):
    code = "PROVIDER_ERROR"


class ProviderTransientError(ProviderError, TransientError):
    code = "PROVIDER_TRANSIENT"


class ProviderNotConfiguredError(ProviderError, BlockedError):
    code = "PROVIDER_NOT_CONFIGURED"


class StructuredOutputError(ProviderError):
    """The model answered, but not in the requested schema, even after repair."""

    code = "STRUCTURED_OUTPUT_INVALID"


# ---------------------------------------------------------------- Google Vids
class VidsError(VidsProdError):
    code = "VIDS_ERROR"


class LiveAutomationDisabledError(VidsError, BlockedError):
    code = "LIVE_AUTOMATION_DISABLED"


class CapabilityUnavailableError(VidsError, BlockedError):
    """The operation is not VERIFIED in the capability registry."""

    code = "CAPABILITY_UNAVAILABLE"


class SessionExpiredError(VidsError, HumanRequiredError):
    code = "SESSION_EXPIRED"


class WrongAccountError(VidsError, HumanRequiredError):
    code = "WRONG_ACCOUNT"


class UIMismatchError(VidsError, HumanRequiredError):
    """The page is not in the state the next action assumes. Never clicked through."""

    code = "UI_MISMATCH"


class UnsavedClipError(VidsError, HumanRequiredError):
    """A generated clip is open in the page but not inserted; leaving the page loses it."""

    code = "UNSAVED_CLIP"


class GenerationRefusedError(VidsError):
    """Vids declined the prompt (content policy). Handled by prompt revision."""

    code = "GENERATION_REFUSED"


class QuotaExhaustedError(VidsError, HumanRequiredError):
    code = "QUOTA_EXHAUSTED"


class BrowserDisconnectedError(VidsError, TransientError):
    code = "BROWSER_DISCONNECTED"


class GenerationTimeoutError(VidsError, TransientError):
    code = "GENERATION_TIMEOUT"


class ProtectedProjectError(VidsError, BlockedError):
    """Refuses to touch a Vids project this system did not create."""

    code = "PROTECTED_PROJECT"


# ---------------------------------------------------------------- budget / intents
class BudgetExhaustedError(HumanRequiredError):
    """Waits for a person: raise the configured limit, or wait for the monthly reset."""

    code = "BUDGET_EXHAUSTED"


class AmbiguousIntentError(HumanRequiredError):
    """After a restart, a non-idempotent action's outcome cannot be determined."""

    code = "AMBIGUOUS_INTENT"
