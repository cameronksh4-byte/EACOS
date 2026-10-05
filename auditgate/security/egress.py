"""Egress filter: check text BEFORE it leaves the system (emails, API responses, exports).

Sanitization protects what goes INTO a model. Egress filtering protects what comes
OUT of it. An injected document might persuade an agent to put a bank account into
a "dispute email" or to include a link to an attacker's site; this catches both,
whatever the model was told.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from auditgate.security.sanitizer import DETECTORS

URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>\"')\]]+", re.I)

# Identifiers that must never appear in outbound text. Emails and phone numbers are
# allowed: a dispute email legitimately addresses the vendor.
BLOCKED_LABELS = {"CREDIT_CARD", "SSN", "BANK_ACCOUNT", "ROUTING_NUMBER", "IBAN", "EIN"}


@dataclass(frozen=True)
class EgressViolation:
    kind: str
    detail: str


def _domain(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    return (parsed.hostname or "").lower().removeprefix("www.")


def check_egress(text: str, allowed_domains: Iterable[str] = ()) -> list[EgressViolation]:
    allowed = {d.lower().removeprefix("www.") for d in allowed_domains}
    violations: list[EgressViolation] = []
    for match in URL_RE.finditer(text):
        domain = _domain(match.group(0))
        if not any(domain == a or domain.endswith(f".{a}") for a in allowed):
            violations.append(EgressViolation("url_not_allowed", f"{domain or match.group(0)}"))
    for det in DETECTORS:
        if det.label not in BLOCKED_LABELS:
            continue
        for m in det.pattern.finditer(text):
            value = m.group(det.group)
            if value and (det.validate is None or det.validate(value)):
                # Never echo the secret itself into logs: show only the last 4 characters.
                violations.append(EgressViolation(f"blocked_{det.label.lower()}", f"…{value[-4:]}"))
    return violations
