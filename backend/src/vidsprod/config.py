"""Application settings, read from the environment (and `backend/.env` in development).

Every tunable that shapes production — retry limits, budgets, speech rate, clip limits,
model identifiers — lives here rather than in the code that uses it. Validation at
startup refuses combinations that would be unsafe, such as a simulated Vids adapter in
production or direct Gemini access switched on without being explicitly enabled.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent
REPO_DIR = PROJECT_DIR.parent


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AIProviderName(StrEnum):
    GATEWAY = "gateway"
    GEMINI = "gemini"
    FAKE = "fake"


class VidsMode(StrEnum):
    """MOCK: simulated Vids. BROWSER_TEST: real Chrome and Vids, read-only (launch, sign-in
    detection, diagnostics); nothing is created or generated. LIVE: real project creation,
    generation, editing and export, and only with GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED=true."""

    MOCK = "mock"
    BROWSER_TEST = "browser_test"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ application
    app_env: AppEnv = AppEnv.DEVELOPMENT
    log_level: str = "INFO"
    database_url: SecretStr = SecretStr("postgresql+asyncpg://vidsprod@localhost:5432/vids")
    test_database_url: SecretStr | None = None  # used only by the test suite
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_statement_timeout_ms: int = Field(default=30_000, ge=1000)
    api_token: SecretStr | None = None
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    artifact_dir: Path = PROJECT_DIR / "var" / "artifacts"
    design_md_path: Path = REPO_DIR / "DESIGN.md"
    logo_path: Path = REPO_DIR / "logo.png"
    max_request_bytes: int = Field(default=1_000_000, ge=10_000)

    # ------------------------------------------------------------------ worker
    worker_embedded: bool = True
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    worker_lease_seconds: int = Field(default=60, ge=10)

    # ------------------------------------------------------------------ AI provider
    ai_provider: AIProviderName = AIProviderName.GATEWAY
    ai_timeout_seconds: float = Field(default=120.0, gt=0)
    ai_max_attempts: int = Field(default=3, ge=1, le=10)
    ai_backoff_base_seconds: float = Field(default=2.0, ge=0)
    ai_structured_repair_attempts: int = Field(default=1, ge=0, le=3)
    ai_model_creative: str = "gemini-3.1-pro-preview"
    ai_model_reasoning: str = "gemini-3.1-pro-preview"
    ai_model_vision: str = "gemini-3.8-flash"
    ai_model_transcribe: str = "gemini-3.8-flash"
    # Cost per 1M tokens, if known, for the request log. Unknown stays unknown.
    ai_cost_per_mtok_input: float | None = None
    ai_cost_per_mtok_output: float | None = None

    # Company gateway (default provider).
    gateway_base_url: str | None = None
    gateway_api_key: SecretStr | None = None
    gateway_text_path: str = "/llm/generate"
    gateway_models_path: str = "/llm/models"
    gateway_location: str = "global"
    gateway_thinking_budget: int = Field(default=1024, ge=0)
    gateway_structured_mode: str = Field(default="schema", pattern="^(schema|prompt)$")

    # Direct Gemini: off until data handling is approved.
    gemini_direct_enabled: bool = False
    gemini_api_key: SecretStr | None = None
    gemini_store_interactions: bool = False

    # ------------------------------------------------------------------ Google Vids
    google_vids_mode: VidsMode = VidsMode.MOCK
    google_vids_live_automation_enabled: bool = False
    google_vids_allowed_account: str | None = None
    google_vids_url: str = "https://vids.google.com/"
    google_vids_project_prefix: str = "[vidsprod]"
    browser_profile_dir: Path = PROJECT_DIR / "var" / "browser-profile"
    browser_channel: str = "chrome"
    browser_headless: bool = False
    browser_action_timeout_ms: int = Field(default=15_000, ge=1000)
    browser_generation_timeout_seconds: int = Field(default=600, ge=30)
    browser_min_action_interval_ms: int = Field(default=800, ge=0)
    locator_catalogue_path: Path = PROJECT_DIR / "recon" / "locators.json"
    mock_vids_state_dir: Path = PROJECT_DIR / "var" / "mock-vids"
    mock_vids_scenario: str = Field(default="realistic", pattern="^(clean|realistic|stress)$")

    # ------------------------------------------------------------------ planning
    speech_words_per_second: float = Field(default=2.5, gt=0.5, le=5)
    speech_dialogue_share: float = Field(default=0.8, gt=0.1, le=1)
    speech_min_fill_share: float = Field(default=0.75, gt=0, le=1)
    video_min_seconds: float = Field(default=6.0, gt=0)
    video_max_seconds: float = Field(default=90.0, gt=0)
    video_pacing_padding_seconds: float = Field(default=2.0, ge=0)
    reading_words_per_second: float = Field(default=3.0, gt=0.5)
    min_super_seconds: float = Field(default=2.0, gt=0)

    # ------------------------------------------------------------------ production
    max_takes_per_scene: int = Field(default=3, ge=1, le=10)
    max_edits_per_scene: int = Field(default=2, ge=0, le=10)
    generation_monthly_limit: int = Field(default=50, ge=1)
    generation_campaign_limit: int = Field(default=20, ge=1)
    stage_max_attempts: int = Field(default=3, ge=1, le=10)
    stage_backoff_base_seconds: float = Field(default=5.0, ge=0)
    stage_backoff_max_seconds: float = Field(default=300.0, ge=0)
    duration_tolerance_seconds: float = Field(default=0.6, ge=0)
    export_timeout_seconds: int = Field(default=900, ge=30)
    export_min_bytes: int = Field(default=10_000, ge=1)
    export_max_bytes: int = Field(default=2_000_000_000, ge=1)

    # ------------------------------------------------------------------ approvals
    approval_plan_required: bool = True
    approval_compliance_required: bool = True
    delivery_submission_target: str = Field(default="none", pattern="^(none|local)$")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def _refuse_unsafe_combinations(self) -> Settings:
        if self.video_min_seconds >= self.video_max_seconds:
            raise ValueError("VIDEO_MIN_SECONDS must be below VIDEO_MAX_SECONDS")
        if self.ai_provider is AIProviderName.GEMINI and not self.gemini_direct_enabled:
            raise ValueError(
                "AI_PROVIDER=gemini requires GEMINI_DIRECT_ENABLED=true, which must only be "
                "set once direct data handling has been approved"
            )
        if self.app_env is AppEnv.PRODUCTION:
            if self.ai_provider is AIProviderName.FAKE:
                raise ValueError("AI_PROVIDER=fake is not allowed in production")
            if self.google_vids_mode is VidsMode.MOCK:
                raise ValueError("GOOGLE_VIDS_MODE=mock is not allowed in production")
            if self.api_token is None:
                raise ValueError("API_TOKEN is required in production")
        if self.browser_enabled and not self.google_vids_allowed_account:
            raise ValueError(
                "GOOGLE_VIDS_MODE=browser_test/live requires GOOGLE_VIDS_ALLOWED_ACCOUNT "
                "(the one account automation may operate)"
            )
        if self.google_vids_live_automation_enabled and not self.google_vids_allowed_account:
            raise ValueError(
                "GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED=true requires "
                "GOOGLE_VIDS_ALLOWED_ACCOUNT (the one account automation may operate)"
            )
        return self

    @property
    def simulation(self) -> bool:
        """True when any part of production is simulated rather than real."""
        return (
            self.ai_provider is AIProviderName.FAKE
            or self.google_vids_mode is VidsMode.MOCK
        )

    @property
    def live_actions_allowed(self) -> bool:
        """Both switches: the mode says live, and the authorisation flag is on."""
        return (self.google_vids_mode is VidsMode.LIVE
                and self.google_vids_live_automation_enabled)

    @property
    def browser_enabled(self) -> bool:
        return self.google_vids_mode in (VidsMode.BROWSER_TEST, VidsMode.LIVE)

    def public_view(self) -> dict:
        """Effective configuration safe to show in the dashboard: no secrets, no URLs
        with credentials, no filesystem paths beyond their names."""
        return {
            "app_env": self.app_env.value,
            "ai_provider": self.ai_provider.value,
            "ai_models": {
                "creative": self.ai_model_creative,
                "reasoning": self.ai_model_reasoning,
                "vision": self.ai_model_vision,
                "transcribe": self.ai_model_transcribe,
            },
            "gateway_configured": bool(self.gateway_base_url and self.gateway_api_key),
            "gemini_direct_enabled": self.gemini_direct_enabled,
            "google_vids_mode": self.google_vids_mode.value,
            "live_actions_allowed": self.live_actions_allowed,
            "google_vids_live_automation_enabled": self.google_vids_live_automation_enabled,
            "google_vids_allowed_account_set": bool(self.google_vids_allowed_account),
            "simulation": self.simulation,
            "planning": {
                "speech_words_per_second": self.speech_words_per_second,
                "speech_dialogue_share": self.speech_dialogue_share,
                "video_min_seconds": self.video_min_seconds,
                "video_max_seconds": self.video_max_seconds,
            },
            "production": {
                "max_takes_per_scene": self.max_takes_per_scene,
                "max_edits_per_scene": self.max_edits_per_scene,
                "generation_monthly_limit": self.generation_monthly_limit,
                "generation_campaign_limit": self.generation_campaign_limit,
                "stage_max_attempts": self.stage_max_attempts,
            },
            "approvals": {
                "plan_required": self.approval_plan_required,
                "compliance_required": self.approval_compliance_required,
                "delivery_submission_target": self.delivery_submission_target,
            },
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
