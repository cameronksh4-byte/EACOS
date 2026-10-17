"""Strict Pydantic v2 schemas for cross-border shipping documents.

These models serve three purposes at once:

1. They are the ``response_model`` that ``instructor`` hands to Claude, so
   field descriptions double as extraction instructions.
2. Their validators enforce arithmetic invariants (line items summing to the
   totals). When Claude mis-transcribes a number, the ``ValidationError`` is
   fed back to the model and it re-reads the document.
3. They are the persisted, audited representation of each document.

Derived values (pounds, gross-weight estimates) are ``computed_field``s: they
are excluded from the extraction schema, so the model is never asked to do
arithmetic, and they are always recomputed from the stated figures.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from triage_engine.schemas.units import (
    NonNegativeDecimal,
    PalletType,
    PositiveDecimal,
    Weight,
    WeightBasis,
    drop_computed_fields,
    estimate_gross_lb,
    estimate_net_lb,
    within_relative_tolerance,
)
from triage_engine.security.pii import (
    RFC_PERSONA_FISICA_RE,
    is_business_rfc,
    is_personal_identifier,
    is_pii_token,
)

#: Line-item weights may be rounded individually; allow 0.5 % drift vs total.
LINE_WEIGHT_TOLERANCE_PCT = Decimal("0.5")
CENT = Decimal("0.01")


def _validate_rfc(value: str | None) -> str | None:
    """Accept a well-formed RFC or a PII token; reject anything else."""
    if value is None:
        return None
    candidate = value.strip().upper().replace(" ", "").replace("-", "")
    if is_pii_token(value):
        return value
    if is_business_rfc(candidate) or RFC_PERSONA_FISICA_RE.fullmatch(candidate):
        return candidate
    raise ValueError("not a valid Mexican RFC (12 or 13 characters)")


RFC = Annotated[str | None, AfterValidator(_validate_rfc)]


def _coerce_datetime(value: object) -> object:
    """Accept ISO dates without a time part (``2026-10-01``) as midnight."""
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            return value
    return value


DocumentDateTime = Annotated[datetime | None, BeforeValidator(_coerce_datetime)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        populate_by_name=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_computed_fields(cls, data: object) -> object:
        """Let serialised models round-trip: computed fields are re-derived, never trusted."""
        return drop_computed_fields(cls, data)


# ---------------------------------------------------------------------------
# Mexican CFDI 4.0 factura
# ---------------------------------------------------------------------------


class FacturaLineItem(_StrictModel):
    """One ``cfdi:Concepto`` of a factura."""

    description: str = Field(min_length=1, max_length=1000, description="Descripcion.")
    product_key: str | None = Field(
        default=None, pattern=r"^\d{8}$", description="SAT ClaveProdServ (8 digits)."
    )
    tariff_fraction: str | None = Field(
        default=None,
        pattern=r"^\d{8,10}$",
        description="Mexican fraccion arancelaria / HTS code, digits only.",
    )
    quantity: PositiveDecimal = Field(description="Cantidad.")
    unit_code: str | None = Field(
        default=None, max_length=10, description="SAT ClaveUnidad, e.g. H87, KGM."
    )
    unit_value: NonNegativeDecimal = Field(description="ValorUnitario (price per unit).")
    amount: NonNegativeDecimal = Field(description="Importe for the line, before taxes.")
    net_weight: Weight | None = Field(
        default=None, description="Net weight of this line if printed, with its unit."
    )

    @model_validator(mode="after")
    def _amount_matches_quantity_times_price(self) -> FacturaLineItem:
        """Importe must equal Cantidad x ValorUnitario.

        Tolerance is half a cent per unit (unit prices are often printed
        rounded to 2 decimals) plus one cent for the line's own rounding.
        """
        expected = self.quantity * self.unit_value
        tolerance = self.quantity * Decimal("0.005") + CENT
        if abs(expected - self.amount) > tolerance:
            raise ValueError(
                f"line '{self.description[:40]}': amount {self.amount} != "
                f"quantity {self.quantity} x unit_value {self.unit_value}"
            )
        return self


class MexicanFacturaDocument(_StrictModel):
    """A Mexican SAT CFDI 4.0 invoice (factura), optionally with Carta Porte data."""

    document_type: Literal["FACTURA"] = "FACTURA"
    cfdi_version: str | None = Field(default=None, description="CFDI Version, e.g. 4.0.")
    fiscal_uuid: str | None = Field(
        default=None,
        pattern=r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$",
        description="UUID (folio fiscal) from the TimbreFiscalDigital stamp.",
    )
    series: str | None = Field(default=None, max_length=25, description="Serie.")
    folio: str | None = Field(default=None, max_length=40, description="Folio.")
    issued_at: DocumentDateTime = Field(default=None, description="Fecha of the CFDI.")
    issuer_rfc: RFC = Field(default=None, description="Emisor RFC.")
    issuer_name: str | None = Field(default=None, max_length=300)
    receiver_rfc: RFC = Field(default=None, description="Receptor RFC.")
    receiver_name: str | None = Field(default=None, max_length=300)
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 code (MXN, USD).")
    exchange_rate: PositiveDecimal | None = Field(default=None, description="TipoCambio.")
    subtotal: NonNegativeDecimal = Field(description="SubTotal: sum of line amounts.")
    discount: NonNegativeDecimal | None = Field(default=None, description="Descuento.")
    taxes_transferred: NonNegativeDecimal | None = Field(
        default=None, description="TotalImpuestosTrasladados."
    )
    taxes_withheld: NonNegativeDecimal | None = Field(
        default=None, description="TotalImpuestosRetenidos."
    )
    total: NonNegativeDecimal = Field(description="Total.")
    line_items: list[FacturaLineItem] = Field(min_length=1, max_length=2000)

    gross_weight: Weight | None = Field(
        default=None, description="Gross weight if stated (e.g. Carta Porte PesoBrutoTotal)."
    )
    net_weight: Weight | None = Field(default=None, description="Net merchandise weight.")
    package_count: int | None = Field(
        default=None, gt=0, description="Number of bultos / cartons / packages."
    )
    pallet_count: int | None = Field(default=None, gt=0, description="Number of pallets.")
    pallet_type: PalletType = PalletType.UNKNOWN

    @model_validator(mode="after")
    def _line_items_sum_to_subtotal(self) -> MexicanFacturaDocument:
        """SubTotal must equal the sum of every concepto's Importe (SAT rule)."""
        line_sum = sum((line.amount for line in self.line_items), Decimal(0))
        tolerance = CENT * len(self.line_items)
        if abs(line_sum - self.subtotal) > tolerance:
            raise ValueError(f"line item amounts sum to {line_sum} but subtotal is {self.subtotal}")
        return self

    @model_validator(mode="after")
    def _total_is_consistent(self) -> MexicanFacturaDocument:
        """Total = SubTotal - Descuento + traslados - retenciones, when taxes are known."""
        if self.taxes_transferred is None and self.taxes_withheld is None:
            if self.total < self.subtotal - (self.discount or 0):
                raise ValueError("total is lower than subtotal less discount without withholdings")
            return self
        expected = (
            self.subtotal
            - (self.discount or 0)
            + (self.taxes_transferred or 0)
            - (self.taxes_withheld or 0)
        )
        if abs(expected - self.total) > CENT:
            raise ValueError(f"total {self.total} != computed {expected}")
        return self

    @model_validator(mode="after")
    def _weights_are_consistent(self) -> MexicanFacturaDocument:
        """Line weights must sum to the declared net weight; net <= gross."""
        line_weights = [line.net_weight for line in self.line_items]
        if self.net_weight is not None and all(w is not None for w in line_weights):
            total_lb = sum((w.pounds for w in line_weights if w), Decimal(0))
            if not within_relative_tolerance(
                total_lb, self.net_weight.pounds, LINE_WEIGHT_TOLERANCE_PCT
            ):
                raise ValueError(
                    f"line net weights sum to {total_lb} lb but net weight is "
                    f"{self.net_weight.pounds} lb"
                )
        if (
            self.gross_weight
            and self.net_weight
            and self.net_weight.pounds > self.gross_weight.pounds
        ):
            raise ValueError("net weight exceeds gross weight")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def weight_basis(self) -> WeightBasis:
        """Which weight the document actually states."""
        if self.gross_weight:
            return WeightBasis.GROSS
        if self.net_weight or self._line_net_lb() is not None:
            return WeightBasis.NET
        return WeightBasis.UNKNOWN

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_weight_lb(self) -> Decimal | None:
        """Net merchandise weight in pounds (stated, summed, or gross less tare)."""
        net = self.net_weight or self._line_net_weight()
        return estimate_net_lb(
            gross=self.gross_weight,
            net=net,
            pallet_count=self.pallet_count,
            pallet_type=self.pallet_type,
        )

    def gross_weight_lb(
        self, *, pallet_count: int | None = None, pallet_type: PalletType | None = None
    ) -> Decimal | None:
        """Estimated gross weight in pounds, grossing up net weight by pallet tare.

        Args:
            pallet_count: Override when the pallet count is known from
                another document (usually the BOL).
            pallet_type: Override for the pallet footprint.
        """
        return estimate_gross_lb(
            gross=self.gross_weight,
            net=self.net_weight or self._line_net_weight(),
            pallet_count=pallet_count if pallet_count is not None else self.pallet_count,
            pallet_type=pallet_type or self.pallet_type,
        )

    @property
    def references(self) -> set[str]:
        """Identifiers a BOL may cite for this invoice (UUID, folio, serie+folio)."""
        refs: set[str] = set()
        if self.fiscal_uuid:
            refs.add(self.fiscal_uuid.upper())
        if self.folio:
            refs.add(self.folio.upper())
            if self.series:
                refs.add(f"{self.series}{self.folio}".upper())
                refs.add(f"{self.series}-{self.folio}".upper())
        return refs

    def _line_net_weight(self) -> Weight | None:
        lb = self._line_net_lb()
        return Weight(value=lb, unit="LB") if lb else None

    def _line_net_lb(self) -> Decimal | None:
        weights = [line.net_weight for line in self.line_items]
        if not weights or any(w is None for w in weights):
            return None
        return sum((w.pounds for w in weights if w), Decimal(0))


# ---------------------------------------------------------------------------
# Bill of lading
# ---------------------------------------------------------------------------


class PackageType(StrEnum):
    """Handling units as written on US and Mexican BOLs."""

    CARTON = "CARTON"
    BULTO = "BULTO"
    PALLET = "PALLET"
    CRATE = "CRATE"
    DRUM = "DRUM"
    BAG = "BAG"
    ROLL = "ROLL"
    PIECE = "PIECE"
    OTHER = "OTHER"


class BillOfLadingLineItem(_StrictModel):
    """One commodity line of a bill of lading."""

    description: str = Field(min_length=1, max_length=1000)
    package_count: int = Field(gt=0, description="Number of handling units on this line.")
    package_type: PackageType = Field(
        default=PackageType.OTHER, description="CARTON, BULTO, PALLET, ..."
    )
    weight: Weight | None = Field(default=None, description="Line weight with its unit.")
    freight_class: str | None = Field(default=None, max_length=10, description="NMFC class.")
    hazmat: bool = False


class BillOfLadenDocument(_StrictModel):
    """A straight bill of lading (US VICS BOL or Mexican carta de porte copy).

    (The class name follows the integration contract; ``BillOfLadingDocument``
    is provided as an alias.)
    """

    document_type: Literal["BILL_OF_LADING"] = "BILL_OF_LADING"
    bol_number: str = Field(min_length=1, max_length=60)
    scac: str | None = Field(default=None, pattern=r"^[A-Z]{2,4}$", description="Carrier SCAC.")
    pro_number: str | None = Field(default=None, max_length=60)
    issued_at: DocumentDateTime = None
    shipper_name: str | None = Field(default=None, max_length=300)
    shipper_rfc: RFC = None
    consignee_name: str | None = Field(default=None, max_length=300)
    carrier_name: str | None = Field(default=None, max_length=300)
    origin: str | None = Field(default=None, max_length=300)
    destination: str | None = Field(default=None, max_length=300)
    port_of_entry: str | None = Field(
        default=None, max_length=120, description="e.g. Pharr, Hidalgo, Brownsville."
    )
    trailer_number: str | None = Field(default=None, max_length=40)
    seal_numbers: list[str] = Field(default_factory=list, max_length=20)
    driver_license_number: str | None = Field(
        default=None,
        max_length=64,
        description="Driver licence number exactly as printed (tokenised on ingest).",
    )
    invoice_references: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Invoice / factura folio or UUID numbers the BOL references.",
    )
    line_items: list[BillOfLadingLineItem] = Field(min_length=1, max_length=500)
    total_package_count: int | None = Field(
        default=None, gt=0, description="Total handling units / bultos as printed."
    )
    total_gross_weight: Weight | None = Field(
        default=None, description="Total gross (scale) weight as printed."
    )
    pallet_count: int | None = Field(default=None, gt=0)
    pallet_type: PalletType = PalletType.UNKNOWN

    @model_validator(mode="after")
    def _line_counts_sum_to_total(self) -> BillOfLadenDocument:
        """Line handling-unit counts must add up to the printed total."""
        line_total = sum(line.package_count for line in self.line_items)
        if self.total_package_count is not None and line_total != self.total_package_count:
            raise ValueError(
                f"line package counts sum to {line_total} but total is {self.total_package_count}"
            )
        return self

    @model_validator(mode="after")
    def _line_weights_sum_to_total(self) -> BillOfLadenDocument:
        """Line weights must add up to the printed total gross weight."""
        weights = [line.weight for line in self.line_items]
        if self.total_gross_weight is None or any(w is None for w in weights):
            return self
        line_lb = sum((w.pounds for w in weights if w), Decimal(0))
        if not within_relative_tolerance(
            line_lb, self.total_gross_weight.pounds, LINE_WEIGHT_TOLERANCE_PCT
        ):
            raise ValueError(
                f"line weights sum to {line_lb} lb but total gross weight is "
                f"{self.total_gross_weight.pounds} lb"
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_package_count(self) -> int:
        """Printed total, or the sum of the lines when no total is printed."""
        return self.total_package_count or sum(line.package_count for line in self.line_items)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_pallet_count(self) -> int | None:
        """Printed pallet count, or the count of PALLET handling units."""
        if self.pallet_count:
            return self.pallet_count
        pallets = sum(
            line.package_count
            for line in self.line_items
            if line.package_type is PackageType.PALLET
        )
        return pallets or None

    def gross_weight_lb(self) -> Decimal | None:
        """Gross weight in pounds from the total or the summed lines."""
        if self.total_gross_weight is not None:
            return self.total_gross_weight.pounds
        weights = [line.weight for line in self.line_items]
        if weights and all(w is not None for w in weights):
            return sum((w.pounds for w in weights if w), Decimal(0))
        return None


BillOfLadingDocument = BillOfLadenDocument


class ExtractedDocument(_StrictModel):
    """Envelope Claude fills when the document type is not known in advance."""

    document: MexicanFacturaDocument | BillOfLadenDocument = Field(
        discriminator="document_type",
        description="FACTURA for a Mexican CFDI invoice, BILL_OF_LADING for a BOL.",
    )


# ---------------------------------------------------------------------------
# Triage packet
# ---------------------------------------------------------------------------


class FindingSeverity(StrEnum):
    """How strongly a finding affects the disposition."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class FindingCode(StrEnum):
    """Machine-readable reconciliation rule identifiers."""

    WEIGHT_VARIANCE_EXCEEDED = "WEIGHT_VARIANCE_EXCEEDED"
    PACKAGE_COUNT_MISMATCH = "PACKAGE_COUNT_MISMATCH"
    PALLET_COUNT_MISMATCH = "PALLET_COUNT_MISMATCH"
    WEIGHT_UNAVAILABLE = "WEIGHT_UNAVAILABLE"
    PACKAGE_COUNT_UNAVAILABLE = "PACKAGE_COUNT_UNAVAILABLE"
    INVOICE_REFERENCE_MISMATCH = "INVOICE_REFERENCE_MISMATCH"
    PALLET_TARE_ESTIMATED = "PALLET_TARE_ESTIMATED"


class ReconciliationFinding(_StrictModel):
    """One result of comparing the factura with the bill of lading."""

    code: FindingCode
    severity: FindingSeverity
    message: str
    factura_value: str | None = None
    bol_value: str | None = None
    variance_pct: Decimal | None = None


class TriageDisposition(StrEnum):
    """Routing outcome for a load."""

    CLEAR = "CLEAR"  # documents agree; proceed to filing
    REVIEW = "REVIEW"  # data gaps; a broker should look
    HOLD = "HOLD"  # documents disagree; do not file until resolved


def disposition_for(findings: list[ReconciliationFinding]) -> TriageDisposition:
    """Derive the disposition from the most severe finding."""
    severities = {finding.severity for finding in findings}
    if FindingSeverity.CRITICAL in severities:
        return TriageDisposition.HOLD
    if FindingSeverity.WARNING in severities:
        return TriageDisposition.REVIEW
    return TriageDisposition.CLEAR


class CrossBorderLoadTriagePacket(_StrictModel):
    """The reconciled view of one northbound load: factura + BOL + findings."""

    packet_id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    load_reference: str = Field(min_length=1, max_length=120)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    factura: MexicanFacturaDocument
    bill_of_lading: BillOfLadenDocument
    pallet_count_assumed: int | None = Field(default=None, ge=0)
    pallet_type_assumed: PalletType = PalletType.UNKNOWN
    factura_gross_weight_lb: Decimal | None = None
    bol_gross_weight_lb: Decimal | None = None
    weight_variance_pct: Decimal | None = None
    findings: list[ReconciliationFinding] = Field(default_factory=list)
    disposition: TriageDisposition

    @model_validator(mode="after")
    def _disposition_matches_findings(self) -> CrossBorderLoadTriagePacket:
        """A packet can never claim CLEAR while carrying a blocking finding."""
        expected = disposition_for(self.findings)
        if self.disposition is not expected:
            raise ValueError(f"disposition {self.disposition} contradicts findings ({expected})")
        return self

    @model_validator(mode="after")
    def _no_raw_personal_identifiers(self) -> CrossBorderLoadTriagePacket:
        """Defence in depth: personal IDs must be tokenised before packaging."""
        license_number = self.bill_of_lading.driver_license_number
        if license_number and not is_pii_token(license_number):
            raise ValueError("driver licence number must be tokenised")
        for rfc in (
            self.factura.issuer_rfc,
            self.factura.receiver_rfc,
            self.bill_of_lading.shipper_rfc,
        ):
            if rfc and is_personal_identifier(rfc):
                raise ValueError("personal RFC must be tokenised")
        return self
