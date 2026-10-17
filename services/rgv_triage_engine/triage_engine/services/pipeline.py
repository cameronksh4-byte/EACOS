"""Triage pipeline: parse/extract, sanitise, persist, pair, reconcile, audit."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError

from triage_engine.schemas.documents import (
    BillOfLadenDocument,
    CrossBorderLoadTriagePacket,
    MexicanFacturaDocument,
)
from triage_engine.security.xml_safe import UnsafeXMLError
from triage_engine.services.cfdi import CFDIParseError, parse_cfdi
from triage_engine.services.email_intake import Attachment
from triage_engine.services.extraction import BedrockExtractionService, ExtractionError
from triage_engine.services.ingestion import IngestionSanitizer
from triage_engine.services.reconciliation import build_triage_packet
from triage_engine.services.whatsapp import MediaDownloadError, WhatsAppMediaClient
from triage_engine.storage.audit import AuditEventType, AuditLogger
from triage_engine.storage.repository import (
    LogisticsDocument,
    TriageRepository,
    document_references,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Ingested:
    id: UUID
    document: LogisticsDocument


class TriagePipeline:
    """Coordinates document processing for one tenant submission.

    Args:
        repository: Tenant-scoped persistence (RLS-enforced).
        audit: Hash-chained audit logger.
        sanitizer: PII tokeniser.
        extractor: Claude-on-Bedrock extraction service.
        media_client: WhatsApp media downloader.
        weight_threshold_pct: Weight variance threshold for HOLD.
    """

    def __init__(
        self,
        *,
        repository: TriageRepository,
        audit: AuditLogger,
        sanitizer: IngestionSanitizer,
        extractor: BedrockExtractionService,
        media_client: WhatsAppMediaClient | None,
        weight_threshold_pct: Decimal,
    ) -> None:
        self._repo = repository
        self._audit = audit
        self._sanitizer = sanitizer
        self._extractor = extractor
        self._media = media_client
        self._threshold = weight_threshold_pct

    async def parse_xml_attachment(
        self, tenant_id: UUID, attachment: Attachment, actor: str
    ) -> MexicanFacturaDocument:
        """Parse a CFDI XML synchronously so XXE/invalid XML can be rejected inline.

        Raises:
            UnsafeXMLError: Forbidden XML constructs (XXE, DTD, entities).
            CFDIParseError: Not a valid, internally consistent CFDI 4.0.
        """
        try:
            document = parse_cfdi(attachment.content)
        except (UnsafeXMLError, CFDIParseError) as exc:
            await self._audit.record(
                tenant_id=tenant_id,
                event_type=AuditEventType.WEBHOOK_REJECTED,
                actor=actor,
                details={
                    "reason": type(exc).__name__,
                    "message": str(exc)[:200],
                    "source_sha256": attachment.sha256,
                },
            )
            raise
        return document

    async def process_whatsapp_media(
        self, tenant_id: UUID, inbound_id: UUID, media_id: str, filename: str | None
    ) -> list[CrossBorderLoadTriagePacket]:
        """Background task: download a WhatsApp attachment and triage it."""
        actor = "system:whatsapp"
        if self._media is None:
            raise RuntimeError("WhatsApp media client not configured")
        try:
            media = await self._media.download(media_id)
        except MediaDownloadError as exc:
            await self._fail(tenant_id, inbound_id, actor, "media_download", exc)
            return []
        attachment = Attachment(
            filename=filename or "whatsapp-attachment",
            media_type=media.mime_type,
            content=media.content,
            sha256=media.sha256,
        )
        return await self.process_attachments(tenant_id, inbound_id, [attachment], actor)

    async def process_attachments(
        self,
        tenant_id: UUID,
        inbound_id: UUID,
        attachments: list[Attachment],
        actor: str,
        preparsed: dict[str, MexicanFacturaDocument] | None = None,
    ) -> list[CrossBorderLoadTriagePacket]:
        """Turn attachments into stored documents and any resulting packets.

        Never raises for a bad document: failures are audited and the
        inbound message is marked ``failed`` so a broker can follow up.
        """
        preparsed = preparsed or {}
        ingested: list[_Ingested] = []
        failures = 0
        for attachment in attachments:
            try:
                document = preparsed.get(attachment.sha256) or await self._document_from(
                    tenant_id, attachment, actor
                )
                clean = self._sanitizer.sanitize_document(tenant_id, document)
                doc_id = await self._repo.save_document(
                    tenant_id, inbound_id, clean, attachment.sha256
                )
                ingested.append(_Ingested(doc_id, clean))
            except (ExtractionError, UnsafeXMLError, CFDIParseError, ValidationError) as exc:
                failures += 1
                await self._fail(tenant_id, inbound_id, actor, "document", exc, attachment.sha256)

        try:
            packets = await self._pair_and_triage(tenant_id, ingested, actor)
        except ValidationError as exc:
            await self._fail(tenant_id, inbound_id, actor, "triage", exc)
            return []
        status = "failed" if failures and not ingested else "processed"
        await self._repo.set_inbound_status(tenant_id, inbound_id, status)
        return packets

    async def _document_from(
        self, tenant_id: UUID, attachment: Attachment, actor: str
    ) -> LogisticsDocument:
        if attachment.is_cfdi_xml:
            document = parse_cfdi(attachment.content)
            await self._audit.record(
                tenant_id=tenant_id,
                event_type=AuditEventType.DOCUMENT_PARSED,
                actor=actor,
                details={
                    "parser": "cfdi40-deterministic",
                    "source_sha256": attachment.sha256,
                    "document_type": document.document_type,
                },
            )
            return document

        masker = self._sanitizer.masker(tenant_id)
        try:
            result = await self._extractor.extract(
                content=attachment.content, media_type=attachment.media_type, masker=masker
            )
        except ExtractionError as exc:
            await self._audit.record(
                tenant_id=tenant_id,
                event_type=AuditEventType.LLM_EXTRACTION_FAILED,
                actor=actor,
                details={"source_sha256": attachment.sha256, "error": str(exc)[:200]},
            )
            raise
        await self._audit.record(
            tenant_id=tenant_id,
            event_type=AuditEventType.LLM_EXTRACTION,
            actor=actor,
            details={
                "model_id": result.model_id,
                "prompt_version": result.prompt_version,
                "input_sha256": result.input_sha256,
                "output_sha256": result.output_sha256,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_ms": result.latency_ms,
                "document_type": result.document.document_type,
                "schema_validated": True,
            },
        )
        return result.document

    async def _pair_and_triage(
        self, tenant_id: UUID, ingested: list[_Ingested], actor: str
    ) -> list[CrossBorderLoadTriagePacket]:
        facturas = [i for i in ingested if isinstance(i.document, MexicanFacturaDocument)]
        bols = [i for i in ingested if isinstance(i.document, BillOfLadenDocument)]
        paired: set[UUID] = set()
        packets: list[CrossBorderLoadTriagePacket] = []

        # 1. Pair within this submission: by reference, or 1:1 when unambiguous.
        for bol in bols:
            match = next(
                (
                    f
                    for f in facturas
                    if f.id not in paired
                    and set(document_references(bol.document))
                    & set(document_references(f.document))
                ),
                None,
            )
            if match is None and len(facturas) == 1 and len(bols) == 1:
                match = facturas[0]
            if match is not None:
                packets.append(await self._triage(tenant_id, match, bol, actor))
                paired.update({match.id, bol.id})

        # 2. Pair remaining documents with earlier, unpaired counterparts.
        for item in ingested:
            if item.id in paired:
                continue
            counterpart = await self._repo.find_counterpart(tenant_id, item.document)
            if counterpart is None:
                continue
            other = _Ingested(counterpart.id, counterpart.document)
            factura, bol = (
                (item, other) if item.document.document_type == "FACTURA" else (other, item)
            )
            packets.append(await self._triage(tenant_id, factura, bol, actor))
            paired.add(item.id)
        return packets

    async def _triage(
        self, tenant_id: UUID, factura: _Ingested, bol: _Ingested, actor: str
    ) -> CrossBorderLoadTriagePacket:
        packet = build_triage_packet(
            tenant_id=tenant_id,
            factura=factura.document,  # type: ignore[arg-type]
            bol=bol.document,  # type: ignore[arg-type]
            threshold_pct=self._threshold,
        )
        archived = await self._audit.archive(
            tenant_id=tenant_id,
            kind="triage-packet",
            record_id=packet.packet_id,
            payload=packet.model_dump(mode="json"),
        )
        await self._repo.save_packet(tenant_id, packet, factura.id, bol.id)
        await self._audit.record(
            tenant_id=tenant_id,
            event_type=AuditEventType.TRIAGE_PACKET_CREATED,
            actor=actor,
            subject_id=str(packet.packet_id),
            details={
                "load_reference": packet.load_reference,
                "disposition": packet.disposition.value,
                "weight_variance_pct": packet.weight_variance_pct,
                "finding_codes": [f.code.value for f in packet.findings],
                "factura_document_id": str(factura.id),
                "bol_document_id": str(bol.id),
                "archive_key": archived.key,
                "packet_sha256": archived.sha256,
                "review_status": "PENDING_BROKER_REVIEW",
            },
        )
        logger.info(
            "triage packet created",
            extra={"packet_id": str(packet.packet_id), "disposition": packet.disposition.value},
        )
        return packet

    async def _fail(
        self,
        tenant_id: UUID,
        inbound_id: UUID,
        actor: str,
        stage: str,
        exc: Exception,
        source_sha256: str | None = None,
    ) -> None:
        logger.warning("processing failed at %s: %s", stage, type(exc).__name__)
        await self._audit.record(
            tenant_id=tenant_id,
            event_type=AuditEventType.PROCESSING_FAILED,
            actor=actor,
            subject_id=str(inbound_id),
            details={"stage": stage, "error": type(exc).__name__, "source_sha256": source_sha256},
        )
        await self._repo.set_inbound_status(tenant_id, inbound_id, "failed")
