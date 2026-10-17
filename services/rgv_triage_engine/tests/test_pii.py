"""PII detection and tokenisation."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from conftest import PII_KEY, TENANT, make_bol, make_factura
from triage_engine.logging_config import PIIRedactionFilter
from triage_engine.security.pii import PIIKind, PIIMasker, is_pii_token, redact_for_logs
from triage_engine.services.ingestion import IngestionSanitizer

CURP = "GOMJ800101HTSMRN09"  # synthetic, born in Tamaulipas
PERSONAL_RFC = "GOMJ800101AB1"
COMPANY_RFC = "EXP010101AB1"


@pytest.fixture
def masker() -> PIIMasker:
    return PIIMasker(PII_KEY.encode(), TENANT)


def test_masks_curp_and_personal_rfc(masker: PIIMasker) -> None:
    text = f"Operador CURP {CURP}, RFC {PERSONAL_RFC}; exportador {COMPANY_RFC}"
    masked = masker.mask_text(text)
    assert CURP not in masked and PERSONAL_RFC not in masked
    assert COMPANY_RFC in masked
    assert masked.count("PII:CURP:") == 1 and masked.count("PII:RFC:") == 1


def test_keeps_sat_generic_rfcs(masker: PIIMasker) -> None:
    assert masker.mask_text("RFC XAXX010101000 y XEXX010101000") == (
        "RFC XAXX010101000 y XEXX010101000"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Licencia federal de conductor No. BCN0012345",
        "licencia de conducir: TAM-123456",
        "Driver's License # D1234567",
        "CDL 99887766",
    ],
)
def test_masks_labelled_license_numbers(masker: PIIMasker, text: str) -> None:
    masked = masker.mask_text(text)
    assert "PII:LICENSE:" in masked
    assert not any(token in masked for token in ("BCN0012345", "123456", "D1234567", "99887766"))


def test_license_label_without_number_untouched(masker: PIIMasker) -> None:
    assert masker.mask_text("licencia vigente") == "licencia vigente"


def test_tokens_are_deterministic_and_tenant_scoped() -> None:
    a = PIIMasker(PII_KEY.encode(), TENANT)
    b = PIIMasker(PII_KEY.encode(), uuid4())
    assert a.token(PIIKind.CURP, CURP) == a.token(PIIKind.CURP, CURP.lower())
    assert a.token(PIIKind.CURP, CURP) != b.token(PIIKind.CURP, CURP)
    assert is_pii_token(a.token(PIIKind.RFC, PERSONAL_RFC))


def test_masking_is_idempotent(masker: PIIMasker) -> None:
    once = masker.mask_text(f"CURP {CURP} RFC {PERSONAL_RFC} licencia BCN0012345")
    assert masker.mask_text(once) == once


def test_sensitive_keys_masked_whatever_format(masker: PIIMasker) -> None:
    data = {"driver_license": "1234", "nested": [{"curp": "x"}], "note": f"RFC {PERSONAL_RFC}"}
    masked = masker.mask_structure(data)
    assert masked["driver_license"].startswith("PII:LICENSE:")
    assert masked["nested"][0]["curp"].startswith("PII:CURP:")
    assert PERSONAL_RFC not in masked["note"]


def test_short_key_rejected() -> None:
    with pytest.raises(ValueError):
        PIIMasker(b"short", None)


def test_sanitize_document_tokenises_identifiers() -> None:
    sanitizer = IngestionSanitizer(PII_KEY.encode())
    factura = sanitizer.sanitize_document(TENANT, make_factura(issuer_rfc=PERSONAL_RFC))
    bol = sanitizer.sanitize_document(
        TENANT,
        make_bol(driver_license_number="BCN0012345", carrier_name=f"Juan, CURP {CURP}"),
    )
    assert factura.issuer_rfc is not None and factura.issuer_rfc.startswith("PII:RFC:")
    assert factura.receiver_rfc == "XEXX010101000"
    assert bol.driver_license_number is not None and is_pii_token(bol.driver_license_number)
    assert CURP not in (bol.carrier_name or "")
    assert make_factura(issuer_rfc=COMPANY_RFC).issuer_rfc == COMPANY_RFC


def test_log_filter_redacts() -> None:
    record = logging.LogRecord("x", logging.INFO, "", 0, "driver %s", (CURP,), None)
    PIIRedactionFilter().filter(record)
    assert CURP not in record.getMessage()
    assert "PII:CURP:REDACTED" in redact_for_logs(CURP)
