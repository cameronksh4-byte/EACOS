"""Runtime configuration and deterministic service-tier feature flags.

One codebase serves every commercial tier. `SERVICE_TIER` picks a tier, and
each feature is enabled only if the tier is at or above that feature's minimum
in `FEATURE_MIN_TIER`. `DISABLED_FEATURES` can switch individual features off
(a kill switch), but nothing can switch on a feature above the tier. Upgrading
a client means changing one environment variable.

All values come from environment variables of the same name in upper case.
Secrets are `SecretStr`, so they never show up in logs or reprs.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from functools import lru_cache
from typing import Annotated, Any

from pydantic import AliasChoices, Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# ---------------------------------------------------------------------------
# Service tiers and feature flags
# ---------------------------------------------------------------------------


class ServiceTier(str, Enum):
    TIER_0_INCENTIVE = "TIER_0_INCENTIVE"  # free 30-day lead magnet
    TIER_1_PRODUCTION = "TIER_1_PRODUCTION"  # paid implementation
    TIER_2_MANAGED_OPS = "TIER_2_MANAGED_OPS"  # monthly retainer

    @property
    def rank(self) -> int:
        return _TIER_RANK[self]

    def at_least(self, other: "ServiceTier") -> bool:
        return self.rank >= other.rank


_TIER_RANK = {
    ServiceTier.TIER_0_INCENTIVE: 0,
    ServiceTier.TIER_1_PRODUCTION: 1,
    ServiceTier.TIER_2_MANAGED_OPS: 2,
}


class Feature(str, Enum):
    # Tier 0
    TRIAGE_AND_DRAFT = "triage_and_draft"
    SCREENSHOT_VISION = "screenshot_vision"
    OUTAGE_DETECTION = "outage_detection"
    ROI_TELEMETRY = "roi_telemetry"
    # Tier 1
    CONTEXT_ENRICHMENT = "context_enrichment"
    MULTI_TURN = "multi_turn"
    MANAGER_APPROVALS = "manager_approvals"
    # Tier 2
    KNOWLEDGE_FLYWHEEL = "knowledge_flywheel"
    EXECUTIVE_REPORTS = "executive_reports"


FEATURE_MIN_TIER: dict[Feature, ServiceTier] = {
    Feature.TRIAGE_AND_DRAFT: ServiceTier.TIER_0_INCENTIVE,
    Feature.SCREENSHOT_VISION: ServiceTier.TIER_0_INCENTIVE,
    Feature.OUTAGE_DETECTION: ServiceTier.TIER_0_INCENTIVE,
    Feature.ROI_TELEMETRY: ServiceTier.TIER_0_INCENTIVE,
    Feature.CONTEXT_ENRICHMENT: ServiceTier.TIER_1_PRODUCTION,
    Feature.MULTI_TURN: ServiceTier.TIER_1_PRODUCTION,
    Feature.MANAGER_APPROVALS: ServiceTier.TIER_1_PRODUCTION,
    Feature.KNOWLEDGE_FLYWHEEL: ServiceTier.TIER_2_MANAGED_OPS,
    Feature.EXECUTIVE_REPORTS: ServiceTier.TIER_2_MANAGED_OPS,
}
assert set(FEATURE_MIN_TIER) == set(Feature), "every Feature needs a minimum tier"


class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"  # Claude API (supports server-side refusal fallbacks)
    BEDROCK = "bedrock"  # Amazon Bedrock via the Mantle client (no server-side fallbacks)


class IdentityProviderKind(str, Enum):
    DIRECTORY_CACHE = "directory_cache"  # Supabase tenant_directory table
    MICROSOFT_GRAPH = "microsoft_graph"  # Entra ID + Intune
    OKTA = "okta"


def _split_csv(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", validate_default=True)

    # --- Commercial tier ------------------------------------------------------
    service_tier: ServiceTier = ServiceTier.TIER_0_INCENTIVE
    disabled_features: Annotated[frozenset[Feature], NoDecode] = frozenset()
    # Tier 0 trials stop calling the model after this date (tickets go to humans).
    trial_expires_on: date | None = None

    # --- Tenancy --------------------------------------------------------------
    # Default tenant for chat integrations (Slack/Teams/Jira).
    tenant_id: str = Field("", pattern=r"^$|^[a-z0-9-]{2,64}$")
    # Every tenant this deployment may serve. Defaults to [tenant_id].
    allowed_tenant_ids: Annotated[list[str], NoDecode] = []
    helpdesk_name: str = "IT Help Desk"
    tier2_contact_hint: str = "a Tier 2 technician"

    # --- Inbound auth ---------------------------------------------------------
    webhook_secret: SecretStr = SecretStr("")

    # --- LLM provider & models ------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.ANTHROPIC
    anthropic_api_key: SecretStr = Field(
        SecretStr(""), validation_alias=AliasChoices("ANTHROPIC_API_KEY", "LLM_API_KEY")
    )
    aws_region: str = "us-east-1"
    bedrock_model_prefix: str = "anthropic."
    # Claude Haiku 3.5 was retired on 2026-02-19. Haiku 5.5 is its vision-capable successor.
    triage_model: str = "claude-haiku-5-5"
    vision_model: str = "claude-haiku-5-5"
    reasoning_model: str = Field(
        "claude-sonnet-5-5", validation_alias=AliasChoices("REASONING_MODEL", "ESCALATION_MODEL")
    )
    flywheel_model: str = "claude-sonnet-5-5"
    triage_effort: str = Field("medium", pattern="^(low|medium|high|xhigh|max)$")
    vision_effort: str = Field("low", pattern="^(low|medium|high|xhigh|max)$")
    reasoning_effort: str = Field(
        "medium",
        pattern="^(low|medium|high|xhigh|max)$",
        validation_alias=AliasChoices("REASONING_EFFORT", "ESCALATION_EFFORT"),
    )
    flywheel_effort: str = Field("medium", pattern="^(low|medium|high|xhigh|max)$")
    # Server-side refusal fallback on the Sonnet route (Claude API only).
    enable_refusal_fallbacks: bool = True
    llm_timeout_seconds: float = Field(60.0, gt=0, le=600)

    # --- Token caps (max_tokens includes thinking tokens) ---------------------
    triage_max_tokens: int = Field(4096, ge=1024, le=32000)
    vision_max_tokens: int = Field(2048, ge=512, le=16000)
    reasoning_max_tokens: int = Field(
        8192, ge=1024, le=32000, validation_alias=AliasChoices("REASONING_MAX_TOKENS", "ESCALATION_MAX_TOKENS")
    )
    flywheel_max_tokens: int = Field(8192, ge=1024, le=32000)
    max_subject_chars: int = Field(300, ge=50, le=2000)
    max_body_chars: int = Field(6000, ge=500, le=50000)
    max_sop_chars_per_chunk: int = Field(2500, ge=200, le=10000)

    # --- Budget (hard per-tenant monthly cap) ---------------------------------
    monthly_token_budget_usd: float = Field(25.0, ge=0)
    tenant_budget_overrides_usd: dict[str, float] = {}
    # USD per million tokens: (input, output). Cache reads bill at 0.1x input,
    # cache writes at 1.25x.
    model_prices_per_mtok: dict[str, tuple[float, float]] = {
        "claude-haiku-5-5": (0.10, 0.50),
        "claude-sonnet-5-5": (2.00, 10.00),
        "claude-sonnet-5": (2.00, 10.00),
    }
    # Input tokens assumed per image when reserving budget before a vision call.
    reserve_tokens_per_image: int = Field(1600, ge=100)

    # --- Triage policy --------------------------------------------------------
    confidence_threshold: float = Field(0.80, ge=0.80, le=0.99)  # hard floor: guardrail #2

    # --- Supabase / RAG -------------------------------------------------------
    supabase_url: str = ""
    supabase_service_key: SecretStr = SecretStr("")
    rag_top_k: int = Field(4, ge=1, le=20)
    rag_min_similarity: float = Field(0.55, ge=0.0, le=1.0)
    voyage_api_key: SecretStr = SecretStr("")
    embedding_model: str = "voyage-3.5"
    embedding_dimensions: int = Field(1024, description="Must match vector(N) in the migration")

    # --- Tier 0: screenshot vision --------------------------------------------
    max_attachments: int = Field(4, ge=0, le=10)
    max_image_bytes: int = Field(5 * 1024 * 1024, ge=1024, le=5 * 1024 * 1024)
    # SSRF guard: images are only fetched from these hosts (exact or subdomain).
    attachment_allowed_hosts: Annotated[list[str], NoDecode] = [
        "files.slack.com",
        "atlassian.net",
        "graph.microsoft.com",
        "zendesk.com",
    ]
    attachment_fetch_timeout_seconds: float = Field(10.0, gt=0, le=60)

    # --- Tier 0: outage burst detection ---------------------------------------
    outage_window_minutes: int = Field(30, ge=5, le=240)
    outage_min_matching_tickets: int = Field(5, ge=2, le=100)
    outage_similarity_threshold: float = Field(0.88, ge=0.5, le=0.999)

    # --- Tier 0: ROI telemetry ------------------------------------------------
    minutes_saved_per_deflection: int = Field(15, ge=0, le=480)

    # --- Tier 1: identity / MDM context ---------------------------------------
    identity_provider: IdentityProviderKind = IdentityProviderKind.DIRECTORY_CACHE
    directory_cache_ttl_hours: int = Field(24, ge=1, le=720)
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: SecretStr = SecretStr("")
    okta_domain: str = ""
    okta_api_token: SecretStr = SecretStr("")

    # --- Tier 1: multi-turn conversation --------------------------------------
    max_clarifying_questions: int = Field(2, ge=0, le=2)  # the DB enforces <= 2 as well
    thread_ttl_hours: int = Field(72, ge=1, le=720)
    max_history_messages: int = Field(12, ge=2, le=50)

    # --- Tier 1: n8n approvals ------------------------------------------------
    n8n_approval_webhook_url: HttpUrl | None = None
    n8n_outbound_signing_secret: SecretStr = SecretStr("")  # we sign requests to n8n
    n8n_callback_secret: SecretStr = SecretStr("")  # n8n signs callbacks to us
    approval_ttl_hours: int = Field(72, ge=1, le=720)
    webhook_signature_max_skew_seconds: int = Field(300, ge=30, le=900)

    # --- Tier 2: flywheel & reports -------------------------------------------
    n8n_notify_webhook_url: HttpUrl | None = None  # SOP-draft review + weekly report delivery
    flywheel_gap_similarity_threshold: float = Field(0.82, ge=0.5, le=0.99)
    flywheel_min_resolution_chars: int = Field(80, ge=20)

    # --- Chat integrations (unchanged) ----------------------------------------
    slack_bot_token: SecretStr = SecretStr("")
    slack_signing_secret: SecretStr = SecretStr("")
    slack_escalation_channel: str = ""
    teams_outgoing_webhook_secret: SecretStr = SecretStr("")
    teams_incoming_webhook_url: SecretStr = SecretStr("")
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: SecretStr = SecretStr("")
    jira_webhook_secret: SecretStr = SecretStr("")
    jira_escalation_label: str = "tier2-escalation"

    # --- Validators -----------------------------------------------------------

    @field_validator("allowed_tenant_ids", "attachment_allowed_hosts", mode="before")
    @classmethod
    def _csv_list(cls, value: Any) -> Any:
        return _split_csv(value)

    @field_validator("disabled_features", mode="before")
    @classmethod
    def _csv_features(cls, value: Any) -> Any:
        return frozenset(_split_csv(value) or [])

    @field_validator("attachment_allowed_hosts")
    @classmethod
    def _normalise_hosts(cls, value: list[str]) -> list[str]:
        return [h.lower().lstrip(".") for h in value]

    @model_validator(mode="after")
    def _default_tenants(self) -> "Settings":
        if not self.allowed_tenant_ids and self.tenant_id:
            self.allowed_tenant_ids = [self.tenant_id]
        elif self.tenant_id and self.tenant_id not in self.allowed_tenant_ids:
            self.allowed_tenant_ids = [self.tenant_id, *self.allowed_tenant_ids]
        return self

    # --- Feature gating -------------------------------------------------------

    def feature_enabled(self, feature: Feature) -> bool:
        """Deterministic: tier >= feature minimum, and not kill-switched."""
        return self.service_tier.at_least(FEATURE_MIN_TIER[feature]) and feature not in self.disabled_features

    def enabled_features(self) -> list[Feature]:
        return [f for f in Feature if self.feature_enabled(f)]

    def trial_expired(self, today: date | None = None) -> bool:
        if self.service_tier is not ServiceTier.TIER_0_INCENTIVE or self.trial_expires_on is None:
            return False
        return (today or datetime.now(timezone.utc).date()) > self.trial_expires_on

    # --- Tenancy & budget -----------------------------------------------------

    def is_tenant_allowed(self, tenant_id: str) -> bool:
        return tenant_id in self.allowed_tenant_ids

    def budget_for(self, tenant_id: str) -> float:
        return self.tenant_budget_overrides_usd.get(tenant_id, self.monthly_token_budget_usd)

    def provider_model_id(self, model: str) -> str:
        """The model ID as the configured provider expects it."""
        if self.llm_provider is LLMProvider.BEDROCK and not model.startswith(self.bedrock_model_prefix):
            return f"{self.bedrock_model_prefix}{model}"
        return model

    def price_for(self, model: str) -> tuple[float, float]:
        base = model.removeprefix(self.bedrock_model_prefix)
        # Unknown models (e.g. a fallback) are priced like the costliest known one.
        return self.model_prices_per_mtok.get(base) or max(self.model_prices_per_mtok.values())

    # --- Startup validation ---------------------------------------------------

    def tier2_channel_configured(self) -> bool:
        n8n = bool(self.n8n_notify_webhook_url) and bool(self.n8n_outbound_signing_secret.get_secret_value())
        slack = bool(self.slack_escalation_channel) and bool(self.slack_bot_token.get_secret_value())
        return n8n or slack

    def missing_core_settings(self) -> list[str]:
        """Settings the API can't run without at the configured tier and provider."""
        required: dict[str, Any] = {
            "TENANT_ID or ALLOWED_TENANT_IDS": self.allowed_tenant_ids,
            "WEBHOOK_SECRET": self.webhook_secret.get_secret_value(),
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_SERVICE_KEY": self.supabase_service_key.get_secret_value(),
            "VOYAGE_API_KEY": self.voyage_api_key.get_secret_value(),
        }
        if self.llm_provider is LLMProvider.ANTHROPIC:
            required["ANTHROPIC_API_KEY / LLM_API_KEY"] = self.anthropic_api_key.get_secret_value()

        if self.feature_enabled(Feature.OUTAGE_DETECTION) and not self.tier2_channel_configured():
            # Outage broadcasts skip per-ticket escalation only because the parent
            # incident is sent to Tier 2, so a delivery channel is mandatory.
            required["N8N_NOTIFY_WEBHOOK_URL+N8N_OUTBOUND_SIGNING_SECRET or SLACK_BOT_TOKEN+SLACK_ESCALATION_CHANNEL"] = None

        if self.feature_enabled(Feature.CONTEXT_ENRICHMENT):
            if self.identity_provider is IdentityProviderKind.MICROSOFT_GRAPH:
                required["GRAPH_TENANT_ID"] = self.graph_tenant_id
                required["GRAPH_CLIENT_ID"] = self.graph_client_id
                required["GRAPH_CLIENT_SECRET"] = self.graph_client_secret.get_secret_value()
            elif self.identity_provider is IdentityProviderKind.OKTA:
                required["OKTA_DOMAIN"] = self.okta_domain
                required["OKTA_API_TOKEN"] = self.okta_api_token.get_secret_value()

        if self.feature_enabled(Feature.MANAGER_APPROVALS):
            required["N8N_APPROVAL_WEBHOOK_URL"] = self.n8n_approval_webhook_url
            required["N8N_OUTBOUND_SIGNING_SECRET"] = self.n8n_outbound_signing_secret.get_secret_value()
            required["N8N_CALLBACK_SECRET"] = self.n8n_callback_secret.get_secret_value()

        if self.feature_enabled(Feature.KNOWLEDGE_FLYWHEEL) or self.feature_enabled(Feature.EXECUTIVE_REPORTS):
            required["N8N_NOTIFY_WEBHOOK_URL"] = self.n8n_notify_webhook_url
            required["N8N_OUTBOUND_SIGNING_SECRET"] = self.n8n_outbound_signing_secret.get_secret_value()

        return [name for name, value in required.items() if not value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
