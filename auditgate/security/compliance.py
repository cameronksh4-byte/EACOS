"""Deterministic compliance rules that sit above the LLM.

Statutory and policy checks (prompt-payment deadlines, bidding thresholds, notice
deadlines) are plain Python over validated data. No model is consulted, and no
model output can change their result.

Every rule carries its source: jurisdiction, citation, URL, and who verified it
and when. **This module ships with rule templates, not legal values.** You supply the
numbers from the actual statute or contract. A rule nobody has verified still runs,
but its findings are capped at WARNING and labelled UNVERIFIED, so it can never block
a payment on its own.

Example (values illustrative - look up your jurisdiction's actual statute):

    source = RuleSource(jurisdiction="<state>", citation="<statute section>",
                        url="<official statute URL>", verified_by="J. Smith, counsel",
                        verified_on=date(2026, 1, 15))
    rules = RuleSet([payment_deadline_rule("PROMPT_PAY", days=<N>, source=source)])
    report = process_text(text, rules=rules)
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from auditgate.extraction.schemas import AuditFinding, DocumentType, Severity


@dataclass(frozen=True)
class RuleSource:
    jurisdiction: str
    citation: str
    url: str | None = None
    verified_by: str | None = None
    verified_on: date | None = None

    @property
    def verified(self) -> bool:
        return bool(self.verified_by and self.verified_on)


@dataclass(frozen=True)
class ComplianceContext:
    today: date = field(default_factory=date.today)
    facts: Mapping[str, Any] = field(default_factory=dict)  # e.g. {"notices_sent": {"WO-1"}}


Check = Callable[[BaseModel, ComplianceContext], str | None]


@dataclass(frozen=True)
class ComplianceRule:
    id: str
    title: str
    applies_to: DocumentType
    source: RuleSource
    check: Check
    severity: Severity = Severity.ERROR
    parameters: tuple[tuple[str, str], ...] = ()  # recorded for the audit trail


class RuleSet:
    """An immutable, fingerprinted collection of rules. The fingerprint changes if any rule does."""

    def __init__(self, rules: Iterable[ComplianceRule] = ()) -> None:
        self._rules = tuple(rules)
        ids = [r.id for r in self._rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule id")

    def __iter__(self):
        return iter(self._rules)

    def __len__(self) -> int:
        return len(self._rules)

    @property
    def fingerprint(self) -> str:
        parts = [f"{r.id}|{r.source.citation}|{r.source.verified_on}|{r.parameters}" for r in self._rules]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]

    def evaluate(self, doc_type: DocumentType, doc: BaseModel, context: ComplianceContext) -> list[AuditFinding]:
        findings = []
        for rule in self._rules:
            if rule.applies_to is not doc_type:
                continue
            message = rule.check(doc, context)
            if message is None:
                continue
            cite = f"[{rule.source.jurisdiction}: {rule.source.citation}]"
            if rule.source.verified:
                findings.append(AuditFinding(code=f"RULE_{rule.id}", severity=rule.severity,
                                             message=f"{rule.title}: {message} {cite}"))
            else:
                findings.append(AuditFinding(
                    code=f"RULE_{rule.id}", severity=min(rule.severity, Severity.WARNING, key=_rank),
                    message=f"[UNVERIFIED RULE - confirm against the source before relying on it] "
                            f"{rule.title}: {message} {cite}"))
        return findings


def _rank(severity: Severity) -> int:
    return [Severity.INFO, Severity.WARNING, Severity.ERROR].index(severity)


# --------------------------------------------------------------------------- templates


def payment_deadline_rule(rule_id: str, days: int, source: RuleSource, *,
                          title: str = "Payment deadline") -> ComplianceRule:
    """Invoice must be paid within ``days`` of its invoice date (e.g. a prompt-payment statute or contract term)."""

    def check(doc: BaseModel, ctx: ComplianceContext) -> str | None:
        issued = getattr(doc, "invoice_date", None)
        if issued is None:
            return None
        paid = doc.invoice_number in ctx.facts.get("paid_invoices", set())  # type: ignore[attr-defined]
        deadline = issued + timedelta(days=days)
        if not paid and ctx.today > deadline:
            return f"due by {deadline} ({days} days after {issued}); {(ctx.today - deadline).days} days late"
        return None

    return ComplianceRule(rule_id, title, DocumentType.INVOICE, source, check, parameters=(("days", str(days)),))


def amount_threshold_rule(rule_id: str, threshold: Decimal | str, source: RuleSource, *, requirement: str,
                          applies_to: DocumentType = DocumentType.BID, title: str = "Amount threshold",
                          severity: Severity = Severity.WARNING) -> ComplianceRule:
    """Totals above ``threshold`` need something extra (competitive bids, board approval, a second signer)."""
    limit = Decimal(str(threshold))

    def check(doc: BaseModel, ctx: ComplianceContext) -> str | None:
        total = getattr(doc, "total_bid", None) or getattr(doc, "total", None)
        if total is not None and total > limit and doc_id(doc) not in ctx.facts.get("requirement_met", set()):
            return f"total {total} exceeds {limit}: {requirement}"
        return None

    return ComplianceRule(rule_id, title, applies_to, source, check, severity=severity,
                          parameters=(("threshold", str(limit)), ("requirement", requirement)))


def notice_deadline_rule(rule_id: str, days: int, source: RuleSource, *, date_field: str = "scheduled_date",
                         title: str = "Notice deadline") -> ComplianceRule:
    """A notice (e.g. a preliminary lien notice) must be sent within ``days`` of the work date."""

    def check(doc: BaseModel, ctx: ComplianceContext) -> str | None:
        start = getattr(doc, date_field, None)
        if start is None or doc_id(doc) in ctx.facts.get("notices_sent", set()):
            return None
        deadline = start + timedelta(days=days)
        if ctx.today > deadline:
            return f"notice was due by {deadline} ({days} days after {date_field} {start}) and none is recorded"
        return None

    return ComplianceRule(rule_id, title, DocumentType.WORK_ORDER, source, check,
                          parameters=(("days", str(days)), ("date_field", date_field)))


def doc_id(doc: BaseModel) -> str:
    for attr in ("invoice_number", "work_order_number", "project_name"):
        if getattr(doc, attr, None):
            return str(getattr(doc, attr))
    return ""
