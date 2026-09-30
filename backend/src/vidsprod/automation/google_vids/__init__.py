"""Google Vids browser automation: session manager, sign-in detection, locator catalogue,
diagnostics, recovery mapping and the adapter."""

from .adapter import BrowserVidsAdapter
from .browser_manager import BrowserSessionManager
from .recovery import GoogleChallengeError, raise_if_wrong
from .selectors import REQUIRED_LOCATORS, LocatorCatalogue, LocatorSpec, PageDriver

__all__ = ["REQUIRED_LOCATORS", "BrowserSessionManager", "BrowserVidsAdapter",
           "GoogleChallengeError", "LocatorCatalogue", "LocatorSpec", "PageDriver",
           "raise_if_wrong"]
