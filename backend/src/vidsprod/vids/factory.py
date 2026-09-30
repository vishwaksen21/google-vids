"""Builds the configured Vids adapter."""

from __future__ import annotations

from vidsprod.automation.google_vids import BrowserVidsAdapter, LocatorCatalogue
from vidsprod.config import Settings, VidsMode
from vidsprod.vids.capabilities import live_registry
from vidsprod.vids.mock import BUILTIN_SCENARIOS, MockScenario, MockVidsAdapter
from vidsprod.vids.port import GoogleVidsAdapter


def build_adapter(settings: Settings, scenario: MockScenario | None = None) -> GoogleVidsAdapter:
    if settings.google_vids_mode is not VidsMode.MOCK:
        recon = settings.locator_catalogue_path.parent / "capabilities.json"
        return BrowserVidsAdapter(
            settings, live_registry(recon), LocatorCatalogue.load(settings.locator_catalogue_path)
        )
    return MockVidsAdapter(
        settings.mock_vids_state_dir,
        scenario=scenario or BUILTIN_SCENARIOS[settings.mock_vids_scenario],
        logo_path=settings.logo_path,
    )
