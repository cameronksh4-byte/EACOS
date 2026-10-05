"""Pydantic schemas for the documents AuditGate understands, plus deterministic audit rules.

Design rule (the most important idea in this file):

    Schemas describe WHAT IS ON THE PAGE. Audits judge WHETHER IT ADDS UP.

We deliberately do NOT put "line items must sum to the total" inside a validator.
Instructor feeds validation errors back to the model and asks it to try again, so
an arithmetic validator would pressure the model into *inventing* numbers that
balance. Instead, extraction captures the document faithfully and the `audit()`
methods below flag discrepancies for a human. That is how AuditGate catches the
vendor who overbilled you instead of hiding it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
NonNegMoney = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]

# Allow a cent of rounding slack per comparison.
TOLERANCE = Decimal("0.01")


class DocumentType(StrEnum):
    INVOICE = "invoice"
    BID = "bid"
    WORK_ORDER = "work_order"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AuditFinding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier, e.g. TOTAL_MISMATCH")
    severity: Severity
    message: str


class _Doc(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    def audit(self) -> list[AuditFinding]:  # pragma: no cover - overridden
        return []


def _currency(value: str) -> str:
    value = value.upper()
    if len(value) != 3 or not value.isalpha():
        raise ValueError("currency must be a 3-letter ISO 4217 code, e.g. USD")
    return value


# --------------------------------------------------------------------------- invoice


class LineItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    unit_price: Money
    amount: Money = Field(description="Line total exactly as printed on the document")


class Invoice(_Doc):
    """A bill from a vendor."""

    vendor_name: str = Field(min_length=1)
    invoice_number: str = Field(min_length=1)
    invoice_date: date | None = None
    due_date: date | None = None
    bill_to: str | None = Field(default=None, description="Customer / contact being billed")
    currency: str = "USD"
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Money | None = None
    tax: NonNegMoney = Decimal("0.00")
    total: Money

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return _currency(v)

    def audit(self) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        for i, item in enumerate(self.line_items, start=1):
            expected = (item.quantity * item.unit_price).quantize(Decimal("0.01"))
            if abs(expected - item.amount) > TOLERANCE:
                findings.append(AuditFinding(
                    code="LINE_MATH_MISMATCH", severity=Severity.ERROR,
                    message=f"Line {i} '{item.description}': {item.quantity} x {item.unit_price} "
                            f"= {expected}, but document says {item.amount}.",
                ))
        if self.line_items:
            items_sum = sum((li.amount for li in self.line_items), Decimal("0"))
            if self.subtotal is not None and abs(items_sum - self.subtotal) > TOLERANCE:
                findings.append(AuditFinding(
                    code="SUBTOTAL_MISMATCH", severity=Severity.ERROR,
                    message=f"Line items sum to {items_sum}, but subtotal is {self.subtotal}.",
                ))
        base = self.subtotal if self.subtotal is not None else (
            sum((li.amount for li in self.line_items), Decimal("0")) if self.line_items else None
        )
        if base is not None and abs(base + self.tax - self.total) > TOLERANCE:
            findings.append(AuditFinding(
                code="TOTAL_MISMATCH", severity=Severity.ERROR,
                message=f"Subtotal {base} + tax {self.tax} = {base + self.tax}, but total is {self.total}.",
            ))
        if self.invoice_date and self.due_date and self.due_date < self.invoice_date:
            findings.append(AuditFinding(
                code="DUE_BEFORE_ISSUE", severity=Severity.ERROR,
                message=f"Due date {self.due_date} is before invoice date {self.invoice_date}.",
            ))
        if self.invoice_date is None:
            findings.append(AuditFinding(code="MISSING_DATE", severity=Severity.WARNING,
                                         message="Invoice date not found on document."))
        if not self.line_items:
            findings.append(AuditFinding(code="NO_LINE_ITEMS", severity=Severity.WARNING,
                                         message="No itemized charges; total cannot be verified."))
        if self.total <= 0:
            findings.append(AuditFinding(code="NON_POSITIVE_TOTAL", severity=Severity.WARNING,
                                         message=f"Total is {self.total}; confirm this is a credit memo."))
        return findings


# --------------------------------------------------------------------------- bid / quote


class BidItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    description: str = Field(min_length=1)
    amount: NonNegMoney


class Bid(_Doc):
    """A quote/proposal from a contractor or supplier."""

    bidder_name: str = Field(min_length=1)
    project_name: str = Field(min_length=1)
    bid_date: date | None = None
    valid_until: date | None = None
    contact: str | None = None
    currency: str = "USD"
    scope_items: list[BidItem] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    total_bid: NonNegMoney

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return _currency(v)

    def audit(self) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        if self.scope_items:
            items_sum = sum((s.amount for s in self.scope_items), Decimal("0"))
            if abs(items_sum - self.total_bid) > TOLERANCE:
                findings.append(AuditFinding(
                    code="BID_TOTAL_MISMATCH", severity=Severity.ERROR,
                    message=f"Scope items sum to {items_sum}, but total bid is {self.total_bid}.",
                ))
        else:
            findings.append(AuditFinding(code="NO_SCOPE_ITEMS", severity=Severity.WARNING,
                                         message="Bid has no itemized scope; compare carefully."))
        if self.bid_date and self.valid_until and self.valid_until < self.bid_date:
            findings.append(AuditFinding(
                code="EXPIRED_ON_ARRIVAL", severity=Severity.ERROR,
                message=f"Valid-until {self.valid_until} is before bid date {self.bid_date}.",
            ))
        if self.valid_until is None:
            findings.append(AuditFinding(code="NO_EXPIRY", severity=Severity.INFO,
                                         message="No validity period stated; pricing may change."))
        return findings


# --------------------------------------------------------------------------- work order


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EMERGENCY = "emergency"


class WorkOrder(_Doc):
    """An internal or customer work order / service ticket."""

    work_order_number: str = Field(min_length=1)
    customer_name: str = Field(min_length=1)
    site_address: str | None = None
    requested_date: date | None = None
    scheduled_date: date | None = None
    priority: Priority = Priority.NORMAL
    tasks: list[str] = Field(default_factory=list)
    estimated_hours: Decimal | None = Field(default=None, ge=0)
    not_to_exceed: NonNegMoney | None = None

    @field_validator("priority", mode="before")
    @classmethod
    def _normalize_priority(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v

    def audit(self) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        if not self.tasks:
            findings.append(AuditFinding(code="NO_TASKS", severity=Severity.ERROR,
                                         message="Work order has no tasks listed."))
        if self.requested_date and self.scheduled_date and self.scheduled_date < self.requested_date:
            findings.append(AuditFinding(
                code="SCHEDULED_BEFORE_REQUEST", severity=Severity.WARNING,
                message=f"Scheduled {self.scheduled_date} is before request {self.requested_date}.",
            ))
        if self.priority is Priority.EMERGENCY and self.not_to_exceed is None:
            findings.append(AuditFinding(code="EMERGENCY_NO_CAP", severity=Severity.WARNING,
                                         message="Emergency work with no not-to-exceed amount."))
        if self.site_address is None:
            findings.append(AuditFinding(code="NO_SITE_ADDRESS", severity=Severity.WARNING,
                                         message="No site address; technician cannot be dispatched."))
        return findings


SCHEMAS: dict[DocumentType, type[_Doc]] = {
    DocumentType.INVOICE: Invoice,
    DocumentType.BID: Bid,
    DocumentType.WORK_ORDER: WorkOrder,
}
