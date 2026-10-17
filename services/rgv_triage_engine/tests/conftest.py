"""Shared fixtures and in-memory fakes."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from triage_engine.config import Settings
from triage_engine.container import AppServices
from triage_engine.main import create_app
from triage_engine.schemas.documents import (
    BillOfLadenDocument,
    CrossBorderLoadTriagePacket,
    MexicanFacturaDocument,
)
from triage_engine.schemas.review import (
    OPEN_REVIEW_STATUSES,
    BrokerReviewRecord,
    PacketSummary,
    ReviewStatus,
)
from triage_engine.security.auth import SupabaseJWTVerifier
from triage_engine.security.email_auth import (
    AuthResult,
    EmailAuthVerdict,
    EnvelopeInfo,
)
from triage_engine.services.extraction import ExtractionResult
from triage_engine.services.ingestion import IngestionSanitizer
from triage_engine.services.pipeline import TriagePipeline
from triage_engine.services.whatsapp import DownloadedMedia
from triage_engine.storage.audit import (
    AuditLogger,
    InMemoryChainHeadStore,
    InMemoryObjectStore,
)
from triage_engine.storage.repository import (
    Channel,
    LogisticsDocument,
    StoredDocument,
    document_references,
)

FIXTURES = Path(__file__).parent / "fixtures"
TENANT = UUID("11111111-1111-4111-8111-111111111111")
WHATSAPP_PHONE_ID = "1065550001"
INBOX = "docs@tenant.example.com"
APP_SECRET = "meta-app-secret-for-tests"
RELAY_SECRET = "relay-secret-for-tests"
PII_KEY = "k" * 48
JWT_SECRET = "supabase-legacy-jwt-secret-for-tests-0123456789"
JWT_ISSUER = "https://testproject.supabase.co/auth/v1"


class FakeRepository:
    """In-memory stand-in for the RLS-backed Postgres repository."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], UUID] = {
            ("whatsapp", WHATSAPP_PHONE_ID): TENANT,
            ("email", INBOX): TENANT,
        }
        self.inbound: dict[tuple[UUID, str, str], UUID] = {}
        self.status: dict[UUID, str] = {}
        self.documents: dict[UUID, tuple[UUID, LogisticsDocument, UUID | None]] = {}
        self.packets: list[CrossBorderLoadTriagePacket] = []
        self.review_status: dict[UUID, ReviewStatus] = {}
        self.reviews: list[tuple[BrokerReviewRecord, str]] = []

    async def resolve_tenant(self, channel: Channel, external_id: str) -> UUID | None:
        return self.routes.get((channel, external_id.lower()))

    async def record_inbound(
        self,
        tenant_id: UUID,
        channel: Channel,
        external_id: str,
        payload_sha256: str,
        sender_token: str | None,
    ) -> UUID | None:
        key = (tenant_id, channel, external_id)
        if key in self.inbound:
            return None
        self.inbound[key] = uuid4()
        self.status[self.inbound[key]] = "received"
        return self.inbound[key]

    async def set_inbound_status(self, tenant_id: UUID, inbound_id: UUID, status: str) -> None:
        self.status[inbound_id] = status

    async def save_document(
        self, tenant_id: UUID, inbound_id: UUID, document: LogisticsDocument, source_sha256: str
    ) -> UUID:
        doc_id = uuid4()
        self.documents[doc_id] = (tenant_id, document, None)
        return doc_id

    async def find_counterpart(
        self, tenant_id: UUID, document: LogisticsDocument
    ) -> StoredDocument | None:
        refs = set(document_references(document))
        for doc_id, (owner, other, packet_id) in reversed(list(self.documents.items())):
            if (
                owner == tenant_id
                and packet_id is None
                and other.document_type != document.document_type
                and refs & set(document_references(other))
            ):
                return StoredDocument(doc_id, other)
        return None

    async def save_packet(
        self,
        tenant_id: UUID,
        packet: CrossBorderLoadTriagePacket,
        factura_id: UUID,
        bol_id: UUID,
    ) -> None:
        self.packets.append(packet)
        self.review_status[packet.packet_id] = ReviewStatus.PENDING_BROKER_REVIEW
        for doc_id in (factura_id, bol_id):
            owner, doc, _ = self.documents[doc_id]
            self.documents[doc_id] = (owner, doc, packet.packet_id)

    async def list_packets(
        self, tenant_id: UUID, review_status: ReviewStatus | None, limit: int
    ) -> list[PacketSummary]:
        rows = [
            PacketSummary(
                packet_id=p.packet_id,
                load_reference=p.load_reference,
                disposition=p.disposition,
                review_status=self.review_status[p.packet_id],
                weight_variance_pct=str(p.weight_variance_pct),
                created_at=p.created_at,
            )
            for p in reversed(self.packets)
            if p.tenant_id == tenant_id
            and (review_status is None or self.review_status[p.packet_id] is review_status)
        ]
        return rows[:limit]

    async def get_packet(
        self, tenant_id: UUID, packet_id: UUID
    ) -> tuple[CrossBorderLoadTriagePacket, ReviewStatus] | None:
        for packet in self.packets:
            if packet.packet_id == packet_id and packet.tenant_id == tenant_id:
                return packet, self.review_status[packet_id]
        return None

    async def record_review(
        self, tenant_id: UUID, review: BrokerReviewRecord, archive_key: str
    ) -> bool:
        if self.review_status.get(review.packet_id) not in OPEN_REVIEW_STATUSES:
            return False
        self.review_status[review.packet_id] = review.resulting_status
        self.reviews.append((review, archive_key))
        return True


class FakeExtractor:
    """Returns pre-baked documents instead of calling Bedrock."""

    def __init__(self) -> None:
        self.queue: list[LogisticsDocument] = []
        self.calls = 0

    async def extract(self, *, content: bytes, media_type: str, masker: Any) -> ExtractionResult:
        self.calls += 1
        document = self.queue.pop(0)
        return ExtractionResult(
            document=document,
            model_id="anthropic.claude-sonnet-5-5",
            prompt_version="test",
            input_sha256="0" * 64,
            output_sha256="1" * 64,
            input_tokens=100,
            output_tokens=50,
            latency_ms=5,
        )


class FakeMediaClient:
    """Serves attachment bytes for WhatsApp media IDs."""

    def __init__(self) -> None:
        self.media: dict[str, DownloadedMedia] = {}

    async def download(self, media_id: str) -> DownloadedMedia:
        return self.media[media_id]

    async def aclose(self) -> None:
        return None


class FakeEmailAuthenticator:
    """Accepts or rejects every message per ``accept``."""

    def __init__(self, accept: bool = True) -> None:
        self.accept = accept

    async def verify(self, raw_message: bytes, envelope: EnvelopeInfo) -> EmailAuthVerdict:
        result = AuthResult.PASS if self.accept else AuthResult.FAIL
        return EmailAuthVerdict(
            spf=result,
            dkim=result,
            dkim_domain="supplier.com.mx" if self.accept else None,
            from_domain="supplier.com.mx",
            aligned=self.accept,
            accepted=self.accept,
            reason="accepted" if self.accept else "SPF fail",
        )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        whatsapp_app_secret=SecretStr(APP_SECRET),
        whatsapp_verify_token=SecretStr("verify-me"),
        whatsapp_access_token=SecretStr("token"),
        email_relay_secret=SecretStr(RELAY_SECRET),
        pii_hmac_key=SecretStr(PII_KEY),
        allowed_hosts=["testserver"],
        enforce_startup_compliance_checks=False,
    )


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def object_store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
def audit(object_store: InMemoryObjectStore) -> AuditLogger:
    return AuditLogger(object_store, InMemoryChainHeadStore())


@pytest.fixture
def extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def media_client() -> FakeMediaClient:
    return FakeMediaClient()


@pytest.fixture
def email_authenticator() -> FakeEmailAuthenticator:
    return FakeEmailAuthenticator()


@pytest.fixture
def sanitizer() -> IngestionSanitizer:
    return IngestionSanitizer(PII_KEY.encode())


@pytest.fixture
def services(
    settings: Settings,
    repo: FakeRepository,
    audit: AuditLogger,
    sanitizer: IngestionSanitizer,
    extractor: FakeExtractor,
    media_client: FakeMediaClient,
    email_authenticator: FakeEmailAuthenticator,
) -> AppServices:
    pipeline = TriagePipeline(
        repository=repo,
        audit=audit,
        sanitizer=sanitizer,
        extractor=extractor,  # type: ignore[arg-type]
        media_client=media_client,  # type: ignore[arg-type]
        weight_threshold_pct=Decimal("3.0"),
    )
    return AppServices(
        settings=settings,
        repository=repo,
        audit=audit,
        sanitizer=sanitizer,
        pipeline=pipeline,
        email_authenticator=email_authenticator,  # type: ignore[arg-type]
        jwt_verifier=SupabaseJWTVerifier(issuer=JWT_ISSUER, hs256_secret=JWT_SECRET),
    )


@pytest.fixture
def client(settings: Settings, services: AppServices) -> Iterator[TestClient]:
    with TestClient(create_app(settings, services)) as test_client:
        yield test_client


@pytest.fixture
def cfdi_xml() -> bytes:
    return (FIXTURES / "cfdi40_carta_porte.xml").read_bytes()


def make_bol(**overrides: Any) -> BillOfLadenDocument:
    """A BOL matching ``cfdi40_carta_porte.xml`` unless overridden."""
    data: dict[str, Any] = {
        "bol_number": "RGV-2026-000123",
        "scac": "ABCD",
        "carrier_name": "Transportes del Valle",
        "port_of_entry": "Pharr",
        "invoice_references": ["A-1001"],
        "line_items": [
            {
                "description": "Aguacate Hass",
                "package_count": 960,
                "package_type": "CARTON",
                "weight": {"value": "21000", "unit": "LB"},
            },
            {
                "description": "Limon persa",
                "package_count": 240,
                "package_type": "CARTON",
                "weight": {"value": "5350", "unit": "LB"},
            },
        ],
        "total_package_count": 1200,
        "total_gross_weight": {"value": "26350", "unit": "LB"},
        "pallet_count": 20,
        "pallet_type": "GMA_48X40",
    }
    data.update(overrides)
    return BillOfLadenDocument.model_validate(data)


def make_factura(**overrides: Any) -> MexicanFacturaDocument:
    """A minimal factura stating net weight in kilograms."""
    data: dict[str, Any] = {
        "series": "A",
        "folio": "1001",
        "issuer_rfc": "EXP010101AB1",
        "receiver_rfc": "XEXX010101000",
        "currency": "USD",
        "subtotal": "30000.00",
        "total": "30000.00",
        "line_items": [
            {
                "description": "Aguacate Hass",
                "quantity": "960",
                "unit_value": "25.00",
                "amount": "24000.00",
            },
            {
                "description": "Limon persa",
                "quantity": "240",
                "unit_value": "25.00",
                "amount": "6000.00",
            },
        ],
        "net_weight": {"value": "11590", "unit": "KG"},
        "package_count": 1200,
    }
    data.update(overrides)
    return MexicanFacturaDocument.model_validate(data)
