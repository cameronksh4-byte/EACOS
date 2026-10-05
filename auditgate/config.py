"""Runtime configuration, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

Provider = Literal["heuristic", "local", "openai", "anthropic"]


class Settings(BaseModel):
    provider: Provider = "heuristic"

    local_base_url: str = "http://localhost:11434/v1"
    local_model: str = "llama3.1"

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5-5"

    spacy_model: str = "en_core_web_sm"
    redact_orgs: bool = False
    deny_terms: list[str] = Field(default_factory=list)

    max_retries: int = Field(default=2, ge=0, le=5)
    egress_allowed_domains: list[str] = Field(default_factory=list)

    @property
    def sends_data_offsite(self) -> bool:
        """True when document text (sanitized) is sent to a third-party API."""
        return self.provider in ("openai", "anthropic")

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        env = os.environ.get

        def secret(name: str) -> SecretStr | None:
            value = env(name)
            return SecretStr(value) if value else None

        return cls(
            provider=env("AUDITGATE_PROVIDER", "heuristic"),  # type: ignore[arg-type]
            local_base_url=env("AUDITGATE_LOCAL_BASE_URL", cls.model_fields["local_base_url"].default),
            local_model=env("AUDITGATE_LOCAL_MODEL", cls.model_fields["local_model"].default),
            openai_api_key=secret("OPENAI_API_KEY"),
            openai_model=env("AUDITGATE_OPENAI_MODEL", cls.model_fields["openai_model"].default),
            anthropic_api_key=secret("ANTHROPIC_API_KEY"),
            anthropic_model=env("AUDITGATE_ANTHROPIC_MODEL", cls.model_fields["anthropic_model"].default),
            spacy_model=env("AUDITGATE_SPACY_MODEL", cls.model_fields["spacy_model"].default),
            redact_orgs=env("AUDITGATE_REDACT_ORGS", "false").lower() in ("1", "true", "yes"),
            deny_terms=[t.strip() for t in env("AUDITGATE_DENY_TERMS", "").split(",") if t.strip()],
            max_retries=int(env("AUDITGATE_MAX_RETRIES", "2")),
            egress_allowed_domains=[d.strip() for d in env("AUDITGATE_EGRESS_ALLOWED_DOMAINS", "").split(",")
                                    if d.strip()],
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
