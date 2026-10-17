"""Weight normalisation, schema validators and reconciliation rules."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from conftest import TENANT, make_bol, make_factura
from triage_engine.schemas.documents import (
    CrossBorderLoadTriagePacket,
    FindingCode,
    TriageDisposition,
)
from triage_engine.schemas.units import (
    PalletType,
    Weight,
    WeightUnit,
    estimate_gross_lb,
    kg_to_lb,
)
from triage_engine.services.reconciliation import (
    build_triage_packet,
    reconcile,
    weight_variance_pct,
)


class TestUnits:
    def test_kg_to_lb_uses_exact_definition(self) -> None:
        assert kg_to_lb(Decimal("1")) == Decimal("2.20")
        assert kg_to_lb(Decimal("1000")) == Decimal("2204.62")
        assert kg_to_lb(Decimal("0.45359237")) == Decimal("1.00")

    @pytest.mark.parametrize("alias", ["kg", "KGS", "KGM", "Kilogramos", "kilos."])
    def test_kilogram_aliases(self, alias: str) -> None:
        assert Weight(value=Decimal("10"), unit=alias).unit is WeightUnit.KG

    @pytest.mark.parametrize("alias", ["lbs", "LBR", "Libras", "pounds"])
    def test_pound_aliases(self, alias: str) -> None:
        assert Weight(value=Decimal("10"), unit=alias).pounds == Decimal("10.00")

    def test_thousands_separator_and_float_inputs(self) -> None:
        assert Weight(value="1,100.5", unit="KG").pounds == Decimal("2426.19")
        assert Weight(value=0.1, unit="LB").value == Decimal("0.1")

    def test_rejects_non_positive_and_nan(self) -> None:
        for bad in ("0", "-5", "NaN", "Infinity"):
            with pytest.raises(ValidationError):
                Weight(value=bad, unit="KG")

    def test_net_weight_grossed_up_with_pallet_tare(self) -> None:
        net = Weight(value=Decimal("1000"), unit="KG")
        gross = estimate_gross_lb(
            gross=None, net=net, pallet_count=10, pallet_type=PalletType.GMA_48X40
        )
        assert gross == Decimal("2204.62") + Decimal("400.00")

    def test_stated_gross_weight_wins(self) -> None:
        gross = Weight(value=Decimal("5000"), unit="LB")
        net = Weight(value=Decimal("1"), unit="KG")
        assert estimate_gross_lb(
            gross=gross, net=net, pallet_count=10, pallet_type=PalletType.UNKNOWN
        ) == Decimal("5000.00")


class TestFacturaValidators:
    def test_valid_factura(self) -> None:
        factura = make_factura()
        assert factura.net_weight_lb == Decimal("25551.58")

    def test_line_items_must_sum_to_subtotal(self) -> None:
        with pytest.raises(ValidationError, match=r"sum to 30000\.00 but subtotal is 31000\.00"):
            make_factura(subtotal="31000.00", total="31000.00")

    def test_line_amount_must_equal_quantity_times_price(self) -> None:
        lines = [
            {"description": "x", "quantity": "10", "unit_value": "3.00", "amount": "40.00"},
        ]
        with pytest.raises(ValidationError, match=r"amount 40\.00 != quantity 10"):
            make_factura(line_items=lines, subtotal="40.00", total="40.00")

    def test_total_checked_when_taxes_known(self) -> None:
        with pytest.raises(ValidationError, match="total"):
            make_factura(taxes_transferred="4800.00", total="30000.00")
        assert make_factura(taxes_transferred="4800.00", total="34800.00").total == Decimal(
            "34800.00"
        )

    def test_line_weights_must_sum_to_net_weight(self) -> None:
        lines = [
            {
                "description": "a",
                "quantity": "1",
                "unit_value": "1",
                "amount": "1",
                "net_weight": {"value": "100", "unit": "KG"},
            },
            {
                "description": "b",
                "quantity": "1",
                "unit_value": "1",
                "amount": "1",
                "net_weight": {"value": "100", "unit": "KG"},
            },
        ]
        with pytest.raises(ValidationError, match="line net weights sum"):
            make_factura(
                line_items=lines,
                subtotal="2",
                total="2",
                net_weight={"value": "250", "unit": "KG"},
            )

    def test_rejects_malformed_rfc(self) -> None:
        with pytest.raises(ValidationError, match="RFC"):
            make_factura(issuer_rfc="NOT-AN-RFC")

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            make_factura(unexpected="x")


class TestBillOfLadingValidators:
    def test_line_counts_must_sum_to_total(self) -> None:
        with pytest.raises(ValidationError, match="sum to 1200 but total is 1201"):
            make_bol(total_package_count=1201)

    def test_line_weights_must_sum_to_total(self) -> None:
        with pytest.raises(ValidationError, match="line weights sum"):
            make_bol(total_gross_weight={"value": "30000", "unit": "LB"})

    def test_effective_counts(self) -> None:
        bol = make_bol(total_package_count=None, pallet_count=None)
        assert bol.effective_package_count == 1200
        assert bol.effective_pallet_count is None


class TestReconciliation:
    def test_matching_documents_clear(self) -> None:
        packet = build_triage_packet(tenant_id=TENANT, factura=make_factura(), bol=make_bol())
        assert packet.disposition is TriageDisposition.CLEAR
        # 11,590 kg net = 25,551.55 lb + 20 GMA pallets x 40 lb tare.
        assert packet.factura_gross_weight_lb == Decimal("26351.58")
        assert packet.bol_gross_weight_lb == Decimal("26350.00")
        assert {f.code for f in packet.findings} == {FindingCode.PALLET_TARE_ESTIMATED}

    def test_weight_variance_over_three_percent_holds(self) -> None:
        factura = make_factura(net_weight={"value": "12500", "unit": "KG"})
        packet = build_triage_packet(tenant_id=TENANT, factura=factura, bol=make_bol())
        assert packet.disposition is TriageDisposition.HOLD
        finding = next(f for f in packet.findings if f.code is FindingCode.WEIGHT_VARIANCE_EXCEEDED)
        assert finding.variance_pct is not None and finding.variance_pct > Decimal("3")

    def test_three_percent_exactly_is_not_flagged(self) -> None:
        assert weight_variance_pct(Decimal("103"), Decimal("100")) == Decimal("3.00")
        factura = make_factura(gross_weight={"value": "103", "unit": "LB"}, net_weight=None)
        bol = make_bol(
            line_items=[{"description": "x", "package_count": 1200, "weight": None}],
            total_gross_weight={"value": "100", "unit": "LB"},
        )
        findings, _ = reconcile(factura, bol)
        assert FindingCode.WEIGHT_VARIANCE_EXCEEDED not in {f.code for f in findings}

    def test_variance_is_symmetric(self) -> None:
        assert weight_variance_pct(Decimal("100"), Decimal("104")) == weight_variance_pct(
            Decimal("104"), Decimal("100")
        )

    def test_bulto_count_mismatch_holds(self) -> None:
        packet = build_triage_packet(
            tenant_id=TENANT, factura=make_factura(package_count=1180), bol=make_bol()
        )
        assert packet.disposition is TriageDisposition.HOLD
        assert FindingCode.PACKAGE_COUNT_MISMATCH in {f.code for f in packet.findings}

    def test_missing_data_routes_to_review(self) -> None:
        factura = make_factura(net_weight=None, package_count=None)
        packet = build_triage_packet(tenant_id=TENANT, factura=factura, bol=make_bol())
        assert packet.disposition is TriageDisposition.REVIEW
        codes = {f.code for f in packet.findings}
        assert {FindingCode.WEIGHT_UNAVAILABLE, FindingCode.PACKAGE_COUNT_UNAVAILABLE} <= codes

    def test_invoice_reference_mismatch_warns(self) -> None:
        packet = build_triage_packet(
            tenant_id=TENANT, factura=make_factura(), bol=make_bol(invoice_references=["Z-9"])
        )
        assert FindingCode.INVOICE_REFERENCE_MISMATCH in {f.code for f in packet.findings}
        assert packet.disposition is TriageDisposition.REVIEW


class TestPacketInvariants:
    def test_disposition_must_match_findings(self) -> None:
        packet = build_triage_packet(
            tenant_id=TENANT, factura=make_factura(package_count=1), bol=make_bol()
        )
        data = packet.model_dump()
        data["disposition"] = "CLEAR"
        with pytest.raises(ValidationError, match="contradicts findings"):
            CrossBorderLoadTriagePacket.model_validate(data)

    def test_raw_driver_license_rejected(self) -> None:
        with pytest.raises(ValidationError, match="tokenised"):
            build_triage_packet(
                tenant_id=TENANT,
                factura=make_factura(),
                bol=make_bol(driver_license_number="BCN0012345"),
            )

    def test_raw_personal_rfc_rejected(self) -> None:
        with pytest.raises(ValidationError, match="tokenised"):
            build_triage_packet(
                tenant_id=TENANT,
                factura=make_factura(issuer_rfc="GOMJ800101AB1"),
                bol=make_bol(),
            )
