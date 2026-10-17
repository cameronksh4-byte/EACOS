"""Persistence for inbound messages, documents and triage packets."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

import asyncpg
from pydantic import TypeAdapter

from triage_engine.schemas.documents import (
    BillOfLadenDocument,
    CrossBorderLoadTriagePacket,
    MexicanFacturaDocument,
)
from triage_engine.storage.audit import GENESIS_HASH, ChainHead
from triage_engine.storage.db import resolve_channel_tenant, tenant_transaction

Channel = Literal["whatsapp", "email"]
LogisticsDocument = MexicanFacturaDocument | BillOfLadenDocument
_DOC_ADAPTER: TypeAdapter[LogisticsDocument] = TypeAdapter(LogisticsDocument)


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """A persisted document with its database identifier."""

    id: UUID
    document: LogisticsDocument


def document_references(document: LogisticsDocument) -> list[str]:
    """Identifiers used to pair a factura with its BOL."""
    if isinstance(document, MexicanFacturaDocument):
        return sorted(document.references)
    return sorted({ref.strip().upper() for ref in document.invoice_references if ref.strip()})


class TriageRepository(Protocol):
    """Storage operations the triage pipeline needs."""

    async def resolve_tenant(self, channel: Channel, external_id: str) -> UUID | None:
        """Map an inbound channel address to its tenant, or None if unknown."""
        ...

    async def record_inbound(
        self,
        tenant_id: UUID,
        channel: Channel,
        external_id: str,
        payload_sha256: str,
        sender_token: str | None,
    ) -> UUID | None:
        """Insert an inbound message; return None if it was already received."""
        ...

    async def set_inbound_status(self, tenant_id: UUID, inbound_id: UUID, status: str) -> None:
        """Update processing status (received/processed/failed)."""
        ...

    async def save_document(
        self, tenant_id: UUID, inbound_id: UUID, document: LogisticsDocument, source_sha256: str
    ) -> UUID:
        """Persist an extracted document; return its id."""
        ...

    async def find_counterpart(
        self, tenant_id: UUID, document: LogisticsDocument
    ) -> StoredDocument | None:
        """Find an unpaired document of the other type that references this one."""
        ...

    async def save_packet(
        self,
        tenant_id: UUID,
        packet: CrossBorderLoadTriagePacket,
        factura_id: UUID,
        bol_id: UUID,
    ) -> None:
        """Persist a triage packet and mark both documents as paired."""
        ...


class PostgresTriageRepository:
    """Supabase-backed repository; every query runs under tenant RLS."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def resolve_tenant(self, channel: Channel, external_id: str) -> UUID | None:
        return await resolve_channel_tenant(self._pool, channel, external_id)

    async def record_inbound(
        self,
        tenant_id: UUID,
        channel: Channel,
        external_id: str,
        payload_sha256: str,
        sender_token: str | None,
    ) -> UUID | None:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            inbound_id: UUID | None = await conn.fetchval(
                """
                INSERT INTO public.inbound_messages
                    (tenant_id, channel, external_message_id, payload_sha256, sender_token)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (tenant_id, channel, external_message_id) DO NOTHING
                RETURNING id
                """,
                tenant_id,
                channel,
                external_id,
                payload_sha256,
                sender_token,
            )
        return inbound_id

    async def set_inbound_status(self, tenant_id: UUID, inbound_id: UUID, status: str) -> None:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            await conn.execute(
                "UPDATE public.inbound_messages SET status = $2, updated_at = now() WHERE id = $1",
                inbound_id,
                status,
            )

    async def save_document(
        self, tenant_id: UUID, inbound_id: UUID, document: LogisticsDocument, source_sha256: str
    ) -> UUID:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            doc_id: UUID = await conn.fetchval(
                """
                INSERT INTO public.shipping_documents
                    (tenant_id, inbound_message_id, doc_type, source_sha256,
                     doc_references, extracted)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                RETURNING id
                """,
                tenant_id,
                inbound_id,
                document.document_type,
                source_sha256,
                document_references(document),
                document.model_dump_json(),
            )
        return doc_id

    async def find_counterpart(
        self, tenant_id: UUID, document: LogisticsDocument
    ) -> StoredDocument | None:
        refs = document_references(document)
        if not refs:
            return None
        other = "BILL_OF_LADING" if document.document_type == "FACTURA" else "FACTURA"
        async with tenant_transaction(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, extracted
                FROM public.shipping_documents
                WHERE doc_type = $1 AND packet_id IS NULL AND doc_references && $2::text[]
                ORDER BY created_at DESC
                LIMIT 1
                """,
                other,
                refs,
            )
        if row is None:
            return None
        return StoredDocument(id=row["id"], document=_DOC_ADAPTER.validate_json(row["extracted"]))

    async def save_packet(
        self,
        tenant_id: UUID,
        packet: CrossBorderLoadTriagePacket,
        factura_id: UUID,
        bol_id: UUID,
    ) -> None:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO public.triage_packets
                    (id, tenant_id, load_reference, disposition, weight_variance_pct, packet)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                packet.packet_id,
                tenant_id,
                packet.load_reference,
                packet.disposition.value,
                packet.weight_variance_pct,
                packet.model_dump_json(),
            )
            await conn.execute(
                "UPDATE public.shipping_documents SET packet_id = $1 WHERE id = ANY($2::uuid[])",
                packet.packet_id,
                [factura_id, bol_id],
            )


class PostgresChainHeadStore:
    """Audit chain heads in ``public.audit_chain_heads``, locked FOR UPDATE."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def locked(self, tenant_id: UUID) -> AbstractAsyncContextManager[ChainHead]:
        @asynccontextmanager
        async def _cm() -> AsyncIterator[ChainHead]:
            async with tenant_transaction(self._pool, tenant_id) as conn:
                await conn.execute(
                    """
                    INSERT INTO public.audit_chain_heads (tenant_id, last_sequence, last_hash)
                    VALUES ($1, 0, $2) ON CONFLICT (tenant_id) DO NOTHING
                    """,
                    tenant_id,
                    GENESIS_HASH,
                )
                row = await conn.fetchrow(
                    "SELECT last_sequence, last_hash FROM public.audit_chain_heads "
                    "WHERE tenant_id = $1 FOR UPDATE",
                    tenant_id,
                )
                head = ChainHead(tenant_id, row["last_sequence"], row["last_hash"])
                yield head
                if head.appended:
                    await conn.execute(
                        """
                        UPDATE public.audit_chain_heads
                        SET last_sequence = $2, last_hash = $3, updated_at = now()
                        WHERE tenant_id = $1
                        """,
                        tenant_id,
                        head.sequence,
                        head.last_hash,
                    )
                    await conn.executemany(
                        """
                        INSERT INTO public.audit_events (tenant_id, sequence, record_hash)
                        VALUES ($1, $2, $3)
                        """,
                        [(tenant_id, seq, digest) for seq, digest in head.appended],
                    )

        return _cm()
