"""Mapping observed browser states to the errors the workflow engine acts on.

The engine's recovery policy follows from the error type: transient errors are retried
with backoff, human-required errors wait for a person (and the job resumes after the
person acts), and blocked errors stop the job with the reason.
"""

from __future__ import annotations

from vidsprod.config import Settings, VidsMode
from vidsprod.errors import (
    CapabilityUnavailableError,
    HumanRequiredError,
    LiveAutomationDisabledError,
    SessionExpiredError,
    UIMismatchError,
    VidsError,
    WrongAccountError,
)
from vidsprod.vids.port import SessionState, SessionStateName


class GoogleChallengeError(VidsError, HumanRequiredError):
    """Google is asking for verification. A person completes it; the agent never does."""

    code = "GOOGLE_CHALLENGE"


def raise_if_wrong(state: SessionState, settings: Settings | None = None) -> None:
    """Raise unless the session is fit for production. READY is the only state that is."""
    if state.state is SessionStateName.READY:
        if settings is not None and settings.google_vids_mode is VidsMode.BROWSER_TEST:
            raise LiveAutomationDisabledError(
                "BROWSER_TEST mode: the session is ready, but production actions are disabled.")
        return
    match state.state:
        case SessionStateName.SIGNED_OUT:
            raise SessionExpiredError(state.message or "not signed in",
                                      details={"session_state": state.state.value})
        case SessionStateName.CHALLENGE:
            raise GoogleChallengeError(state.message, details={"session_state": state.state.value})
        case SessionStateName.WRONG_ACCOUNT | SessionStateName.ACCOUNT_UNCONFIRMED:
            raise WrongAccountError(state.message, details={"session_state": state.state.value})
        case SessionStateName.UI_CHANGED:
            raise UIMismatchError(state.message, details={"session_state": state.state.value})
        case SessionStateName.SIGNED_IN:
            if settings is not None and settings.google_vids_mode is VidsMode.BROWSER_TEST:
                raise LiveAutomationDisabledError(
                    f"BROWSER_TEST mode: {state.message}. Session handling verified; production "
                    "actions are disabled in this mode.",
                    details={"session_state": state.state.value})
            raise CapabilityUnavailableError(
                f"{state.message}, but the Vids editor UI has not been verified by recon, so "
                "the agent cannot operate it safely yet",
                details={"session_state": state.state.value})
        case SessionStateName.DISABLED | SessionStateName.NOT_RUNNING:
            raise LiveAutomationDisabledError(state.message or "the browser is not enabled")
        case _:
            raise VidsError(state.message or "the browser session is in an unknown state")
