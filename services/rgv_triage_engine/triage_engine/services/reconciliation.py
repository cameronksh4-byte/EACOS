"""Cross-document reconciliation rules (factura vs. bill of lading)."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from triage_engine.schemas.documents import (
    BillOfLadenDocument,
    CrossBorderLoadTriagePacket,
    FindingCode,
    FindingSeverity,
    MexicanFacturaDocument,
    ReconciliationFinding,
    disposition_for,
)
from triage_engine.schemas.units import PalletType, WeightBasis

DEFAULT_WEIGHT_VARIANCE_THRESHOLD_PCT = Decimal("3.0")


def weight_variance_pct(a: Decimal, b: Decimal) -> Decimal:
    """Symmetric percentage difference, relative to the *smaller* weight.

    Using the smaller value as the denominator is the conservative choice:
    a shortfall or an overage of the same size produce the same percentage,
    and the rule trips slightly earlier than a mean-based formula would.
    """
    smaller = min(a, b)
    if smaller <= 0:
        return Decimal("100.00")
    return (abs(a - b) / smaller * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def reconcile(
    factura: MexicanFacturaDocument,
    bol: BillOfLadenDocument,
    *,
    threshold_pct: Decimal = DEFAULT_WEIGHT_VARIANCE_THRESHOLD_PCT,
) -> tuple[list[ReconciliationFinding], dict[str, object]]:
    """Compare a factura with a BOL and return findings plus computed metrics.

    Rules:

    * Gross weights differing by more than ``threshold_pct`` -> CRITICAL.
      A factura stating only *net* weight is grossed up with the estimated
      tare of the wooden pallets counted on the BOL (or on the factura).
    * Any carton/bulto count difference -> CRITICAL.
    * Any pallet count difference -> WARNING.
    * The BOL cites invoices but not this one -> WARNING.
    * Weight or package count missing on either side -> WARNING.
    """
    findings: list[ReconciliationFinding] = []

    pallets = bol.effective_pallet_count or factura.pallet_count
    pallet_type = (
        bol.pallet_type if bol.pallet_type is not PalletType.UNKNOWN else factura.pallet_type
    )

    factura_lb = factura.gross_weight_lb(pallet_count=pallets, pallet_type=pallet_type)
    bol_lb = bol.gross_weight_lb()
    variance: Decimal | None = None

    if factura_lb is None or bol_lb is None:
        findings.append(
            ReconciliationFinding(
                code=FindingCode.WEIGHT_UNAVAILABLE,
                severity=FindingSeverity.WARNING,
                message="Weight missing on one document; variance cannot be checked.",
                factura_value=_fmt(factura_lb),
                bol_value=_fmt(bol_lb),
            )
        )
    else:
        if factura.weight_basis is WeightBasis.NET and pallets:
            findings.append(
                ReconciliationFinding(
                    code=FindingCode.PALLET_TARE_ESTIMATED,
                    severity=FindingSeverity.INFO,
                    message=(
                        f"Factura states net weight; added estimated tare for {pallets} "
                        f"{pallet_type.value} wooden pallet(s)."
                    ),
                )
            )
        variance = weight_variance_pct(factura_lb, bol_lb)
        if variance > threshold_pct:
            findings.append(
                ReconciliationFinding(
                    code=FindingCode.WEIGHT_VARIANCE_EXCEEDED,
                    severity=FindingSeverity.CRITICAL,
                    message=(
                        f"Gross weight variance {variance}% exceeds {threshold_pct}% "
                        f"(factura {factura_lb} lb vs BOL {bol_lb} lb)."
                    ),
                    factura_value=_fmt(factura_lb),
                    bol_value=_fmt(bol_lb),
                    variance_pct=variance,
                )
            )

    bol_packages = bol.effective_package_count
    if factura.package_count is None:
        findings.append(
            ReconciliationFinding(
                code=FindingCode.PACKAGE_COUNT_UNAVAILABLE,
                severity=FindingSeverity.WARNING,
                message="Factura does not state a carton/bulto count.",
                bol_value=str(bol_packages),
            )
        )
    elif factura.package_count != bol_packages:
        findings.append(
            ReconciliationFinding(
                code=FindingCode.PACKAGE_COUNT_MISMATCH,
                severity=FindingSeverity.CRITICAL,
                message=(
                    f"Carton/bulto count mismatch: factura {factura.package_count} "
                    f"vs BOL {bol_packages}."
                ),
                factura_value=str(factura.package_count),
                bol_value=str(bol_packages),
            )
        )

    if (
        factura.pallet_count
        and bol.effective_pallet_count
        and factura.pallet_count != bol.effective_pallet_count
    ):
        findings.append(
            ReconciliationFinding(
                code=FindingCode.PALLET_COUNT_MISMATCH,
                severity=FindingSeverity.WARNING,
                message=(
                    f"Pallet count mismatch: factura {factura.pallet_count} "
                    f"vs BOL {bol.effective_pallet_count}."
                ),
                factura_value=str(factura.pallet_count),
                bol_value=str(bol.effective_pallet_count),
            )
        )

    cited = {ref.strip().upper() for ref in bol.invoice_references if ref.strip()}
    if cited and factura.references and not (cited & factura.references):
        findings.append(
            ReconciliationFinding(
                code=FindingCode.INVOICE_REFERENCE_MISMATCH,
                severity=FindingSeverity.WARNING,
                message="BOL cites invoice numbers that do not match this factura.",
                factura_value=", ".join(sorted(factura.references)),
                bol_value=", ".join(sorted(cited)),
            )
        )

    metrics: dict[str, object] = {
        "pallet_count_assumed": pallets,
        "pallet_type_assumed": pallet_type,
        "factura_gross_weight_lb": factura_lb,
        "bol_gross_weight_lb": bol_lb,
        "weight_variance_pct": variance,
    }
    return findings, metrics


def build_triage_packet(
    *,
    tenant_id: UUID,
    factura: MexicanFacturaDocument,
    bol: BillOfLadenDocument,
    threshold_pct: Decimal = DEFAULT_WEIGHT_VARIANCE_THRESHOLD_PCT,
) -> CrossBorderLoadTriagePacket:
    """Reconcile two documents and assemble the triage packet.

    Raises:
        pydantic.ValidationError: If personal identifiers were not tokenised
            before the packet was assembled.
    """
    findings, metrics = reconcile(factura, bol, threshold_pct=threshold_pct)
    return CrossBorderLoadTriagePacket(
        tenant_id=tenant_id,
        load_reference=bol.bol_number,
        factura=factura,
        bill_of_lading=bol,
        findings=findings,
        disposition=disposition_for(findings),
        **metrics,
    )


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else f"{value} lb"
