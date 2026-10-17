"""Human-in-the-loop broker review of triage packets.

The engine is a pre-bridge triage and proofreading utility. It never decides
whether merchandise may be entered, classified or filed: that is customs
business reserved to a licensed broker (19 CFR Part 111). Every packet
therefore waits in ``PENDING_BROKER_REVIEW`` until a broker records a
decision, and the decision (not the engine's disposition) is the only state
that releases a load for the broker's own filing workflow.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from triage_engine.schemas.documents import (
    CrossBorderLoadTriagePacket,
    FindingCode,
    FindingSeverity,
    TriageDisposition,
)

MIN_OVERRIDE_REASON_CHARS = 20


class ReviewStatus(StrEnum):
    """Lifecycle of a packet's broker review."""

    PENDING_BROKER_REVIEW = "PENDING_BROKER_REVIEW"
    CORRECTION_REQUESTED = "CORRECTION_REQUESTED"
    APPROVED_BY_BROKER = "APPROVED_BY_BROKER"
    REJECTED_BY_BROKER = "REJECTED_BY_BROKER"


#: States from which a new decision may still be recorded.
OPEN_REVIEW_STATUSES = frozenset(
    {ReviewStatus.PENDING_BROKER_REVIEW, ReviewStatus.CORRECTION_REQUESTED}
)


class ReviewDecision(StrEnum):
    """What the broker decided."""

    APPROVE = "APPROVE"
    REQUEST_CORRECTION = "REQUEST_CORRECTION"
    REJECT = "REJECT"

    @property
    def resulting_status(self) -> ReviewStatus:
        """The packet status this decision moves to."""
        return {
            ReviewDecision.APPROVE: ReviewStatus.APPROVED_BY_BROKER,
            ReviewDecision.REQUEST_CORRECTION: ReviewStatus.CORRECTION_REQUESTED,
            ReviewDecision.REJECT: ReviewStatus.REJECTED_BY_BROKER,
        }[self]


class BrokerReviewRequest(BaseModel):
    """Body of ``POST /v1/packets/{packet_id}/reviews``."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    decision: ReviewDecision
    acknowledged_findings: list[FindingCode] = Field(default_factory=list, max_length=20)
    override_reason: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=4000)


class ReviewPolicyError(ValueError):
    """The decision does not meet the reasonable-care rules for this packet."""


def enforce_review_policy(
    packet: CrossBorderLoadTriagePacket, request: BrokerReviewRequest
) -> None:
    """Apply reasonable-care rules before a broker approval is accepted.

    * Approving any packet requires acknowledging each WARNING and CRITICAL
      finding by code, so a discrepancy cannot be clicked past unseen.
    * Approving a HOLD additionally requires a written override reason.

    Rejections and correction requests are always allowed.

    Raises:
        ReviewPolicyError: If an approval does not meet these rules.
    """
    if request.decision is not ReviewDecision.APPROVE:
        return
    required = {
        f.code
        for f in packet.findings
        if f.severity in (FindingSeverity.WARNING, FindingSeverity.CRITICAL)
    }
    missing = required - set(request.acknowledged_findings)
    if missing:
        raise ReviewPolicyError(
            "approval must acknowledge findings: " + ", ".join(sorted(m.value for m in missing))
        )
    if packet.disposition is TriageDisposition.HOLD and (
        not request.override_reason or len(request.override_reason) < MIN_OVERRIDE_REASON_CHARS
    ):
        raise ReviewPolicyError(
            f"approving a HOLD requires an override_reason of at least "
            f"{MIN_OVERRIDE_REASON_CHARS} characters"
        )


class BrokerReviewRecord(BaseModel):
    """An immutable, archived broker decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: UUID = Field(default_factory=uuid4)
    packet_id: UUID
    tenant_id: UUID
    reviewer_id: UUID = Field(description="Supabase auth user who signed off.")
    decision: ReviewDecision
    resulting_status: ReviewStatus
    engine_disposition: TriageDisposition
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledged_findings: list[FindingCode] = Field(default_factory=list)
    override_reason: str | None = None
    notes: str | None = None
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _status_matches_decision(self) -> BrokerReviewRecord:
        if self.resulting_status is not self.decision.resulting_status:
            raise ValueError("resulting_status contradicts decision")
        return self


class PacketSummary(BaseModel):
    """Row in the broker's review queue."""

    model_config = ConfigDict(frozen=True)

    packet_id: UUID
    load_reference: str
    disposition: TriageDisposition
    review_status: ReviewStatus
    weight_variance_pct: str | None
    created_at: datetime
