"""The browser session manager: one dedicated Chrome profile, one serialised session.

* The profile lives in BROWSER_PROFILE_DIR (owner-only permissions). Cookies and local
  state persist there between runs, so a person signs in once and the agent reuses it.
* Every browser operation takes `lock`: two actions never interleave on one Vids editor.
* The browser is launched lazily and relaunched if it crashed or was closed (recovery).
* Nothing here reads cookies, tokens or passwords, and URLs are logged without their
  query strings.
"""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from vidsprod.config import Settings
from vidsprod.errors import BrowserDisconnectedError, LiveAutomationDisabledError
from vidsprod.logging import get_logger

log = get_logger(__name__)

Launcher = Callable[[Path], Awaitable[Any]]
KEYCHAIN_DEFAULT_ARGS = ("--use-mock-keychain", "--password-store=basic")  # returns a Playwright BrowserContext


def safe_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlparse(url)
    if not parts.netloc:  # about:blank, data:, chrome:
        return f"{parts.scheme}:{parts.path}"
    return f"{parts.scheme}://{parts.hostname}{parts.path}"


class BrowserSessionManager:
    def __init__(self, settings: Settings, launcher: Launcher | None = None) -> None:
        self.settings = settings
        self._launcher = launcher
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self.lock = asyncio.Lock()
        self.launched_at: datetime | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------ lifecycle
    def _require_enabled(self) -> None:
        if not self.settings.browser_enabled:
            raise LiveAutomationDisabledError(
                "The browser is not enabled: GOOGLE_VIDS_MODE is 'mock'. Use 'browser_test' "
                "to launch Chrome read-only, or 'live' (with authorisation) to operate Vids."
            )

    def _profile_dir(self) -> Path:
        profile = self.settings.browser_profile_dir
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        if stat.S_IMODE(os.stat(profile).st_mode) & 0o077:
            os.chmod(profile, 0o700)  # the profile holds the Google session: owner only
        return profile

    @property
    def running(self) -> bool:
        try:
            return self._page is not None and not self._page.is_closed()
        except Exception:
            return False

    async def launch(self) -> Any:
        """Start (or reuse) the persistent Chrome session and return its page."""
        self._require_enabled()
        if self.running:
            return self._page
        await self._teardown()
        profile = self._profile_dir()
        try:
            if self._launcher is not None:
                self._context = await self._launcher(profile)
            else:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    channel=self.settings.browser_channel or None,
                    headless=self.settings.browser_headless,
                    accept_downloads=True,
                    viewport={"width": 1440, "height": 900},
                    # Use Chrome's normal keychain, as a regular Chrome does. Playwright's
                    # default mock keychain cannot decrypt cookies saved by an ordinary Chrome
                    # on this profile, so a person's manual sign-in would look signed out.
                    # Only keychain flags are changed; automation stays visible to Google.
                    ignore_default_args=list(KEYCHAIN_DEFAULT_ARGS),
                )
            self._context.on("close", lambda *_: log.info("browser context closed"))
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            self.launched_at = datetime.now(UTC)
            self.last_error = None
            log.info("browser launched", channel=self.settings.browser_channel,
                     headless=self.settings.browser_headless)
            return self._page
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            await self._teardown()
            raise BrowserDisconnectedError(f"could not start Chrome: {self.last_error}") from exc

    async def page(self) -> Any:
        """The live page, relaunching the browser if it crashed or was closed."""
        if not self.running:
            log.info("browser not running; launching")
            return await self.launch()
        return self._page

    async def open_home(self) -> Any:
        page = await self.page()
        try:
            await page.goto(self.settings.google_vids_url, wait_until="domcontentloaded",
                            timeout=self.settings.browser_action_timeout_ms * 2)
        except Exception as exc:
            raise BrowserDisconnectedError(f"could not open Google Vids: {type(exc).__name__}") from exc
        return page

    async def close(self) -> None:
        async with self.lock:
            await self._teardown()

    async def _teardown(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:  # already gone: closing is best effort, and logged
            log.info("browser context was already closed")
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            log.info("playwright was already stopped")
        self._context = self._page = self._playwright = None

    def status(self) -> dict[str, Any]:
        url = None
        if self.running:
            try:
                url = safe_url(self._page.url)
            except Exception:
                url = None
        return {
            "enabled": self.settings.browser_enabled,
            "mode": self.settings.google_vids_mode.value,
            "running": self.running,
            "page": url,
            "launched_at": self.launched_at.isoformat() if self.launched_at else None,
            "headless": self.settings.browser_headless,
            "channel": self.settings.browser_channel,
            "busy": self.lock.locked(),
            "last_error": self.last_error,
        }
