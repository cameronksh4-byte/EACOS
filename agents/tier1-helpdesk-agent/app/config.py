"""Runtime configuration, loaded from environment variables.

One deployment serves one client (tenant). Every value can be set with an
environment variable of the same name in upper case, e.g. ``TENANT_ID``.
Secrets are typed as ``SecretStr`` so they never show up in logs or reprs.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Tenant -----------------------------------------------------------
    tenant_id: str = Field("", description="Client namespace this deployment serves")
    helpdesk_name: str = "IT Help Desk"
    tier2_contact_hint: str = Field(
        "a Tier 2 technician",
        description="How declines refer to the escalation team, e.g. 'the Infrastructure team'",
    )

    # --- Inbound auth -----------------------------------------------------
    webhook_secret: SecretStr = SecretStr("")

    # --- Claude -----------------------------------------------------------
    # Accepts either name: the Terraform task definition sets LLM_API_KEY.
    anthropic_api_key: SecretStr = Field(
        SecretStr(""), validation_alias=AliasChoices("ANTHROPIC_API_KEY", "LLM_API_KEY")
    )
    triage_model: str = "claude-haiku-5-5"
    escalation_model: str = "claude-sonnet-5-5"
    triage_effort: str = "medium"
    escalation_effort: str = "medium"
    # Server-side refusal fallback on the Sonnet route (Haiku has none).
    enable_refusal_fallbacks: bool = True
    llm_timeout_seconds: float = 60.0

    # --- Token caps -------------------------------------------------------
    # max_tokens includes thinking tokens, so leave headroom above the JSON.
    triage_max_tokens: int = 4096
    escalation_max_tokens: int = 8192
    max_subject_chars: int = 300
    max_body_chars: int = 6000
    max_sop_chars_per_chunk: int = 2500

    # --- Budget -----------------------------------------------------------
    monthly_token_budget_usd: float = 25.0
    # USD per million tokens: (input, output). Cache reads bill at 0.1x input,
    # cache writes at 1.25x input.
    model_prices_per_mtok: dict[str, tuple[float, float]] = {
        "claude-haiku-5-5": (0.10, 0.50),
        "claude-sonnet-5-5": (2.00, 10.00),
        "claude-sonnet-5": (2.00, 10.00),
    }

    # --- Triage policy ----------------------------------------------------
    confidence_threshold: float = 0.80

    # --- Supabase / RAG ---------------------------------------------------
    supabase_url: str = ""
    supabase_service_key: SecretStr = SecretStr("")
    rag_top_k: int = 4
    rag_min_similarity: float = 0.55

    voyage_api_key: SecretStr = SecretStr("")
    embedding_model: str = "voyage-3.5"
    embedding_dimensions: int = 1024

    # --- Slack ------------------------------------------------------------
    slack_bot_token: SecretStr = SecretStr("")
    slack_signing_secret: SecretStr = SecretStr("")
    slack_escalation_channel: str = ""

    # --- Microsoft Teams (outgoing webhook) -------------------------------
    teams_outgoing_webhook_secret: SecretStr = SecretStr("")
    teams_incoming_webhook_url: SecretStr = SecretStr("")

    # --- Jira -------------------------------------------------------------
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: SecretStr = SecretStr("")
    jira_webhook_secret: SecretStr = SecretStr("")
    jira_escalation_label: str = "tier2-escalation"

    def missing_core_settings(self) -> list[str]:
        """Settings the API cannot run without."""
        required = {
            "TENANT_ID": self.tenant_id,
            "WEBHOOK_SECRET": self.webhook_secret.get_secret_value(),
            "ANTHROPIC_API_KEY / LLM_API_KEY": self.anthropic_api_key.get_secret_value(),
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_SERVICE_KEY": self.supabase_service_key.get_secret_value(),
            "VOYAGE_API_KEY": self.voyage_api_key.get_secret_value(),
        }
        return [name for name, value in required.items() if not value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
