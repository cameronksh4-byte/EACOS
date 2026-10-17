"""Runtime configuration.

Every setting is read from the environment (prefix ``TRIAGE_``) or from an
optional ``.env`` file. Secrets are typed as :class:`pydantic.SecretStr` so
they never appear in ``repr()`` output, tracebacks or log lines.

In production, inject secrets from AWS Secrets Manager or SSM Parameter Store
into the task environment; never bake them into images.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: 19 CFR 163.4(a): entry records must be kept for 5 years from the date of
#: entry. 1,827 days covers five years including leap days.
MIN_CBP_RETENTION_DAYS = 1827
#: Records are locked at triage time, which precedes the entry date the
#: 163.4 clock starts from; a 90-day margin covers the gap.
DEFAULT_RETENTION_DAYS = MIN_CBP_RETENTION_DAYS + 90

TEN_MIB = 10 * 1024 * 1024


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_prefix="TRIAGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["production", "staging", "development", "test"] = "production"
    aws_region: str = "us-east-1"
    log_level: str = "INFO"
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    # --- Ingress limits -------------------------------------------------
    max_request_body_bytes: int = Field(default=TEN_MIB, gt=0, le=TEN_MIB)

    # --- WhatsApp Cloud API --------------------------------------------
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_verify_token: SecretStr = SecretStr("")
    whatsapp_access_token: SecretStr = SecretStr("")
    whatsapp_graph_api_version: str = "v21.0"
    whatsapp_media_host_allowlist: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["lookaside.fbsbx.com"]
    )

    # --- Inbound e-mail relay ------------------------------------------
    #: Shared secret used by the MTA relay (SES/Postfix/etc.) to sign each
    #: POST. Envelope data (client IP, MAIL FROM, HELO) feeds SPF, so it must
    #: be authenticated or an attacker could forge a passing SPF result.
    email_relay_secret: SecretStr = SecretStr("")
    email_require_spf_pass: bool = True
    email_require_aligned_dkim: bool = True
    email_dns_timeout_seconds: float = Field(default=5.0, gt=0, le=30)

    # --- PII tokenisation ----------------------------------------------
    #: HMAC key for PII tokens. Plain SHA-256 of an RFC/CURP is trivially
    #: brute-forced (the ID space is small), so a secret key is mandatory.
    pii_hmac_key: SecretStr = SecretStr("")

    # --- AWS Bedrock (Claude) ------------------------------------------
    #: Bedrock model ID. Claude 3.5 Sonnet was retired on 2025-10-28, so the
    #: default is its current successor in the Sonnet line.
    bedrock_model_id: str = "anthropic.claude-sonnet-5-5"
    #: Optional VPC interface endpoint (PrivateLink) for the Bedrock Mantle
    #: API, e.g. ``https://vpce-...bedrock-mantle.us-east-1.vpce.amazonaws.com/anthropic``.
    bedrock_endpoint_url: str | None = None
    bedrock_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    bedrock_max_tokens: int = Field(default=16000, gt=0, le=64000)
    bedrock_timeout_seconds: float = Field(default=120.0, gt=0)
    extraction_max_retries: int = Field(default=2, ge=0, le=5)

    # --- Supabase Postgres ---------------------------------------------
    database_url: SecretStr = SecretStr("")
    database_ssl_root_cert: str | None = None
    database_pool_min_size: int = Field(default=1, ge=0)
    database_pool_max_size: int = Field(default=10, ge=1)
    #: Set True when connecting through Supavisor in *transaction* mode,
    #: which cannot hold prepared statements across transactions.
    database_uses_transaction_pooler: bool = False

    # --- Immutable audit trail -----------------------------------------
    audit_bucket: str = ""
    audit_kms_key_id: str | None = None
    audit_retention_days: int = Field(default=DEFAULT_RETENTION_DAYS)
    audit_key_prefix: str = "audit/v1"

    # --- Broker sign-off (Supabase Auth) ------------------------------
    #: ``https://<project-ref>.supabase.co/auth/v1``
    supabase_jwt_issuer: str = ""
    #: ``https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json``
    supabase_jwks_url: str | None = None
    #: Legacy shared JWT secret, only for projects without signing keys.
    supabase_jwt_secret: SecretStr = SecretStr("")
    #: Approvals require a multi-factor session (Supabase ``aal2``).
    broker_approval_requires_mfa: bool = True

    # --- Triage rules --------------------------------------------------
    weight_variance_threshold_pct: Decimal = Decimal("3.0")

    #: Fail startup if Object Lock or Bedrock logging is misconfigured.
    enforce_startup_compliance_checks: bool = True

    @field_validator("allowed_hosts", "whatsapp_media_host_allowlist", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept comma-separated strings for list settings."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("aws_region")
    @classmethod
    def _us_region_only(cls, value: str) -> str:
        """Keep compute, inference and audit storage inside the United States."""
        if not value.startswith("us-") or value.startswith("us-gov-"):
            raise ValueError("aws_region must be a commercial US region (e.g. us-east-1)")
        return value

    @field_validator("bedrock_model_id")
    @classmethod
    def _no_global_inference_profile(cls, value: str) -> str:
        """Reject cross-region profiles that may route inference outside the US."""
        if value.startswith(("global.", "eu.", "apac.", "jp.", "au.")):
            raise ValueError("bedrock_model_id must be an in-region or us. model ID")
        return value

    @field_validator("audit_retention_days")
    @classmethod
    def _enforce_cbp_retention(cls, value: int) -> int:
        """Refuse a retention period shorter than 19 CFR 163.4 requires."""
        if value < MIN_CBP_RETENTION_DAYS:
            raise ValueError(
                f"audit_retention_days must be >= {MIN_CBP_RETENTION_DAYS} (19 CFR 163.4)"
            )
        return value

    def require_secret(self, name: str) -> str:
        """Return a secret's value or raise if it is unset.

        Args:
            name: Attribute name of a ``SecretStr`` setting.

        Raises:
            RuntimeError: If the secret is empty, so callers fail closed.
        """
        secret = getattr(self, name)
        value = secret.get_secret_value() if isinstance(secret, SecretStr) else ""
        if not value:
            raise RuntimeError(f"Required secret TRIAGE_{name.upper()} is not configured")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
