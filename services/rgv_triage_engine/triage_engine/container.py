"""Construction of the long-lived service objects."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from triage_engine.config import Settings
from triage_engine.security.email_auth import EmailAuthenticator
from triage_engine.services.extraction import BedrockExtractionService
from triage_engine.services.ingestion import IngestionSanitizer
from triage_engine.services.pipeline import TriagePipeline
from triage_engine.services.whatsapp import WhatsAppMediaClient
from triage_engine.storage.audit import AuditLogger, S3ObjectLockStore
from triage_engine.storage.repository import TriageRepository

logger = logging.getLogger(__name__)


@dataclass
class AppServices:
    """Everything the request handlers need, built once per process."""

    settings: Settings
    repository: TriageRepository
    audit: AuditLogger
    sanitizer: IngestionSanitizer
    pipeline: TriagePipeline
    email_authenticator: EmailAuthenticator
    closers: list[Any] = field(default_factory=list)

    async def aclose(self) -> None:
        """Release pools and HTTP clients."""
        for closer in reversed(self.closers):
            try:
                await closer()
            except Exception:
                logger.warning("error while closing a resource", exc_info=True)


async def build_services(settings: Settings) -> AppServices:
    """Wire production dependencies (Supabase, S3, Bedrock, Graph API).

    Raises:
        RuntimeError: If a required secret is missing or a compliance
            precondition (Object Lock, no Bedrock invocation logging) fails.
    """
    import boto3

    from triage_engine.storage.db import create_pool
    from triage_engine.storage.repository import (
        PostgresChainHeadStore,
        PostgresTriageRepository,
    )

    for secret in ("whatsapp_app_secret", "email_relay_secret", "pii_hmac_key"):
        settings.require_secret(secret)
    if not settings.audit_bucket:
        raise RuntimeError("TRIAGE_AUDIT_BUCKET is not configured")

    pool = await create_pool(settings)
    closers: list[Any] = [pool.close]
    try:
        object_store = S3ObjectLockStore(
            boto3.client("s3", region_name=settings.aws_region),
            settings.audit_bucket,
            settings.audit_retention_days,
            settings.audit_kms_key_id,
        )
        extractor = BedrockExtractionService(settings)
        if settings.enforce_startup_compliance_checks:
            await object_store.assert_object_lock_enabled()
            await extractor.assert_zero_retention(
                boto3.client("bedrock", region_name=settings.aws_region)
            )

        repository = PostgresTriageRepository(pool)
        audit = AuditLogger(object_store, PostgresChainHeadStore(pool), settings.audit_key_prefix)
        sanitizer = IngestionSanitizer(settings.pii_hmac_key.get_secret_value().encode())
        media_client = WhatsAppMediaClient(settings)
        closers.append(media_client.aclose)
        pipeline = TriagePipeline(
            repository=repository,
            audit=audit,
            sanitizer=sanitizer,
            extractor=extractor,
            media_client=media_client,
            weight_threshold_pct=settings.weight_variance_threshold_pct,
        )
    except BaseException:
        for closer in reversed(closers):
            await closer()
        raise

    return AppServices(
        settings=settings,
        repository=repository,
        audit=audit,
        sanitizer=sanitizer,
        pipeline=pipeline,
        email_authenticator=EmailAuthenticator(
            require_spf_pass=settings.email_require_spf_pass,
            require_aligned_dkim=settings.email_require_aligned_dkim,
            dns_timeout=settings.email_dns_timeout_seconds,
        ),
        closers=closers,
    )
