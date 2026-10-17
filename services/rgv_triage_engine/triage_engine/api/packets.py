"""Broker review queue and human-in-the-loop sign-off."""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from triage_engine.container import AppServices
from triage_engine.schemas.review import (
    OPEN_REVIEW_STATUSES,
    BrokerReviewRecord,
    BrokerReviewRequest,
    PacketSummary,
    ReviewDecision,
    ReviewPolicyError,
    ReviewStatus,
    enforce_review_policy,
)
from triage_engine.security.auth import BrokerPrincipal, require_broker
from triage_engine.storage.audit import AuditEventType, canonical_json

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/packets", tags=["broker review"])

Broker = Annotated[BrokerPrincipal, Depends(require_broker)]


def _services(request: Request) -> AppServices:
    services: AppServices = request.app.state.services
    return services


@router.get("")
async def list_packets(
    request: Request,
    broker: Broker,
    review_status: ReviewStatus | None = ReviewStatus.PENDING_BROKER_REVIEW,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[PacketSummary]:
    """The broker's review queue (pending packets by default), newest first."""
    return await _services(request).repository.list_packets(broker.user_id, review_status, limit)


@router.get("/{packet_id}")
async def get_packet(request: Request, broker: Broker, packet_id: UUID) -> dict[str, Any]:
    """One packet with its findings and review status."""
    found = await _services(request).repository.get_packet(broker.user_id, packet_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    packet, review_status = found
    return {
        "review_status": review_status.value,
        "packet_sha256": hashlib.sha256(canonical_json(packet.model_dump(mode="json"))).hexdigest(),
        "packet": packet.model_dump(mode="json"),
    }


@router.post("/{packet_id}/reviews", status_code=status.HTTP_201_CREATED)
async def review_packet(
    request: Request, broker: Broker, packet_id: UUID, body: BrokerReviewRequest
) -> BrokerReviewRecord:
    """Record a licensed broker's decision on a packet.

    Approvals must acknowledge every WARNING/CRITICAL finding, approving a
    HOLD needs a written override reason, and (by default) approvals need an
    MFA session. The decision is archived under S3 Object Lock and chained
    into the audit log before the packet's status changes.
    """
    services = _services(request)
    tenant_id = broker.user_id
    found = await services.repository.get_packet(tenant_id, packet_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    packet, current_status = found
    if current_status not in OPEN_REVIEW_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="review is final")

    async def deny(reason: str, http_status: int) -> HTTPException:
        await services.audit.record(
            tenant_id=tenant_id,
            event_type=AuditEventType.BROKER_REVIEW_DENIED,
            actor=f"broker:{broker.user_id}",
            subject_id=str(packet_id),
            details={"decision": body.decision.value, "reason": reason},
        )
        return HTTPException(status_code=http_status, detail=reason)

    if (
        body.decision is ReviewDecision.APPROVE
        and services.settings.broker_approval_requires_mfa
        and broker.aal != "aal2"
    ):
        raise await deny("approval requires a multi-factor session", status.HTTP_403_FORBIDDEN)
    try:
        enforce_review_policy(packet, body)
    except ReviewPolicyError as exc:
        raise await deny(str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT) from None

    masker = services.sanitizer.masker(tenant_id)
    packet_sha256 = hashlib.sha256(canonical_json(packet.model_dump(mode="json"))).hexdigest()
    record = BrokerReviewRecord(
        packet_id=packet_id,
        tenant_id=tenant_id,
        reviewer_id=broker.user_id,
        decision=body.decision,
        resulting_status=body.decision.resulting_status,
        engine_disposition=packet.disposition,
        packet_sha256=packet_sha256,
        acknowledged_findings=sorted(set(body.acknowledged_findings)),
        override_reason=masker.mask_text(body.override_reason) if body.override_reason else None,
        notes=masker.mask_text(body.notes) if body.notes else None,
    )
    archived = await services.audit.archive(
        tenant_id=tenant_id,
        kind="broker-review",
        record_id=record.review_id,
        payload=record.model_dump(mode="json"),
    )
    if not await services.repository.record_review(tenant_id, record, archived.key):
        raise await deny("review is final", status.HTTP_409_CONFLICT)

    await services.audit.record(
        tenant_id=tenant_id,
        event_type=AuditEventType.BROKER_REVIEW_RECORDED,
        actor=f"broker:{broker.user_id}",
        subject_id=str(packet_id),
        details={
            "review_id": str(record.review_id),
            "decision": record.decision.value,
            "resulting_status": record.resulting_status.value,
            "engine_disposition": record.engine_disposition.value,
            "packet_sha256": packet_sha256,
            "review_archive_key": archived.key,
            "review_sha256": archived.sha256,
            "acknowledged_findings": [c.value for c in record.acknowledged_findings],
            "override": record.override_reason is not None,
            "aal": broker.aal,
        },
    )
    logger.info(
        "broker review recorded",
        extra={"packet_id": str(packet_id), "decision": record.decision.value},
    )
    return record
