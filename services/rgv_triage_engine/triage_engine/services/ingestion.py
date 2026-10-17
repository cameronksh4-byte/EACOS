"""Ingestion sanitiser: PII tokenisation applied to everything that enters the system.

This runs as a pipeline stage directly after a webhook is authenticated, not
as a body-rewriting ASGI middleware. Signature checks (HMAC, DKIM) must see
the exact original bytes, so rewriting the body before the handler would
break them. Everything after authentication (persistence, the audit trail,
logs and LLM prompts) only ever sees tokenised data.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from triage_engine.schemas.documents import BillOfLadenDocument, MexicanFacturaDocument
from triage_engine.security.pii import PIIMasker


class IngestionSanitizer:
    """Builds tenant-scoped maskers and applies them to inbound data.

    Args:
        hmac_key: Secret key for PII tokens (>= 32 bytes).
    """

    def __init__(self, hmac_key: bytes) -> None:
        if len(hmac_key) < 32:
            raise ValueError("PII HMAC key must be at least 32 bytes")
        self._key = hmac_key

    def masker(self, tenant_id: UUID | None) -> PIIMasker:
        """Return a masker whose tokens are scoped to ``tenant_id``."""
        return PIIMasker(self._key, tenant_id)

    def sanitize_payload(self, tenant_id: UUID | None, payload: Any) -> Any:
        """Tokenise identifiers anywhere in a JSON-like webhook payload."""
        return self.masker(tenant_id).mask_structure(payload)

    def sanitize_text(self, tenant_id: UUID | None, text: str) -> str:
        """Tokenise identifiers in free text (captions, e-mail bodies)."""
        return self.masker(tenant_id).mask_text(text)

    def sanitize_document(
        self, tenant_id: UUID, document: MexicanFacturaDocument | BillOfLadenDocument
    ) -> MexicanFacturaDocument | BillOfLadenDocument:
        """Tokenise personal identifiers in an extracted document.

        Business (persona moral) RFCs are kept because customs filings need
        them; personal RFCs, CURPs and licence numbers are tokenised.
        Free-text fields are scanned as well, since a driver's CURP can turn
        up in a description or address line.
        """
        masker = self.masker(tenant_id)
        data = document.model_dump(mode="json", exclude_computed_fields=True)
        for field in ("issuer_rfc", "receiver_rfc", "shipper_rfc"):
            if data.get(field):
                data[field] = masker.mask_identifier(data[field])
        if data.get("driver_license_number"):
            raw = data["driver_license_number"]
            data["driver_license_number"] = masker.mask_structure({"driver_license_number": raw})[
                "driver_license_number"
            ]
        data = _mask_free_text(data, masker)
        return type(document).model_validate(data)


_STRUCTURAL_KEYS = frozenset(
    {
        "document_type",
        "currency",
        "unit",
        "unit_code",
        "product_key",
        "tariff_fraction",
        "fiscal_uuid",
        "package_type",
        "pallet_type",
        "issued_at",
    }
)


def _mask_free_text(data: Any, masker: PIIMasker) -> Any:
    if isinstance(data, dict):
        return {
            key: value if key in _STRUCTURAL_KEYS else _mask_free_text(value, masker)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_mask_free_text(item, masker) for item in data]
    if isinstance(data, str):
        return masker.mask_text(data)
    return data
