"""Detection and keyed tokenisation of Mexican personal identifiers.

Covered identifiers:

* **CURP** (Clave Única de Registro de Población) - always personal.
* **RFC of a persona física** (13 characters) - personal. RFCs of a persona
  moral (12 characters, a company) are business identifiers that customs
  filings need, so they are left intact. The SAT generic RFCs
  ``XAXX010101000`` / ``XEXX010101000`` identify no one and are also kept.
* **Driver licence numbers** (state licences and the federal *Licencia
  Federal de Conductor*). Formats vary by issuing state, so they are found by
  their label ("licencia", "driver license", "CDL", ...) or by field name.

Each match is replaced by ``PII:<KIND>:<16 hex>``, an HMAC-SHA256 of the
normalised value keyed with a server secret and scoped to the tenant. The
token is stable, so the same driver can be correlated across documents
within one tenant, but it cannot be reversed or brute-forced without the key
and cannot be joined across tenants.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any
from uuid import UUID


class PIIKind(StrEnum):
    """Kinds of personal identifier this module tokenises."""

    CURP = "CURP"
    RFC = "RFC"
    LICENSE = "LICENSE"
    PHONE = "PHONE"  # WhatsApp sender numbers
    EMAIL = "EMAIL"  # envelope senders


_STATES = (
    "AS|BC|BS|CC|CL|CM|CS|CH|DF|DG|GT|GR|HG|JC|MC|MN|MS|NT|NL|OC|PL|"
    "QT|QR|SP|SL|SR|TC|TS|TL|VZ|YN|ZS|NE"
)
_DATE = r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"

CURP_RE = re.compile(
    rf"(?<![A-Z0-9])[A-Z][AEIOUX][A-Z]{{2}}{_DATE}[HMX](?:{_STATES})[B-DF-HJ-NP-TV-Z]{{3}}[A-Z0-9]\d(?![A-Z0-9])",
    re.IGNORECASE,
)
#: Persona física: 4 letters, YYMMDD, 3-character homoclave.
RFC_PERSONA_FISICA_RE = re.compile(
    rf"(?<![A-Z0-9Ñ&])[A-ZÑ&]{{4}}{_DATE}[A-Z0-9]{{2}}[A0-9](?![A-Z0-9])",
    re.IGNORECASE,
)
#: Persona moral: 3 letters, YYMMDD, 3-character homoclave.
RFC_PERSONA_MORAL_RE = re.compile(
    rf"^[A-ZÑ&]{{3}}{_DATE}[A-Z0-9]{{2}}[A0-9]$",
    re.IGNORECASE,
)
GENERIC_RFCS = frozenset({"XAXX010101000", "XEXX010101000"})

LICENSE_RE = re.compile(
    r"(?P<label>\b(?:licencia(?:\s+federal)?(?:\s+de\s+(?:conducir|manejo|conductor))?"
    r"|lic\.\s*fed\.?|driver'?s?\s+licen[cs]e|\bCDL\b|\bLFC\b)"
    r"(?:\s*(?:no\.?|n[uú]m(?:ero)?\.?|number|#))?\s*[:#.\-]?\s*)"
    r"(?P<value>(?=[A-Z0-9\-]*\d)[A-Z0-9][A-Z0-9\-]{4,19})",
    re.IGNORECASE,
)

TOKEN_RE = re.compile(r"^PII:(?:CURP|RFC|LICENSE|PHONE|EMAIL):[0-9a-f]{16}$")

#: JSON keys whose values are personal identifiers regardless of format.
_SENSITIVE_KEY_RE = re.compile(
    r"(licen[cs]e|licencia|curp|driver_?id|operator_?id|rfc_?(?:personal|fisica|driver|conductor))",
    re.IGNORECASE,
)


def is_pii_token(value: str) -> bool:
    """True if ``value`` is already a token produced by :class:`PIIMasker`."""
    return bool(TOKEN_RE.match(value))


def is_personal_identifier(value: str) -> bool:
    """True for a raw CURP or persona-física RFC (generic SAT RFCs excluded)."""
    candidate = value.strip().upper()
    if is_pii_token(value) or candidate in GENERIC_RFCS:
        return False
    return bool(CURP_RE.fullmatch(candidate) or RFC_PERSONA_FISICA_RE.fullmatch(candidate))


def is_business_rfc(value: str) -> bool:
    """True for persona-moral or SAT generic RFCs, which are not personal data."""
    candidate = value.strip().upper()
    return candidate in GENERIC_RFCS or bool(RFC_PERSONA_MORAL_RE.match(candidate))


class PIIMasker:
    """Tokenises Mexican personal identifiers in text and structured data.

    Args:
        key: HMAC key (at least 32 bytes). Load it from a secrets manager.
        tenant_id: Tenant the tokens are scoped to.

    Raises:
        ValueError: If the key is too short to resist brute force.
    """

    def __init__(self, key: bytes, tenant_id: UUID | None) -> None:
        if len(key) < 32:
            raise ValueError("PII HMAC key must be at least 32 bytes")
        self._key = key
        self._scope = str(tenant_id) if tenant_id else "global"

    def token(self, kind: PIIKind, raw: str) -> str:
        """Return the deterministic token for one identifier."""
        normalised = re.sub(r"[\s\-]", "", raw).upper()
        digest = hmac.new(
            self._key, f"{self._scope}|{kind.value}|{normalised}".encode(), hashlib.sha256
        ).hexdigest()
        return f"PII:{kind.value}:{digest[:16]}"

    def mask_text(self, text: str) -> str:
        """Replace every CURP, personal RFC and labelled licence number in ``text``."""
        if not text:
            return text
        # CURP first: its first 10 characters look like an RFC.
        text = CURP_RE.sub(lambda m: self.token(PIIKind.CURP, m.group(0)), text)
        text = RFC_PERSONA_FISICA_RE.sub(self._rfc_sub, text)
        return LICENSE_RE.sub(
            lambda m: m.group("label") + self.token(PIIKind.LICENSE, m.group("value")), text
        )

    def mask_identifier(self, value: str | None) -> str | None:
        """Mask a value known to be an RFC/CURP field, keeping business RFCs."""
        if value is None or is_pii_token(value) or is_business_rfc(value):
            return value
        candidate = value.strip()
        if CURP_RE.fullmatch(candidate):
            return self.token(PIIKind.CURP, candidate)
        if RFC_PERSONA_FISICA_RE.fullmatch(candidate):
            return self.token(PIIKind.RFC, candidate)
        return self.mask_text(candidate)

    def mask_structure(self, data: Any) -> Any:
        """Recursively mask a JSON-like structure.

        Values under sensitive keys (``driver_license``, ``curp``...) are
        tokenised whatever their format; every other string is scanned.
        """
        if isinstance(data, Mapping):
            masked: dict[str, Any] = {}
            for key, value in data.items():
                if isinstance(value, str) and _SENSITIVE_KEY_RE.search(str(key)) and value:
                    kind = PIIKind.CURP if "curp" in str(key).lower() else PIIKind.LICENSE
                    masked[key] = value if is_pii_token(value) else self.token(kind, value)
                else:
                    masked[key] = self.mask_structure(value)
            return masked
        if isinstance(data, list):
            return [self.mask_structure(item) for item in data]
        if isinstance(data, str):
            return self.mask_text(data)
        return data

    def _rfc_sub(self, match: re.Match[str]) -> str:
        value = match.group(0)
        preceding = match.string[max(0, match.start() - 12) : match.start()]
        if value.upper() in GENERIC_RFCS or re.search(r"PII:[A-Z]+:$", preceding):
            return value
        return self.token(PIIKind.RFC, value)


def redact_for_logs(text: str) -> str:
    """Irreversibly redact identifiers for log output (no key required)."""
    text = CURP_RE.sub("PII:CURP:REDACTED", text)
    text = RFC_PERSONA_FISICA_RE.sub(
        lambda m: m.group(0) if m.group(0).upper() in GENERIC_RFCS else "PII:RFC:REDACTED", text
    )
    return LICENSE_RE.sub(lambda m: m.group("label") + "PII:LICENSE:REDACTED", text)
