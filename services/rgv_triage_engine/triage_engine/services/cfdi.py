"""Deterministic parser for Mexican SAT CFDI 4.0 XML invoices.

A stamped CFDI is already structured data, so it is parsed directly instead
of being sent to the LLM. That is cheaper, exact, and keeps the document's
personal data out of any model call.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element

from pydantic import ValidationError

from triage_engine.schemas.documents import FacturaLineItem, MexicanFacturaDocument
from triage_engine.schemas.units import PalletType, Weight
from triage_engine.security.xml_safe import parse_untrusted_xml

CFDI_40_NS = "http://www.sat.gob.mx/cfd/4"
TFD_NS = "http://www.sat.gob.mx/TimbreFiscalDigital"
CARTA_PORTE_NS_PREFIX = "http://www.sat.gob.mx/CartaPorte"
COMERCIO_EXTERIOR_NS_PREFIX = "http://www.sat.gob.mx/ComercioExterior"

#: UN/ECE Rec 21 packaging codes (used in SAT c_ClaveUnidad) that denote a
#: countable handling unit rather than a measure.
PACKAGE_UNIT_CODES = frozenset({"XBX", "XCT", "XPK", "XBG", "XCR", "XBA", "XDR", "XCS", "XRO"})
PALLET_UNIT_CODES = frozenset({"XPX", "XOA", "XOB", "XOC", "XOD"})


class CFDIParseError(ValueError):
    """The XML is not a valid, internally consistent CFDI 4.0 document."""


def _ns(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _dec(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation as exc:
        raise CFDIParseError(f"invalid decimal value {value!r}") from exc


def _iter_complements(root: Element, ns_prefix: str, local_name: str) -> list[Element]:
    return [
        el
        for el in root.iter()
        if _ns(el.tag).startswith(ns_prefix) and _local(el.tag) == local_name
    ]


def parse_cfdi(xml_bytes: bytes) -> MexicanFacturaDocument:
    """Parse and validate a CFDI 4.0 XML file.

    Args:
        xml_bytes: Raw XML exactly as received.

    Returns:
        A validated :class:`MexicanFacturaDocument`.

    Raises:
        triage_engine.security.xml_safe.UnsafeXMLError: XXE, DTDs, entity
            expansion or malformed XML.
        CFDIParseError: Not a CFDI 4.0 comprobante, or its figures do not
            reconcile (e.g. conceptos do not sum to SubTotal).
    """
    root = parse_untrusted_xml(xml_bytes)
    try:
        return _build_document(root)
    except ValidationError as exc:
        raise CFDIParseError(f"CFDI failed validation: {exc.error_count()} error(s)") from exc


def _build_document(root: Element) -> MexicanFacturaDocument:
    """Map a parsed Comprobante element onto the factura schema."""
    if _ns(root.tag) != CFDI_40_NS or _local(root.tag) != "Comprobante":
        raise CFDIParseError("root element is not cfdi:Comprobante (CFDI 4.0 namespace)")
    if root.get("Version") != "4.0":
        raise CFDIParseError(f"unsupported CFDI version {root.get('Version')!r}")

    q = f"{{{CFDI_40_NS}}}"
    emisor = root.find(f"{q}Emisor")
    receptor = root.find(f"{q}Receptor")
    impuestos = root.find(f"{q}Impuestos")
    conceptos = root.findall(f"{q}Conceptos/{q}Concepto")
    if not conceptos:
        raise CFDIParseError("CFDI has no conceptos")

    timbre = _iter_complements(root, TFD_NS, "TimbreFiscalDigital")
    tariffs = _tariff_fractions_by_item(root)

    lines = []
    for concepto in conceptos:
        no_ident = concepto.get("NoIdentificacion")
        lines.append(
            FacturaLineItem(
                description=concepto.get("Descripcion") or "(sin descripcion)",
                product_key=concepto.get("ClaveProdServ"),
                tariff_fraction=tariffs.get(no_ident or ""),
                quantity=_dec(concepto.get("Cantidad")),
                unit_code=concepto.get("ClaveUnidad"),
                unit_value=_dec(concepto.get("ValorUnitario")),
                amount=_dec(concepto.get("Importe")),
            )
        )

    goods_weight, packages, pallets = _carta_porte_logistics(root)

    return MexicanFacturaDocument(
        cfdi_version="4.0",
        fiscal_uuid=timbre[0].get("UUID") if timbre else None,
        series=root.get("Serie"),
        folio=root.get("Folio"),
        issued_at=root.get("Fecha"),
        issuer_rfc=emisor.get("Rfc") if emisor is not None else None,
        issuer_name=emisor.get("Nombre") if emisor is not None else None,
        receiver_rfc=receptor.get("Rfc") if receptor is not None else None,
        receiver_name=receptor.get("Nombre") if receptor is not None else None,
        currency=root.get("Moneda") or "",
        exchange_rate=_dec(root.get("TipoCambio")),
        subtotal=_dec(root.get("SubTotal")),
        discount=_dec(root.get("Descuento")),
        taxes_transferred=_dec(impuestos.get("TotalImpuestosTrasladados"))
        if impuestos is not None
        else None,
        taxes_withheld=_dec(impuestos.get("TotalImpuestosRetenidos"))
        if impuestos is not None
        else None,
        total=_dec(root.get("Total")),
        line_items=lines,
        # Carta Porte PesoBrutoTotal is the weight of the goods themselves;
        # wooden pallets are not merchandise, so it is pallet-exclusive.
        net_weight=goods_weight,
        package_count=packages,
        pallet_count=pallets,
        pallet_type=PalletType.UNKNOWN,
    )


def _tariff_fractions_by_item(root: Element) -> dict[str, str]:
    """Map NoIdentificacion -> FraccionArancelaria from Comercio Exterior."""
    result: dict[str, str] = {}
    for merc in _iter_complements(root, COMERCIO_EXTERIOR_NS_PREFIX, "Mercancia"):
        ident, fraction = merc.get("NoIdentificacion"), merc.get("FraccionArancelaria")
        if ident and fraction:
            result[ident] = fraction
    return result


def _carta_porte_logistics(root: Element) -> tuple[Weight | None, int | None, int | None]:
    """Extract total weight, package count and pallet count from Carta Porte."""
    mercancias = _iter_complements(root, CARTA_PORTE_NS_PREFIX, "Mercancias")
    if not mercancias:
        return None, None, None
    header = mercancias[0]
    weight: Weight | None = None
    total = _dec(header.get("PesoBrutoTotal"))
    if total and total > 0:
        weight = Weight(value=total, unit=header.get("UnidadPeso") or "KGM")

    packages = pallets = 0
    for merc in header:
        if _local(merc.tag) != "Mercancia":
            continue
        unit = (merc.get("ClaveUnidad") or "").upper()
        qty = _dec(merc.get("Cantidad")) or Decimal(0)
        if qty != qty.to_integral_value():
            continue
        if unit in PACKAGE_UNIT_CODES:
            packages += int(qty)
        elif unit in PALLET_UNIT_CODES:
            pallets += int(qty)
    return weight, packages or None, pallets or None
