"""Sign-in detection, and the manual-login flow.

Detection uses facts that need no Vids-specific selector:

* Google's sign-in lives on accounts.google.com, so being redirected there means signed out.
* Verification pages ("unusual traffic", /sorry/, challenge flows) mean stop: a person
  must deal with them, and the agent never tries to get past one.
* On the Vids host, the signed-in account is confirmed when the allowed account's address
  appears in the page's accessibility tree (Google's account button names it). If another
  address appears instead, it is the wrong account. If none does, the account is
  unconfirmed and production does not start.

The last rule is a heuristic until it is observed against the live page; it is recorded
as such in the capability registry.

The agent never types credentials. `open_login` shows the sign-in page in the dedicated
profile; the person signs in; `wait_for_login` notices.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from vidsprod.config import Settings
from vidsprod.domain.enums import ArtifactKind
from vidsprod.logging import get_logger
from vidsprod.vids.port import EvidenceFile, SessionState, SessionStateName

from .browser_manager import BrowserSessionManager, safe_url

log = get_logger(__name__)

_SIGNIN_HOSTS = ("accounts.google.com",)
_CHALLENGE = re.compile(r"(unusual traffic|verify it's you|confirm it's you|/sorry/|captcha)", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def mask(account: str) -> str:
    name, _, domain = account.partition("@")
    return f"{name[:2]}***@{domain}" if domain else f"{account[:2]}***"


async def _snapshot_text(page: Any) -> str:
    try:
        return await page.locator("body").aria_snapshot(timeout=10_000)
    except Exception:
        try:
            return await page.locator("body").inner_text(timeout=10_000)
        except Exception:
            return ""


# Observed 2026-09-29: vids.google.com redirects a signed-in profile to the Vids app at
# docs.google.com/videos/u/<n>/. Both are Google Vids; anything else is not.
_VIDS_APP = (("docs.google.com", "/videos"),)


def is_vids_page(url: str, settings: Settings) -> bool:
    parts = urlparse(url or "")
    if parts.hostname == urlparse(settings.google_vids_url).hostname:
        return True
    return any(parts.hostname == host and parts.path.startswith(prefix) for host, prefix in _VIDS_APP)


async def detect(page: Any, settings: Settings, evidence_dir: Path | None = None,
                 account_wait_seconds: float = 12.0) -> SessionState:
    url = page.url or ""
    host = urlparse(url).hostname or ""
    text = await _snapshot_text(page)
    allowed = (settings.google_vids_allowed_account or "").lower()
    if is_vids_page(url, settings) and allowed:
        # The account indicator renders after the DOM is ready: give it a moment.
        deadline = time.monotonic() + account_wait_seconds
        while not _EMAIL.search(text) and time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            text = await _snapshot_text(page)
    evidence: list[EvidenceFile] = []
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        shot = evidence_dir / f"session_{int(time.time() * 1000)}.png"
        try:
            await page.screenshot(path=str(shot))
            evidence.append(EvidenceFile(path=shot, kind=ArtifactKind.SCREENSHOT,
                                         mime="image/png", label="session check"))
        except Exception as exc:  # a screenshot is evidence, not a precondition
            log.warning("session screenshot failed", error=type(exc).__name__)
    url = page.url or url
    host = urlparse(url).hostname or host
    where = safe_url(url)
    if _CHALLENGE.search(url) or _CHALLENGE.search(text[:5000]):
        return SessionState(state=SessionStateName.CHALLENGE, page=where, evidence=evidence,
                            message="Google is asking for verification. Complete it by hand "
                                    "in the Chrome window; the agent will not attempt it.")
    if host in _SIGNIN_HOSTS:
        return SessionState(state=SessionStateName.SIGNED_OUT, page=where, evidence=evidence,
                            message="Not signed in. Sign in by hand in the Chrome window.")
    if not is_vids_page(url, settings):
        return SessionState(state=SessionStateName.SIGNED_OUT, page=where, evidence=evidence,
                            message=f"Google Vids sent the browser to {host}; this usually "
                                    "means the profile is not signed in.")
    found = {m.lower() for m in _EMAIL.findall(text)}
    if allowed and allowed in found:
        return SessionState(state=SessionStateName.SIGNED_IN, authenticated=True,
                            account_confirmed=True, account_label=mask(allowed), page=where,
                            evidence=evidence, message=f"Signed in as {mask(allowed)}")
    if found:
        return SessionState(state=SessionStateName.WRONG_ACCOUNT, authenticated=True,
                            account_label=", ".join(sorted(mask(f) for f in found))[:120],
                            page=where, evidence=evidence,
                            message="Signed in, but not as the account automation may operate.")
    return SessionState(state=SessionStateName.ACCOUNT_UNCONFIRMED, authenticated=True,
                        page=where, evidence=evidence,
                        message="On Google Vids, but the signed-in account could not be read "
                                "from the page.")


async def open_login(manager: BrowserSessionManager) -> SessionState:
    """Show Google Vids (and so Google's sign-in page) in the dedicated profile."""
    async with manager.lock:
        page = await manager.open_home()
        return await detect(page, manager.settings)


async def wait_for_login(manager: BrowserSessionManager, *, timeout_seconds: float = 300,
                         poll_seconds: float = 3.0) -> SessionState:
    """Poll until the person has signed in (or the timeout passes). Reads only."""
    deadline = time.monotonic() + timeout_seconds
    state = SessionState(state=SessionStateName.NOT_RUNNING)
    while time.monotonic() < deadline:
        async with manager.lock:
            page = await manager.page()
            if is_vids_page(page.url or "", manager.settings):
                return await detect(page, manager.settings)
            state = await detect(page, manager.settings)
        if state.state is SessionStateName.CHALLENGE:
            return state
        await asyncio.sleep(poll_seconds)
    return state
