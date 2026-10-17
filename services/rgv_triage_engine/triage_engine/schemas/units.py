"""Weight units, metric-to-imperial normalisation and pallet tare estimates.

All arithmetic uses :class:`decimal.Decimal`; binary floats are never used for
weights that end up on customs paperwork.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

#: Exact by international definition (1959): 1 lb = 0.45359237 kg.
KG_PER_LB = Decimal("0.45359237")
LB_QUANTUM = Decimal("0.01")


class WeightUnit(StrEnum):
    """Canonical weight units."""

    KG = "KG"
    LB = "LB"


#: Spellings seen on Mexican facturas, Carta Porte complements and US BOLs.
_UNIT_ALIASES: dict[str, WeightUnit] = {
    "KG": WeightUnit.KG,
    "KGS": WeightUnit.KG,
    "KGM": WeightUnit.KG,  # SAT c_ClaveUnidad / UN/ECE Rec 20
    "KILO": WeightUnit.KG,
    "KILOS": WeightUnit.KG,
    "KILOGRAM": WeightUnit.KG,
    "KILOGRAMS": WeightUnit.KG,
    "KILOGRAMO": WeightUnit.KG,
    "KILOGRAMOS": WeightUnit.KG,
    "LB": WeightUnit.LB,
    "LBS": WeightUnit.LB,
    "LBR": WeightUnit.LB,  # UN/ECE Rec 20
    "POUND": WeightUnit.LB,
    "POUNDS": WeightUnit.LB,
    "LIBRA": WeightUnit.LB,
    "LIBRAS": WeightUnit.LB,
}


def _coerce_unit(value: Any) -> Any:
    if isinstance(value, str):
        key = value.strip().upper().rstrip(".")
        if key in _UNIT_ALIASES:
            return _UNIT_ALIASES[key]
    return value


def _coerce_decimal(value: Any) -> Any:
    """Build a Decimal from ints, floats (via repr) or ``"12,345.60"`` strings.

    Returning a ``Decimal`` keeps validation working under pydantic strict
    mode, which instructor uses when parsing tool output.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.replace(",", "").strip())
        except InvalidOperation:
            return value
    return value


def drop_computed_fields(model: type[BaseModel], data: Any) -> Any:
    """Remove computed-field keys from input so a model's own dump re-validates."""
    if isinstance(data, dict):
        computed = model.model_computed_fields
        return {k: v for k, v in data.items() if k not in computed}
    return data


UnitField = Annotated[WeightUnit, BeforeValidator(_coerce_unit)]
PositiveDecimal = Annotated[
    Decimal, BeforeValidator(_coerce_decimal), Field(gt=0, allow_inf_nan=False, max_digits=14)
]
NonNegativeDecimal = Annotated[
    Decimal, BeforeValidator(_coerce_decimal), Field(ge=0, allow_inf_nan=False, max_digits=18)
]


def kg_to_lb(kilograms: Decimal) -> Decimal:
    """Convert kilograms to pounds, rounded half-even to 0.01 lb."""
    return (kilograms / KG_PER_LB).quantize(LB_QUANTUM, rounding=ROUND_HALF_EVEN)


def to_pounds(value: Decimal, unit: WeightUnit) -> Decimal:
    """Normalise a weight in any supported unit to pounds."""
    if unit is WeightUnit.KG:
        return kg_to_lb(value)
    return value.quantize(LB_QUANTUM, rounding=ROUND_HALF_EVEN)


class Weight(BaseModel):
    """A weight as stated on a source document, plus its pound equivalent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: PositiveDecimal = Field(description="Numeric weight exactly as printed.")
    unit: UnitField = Field(description="Unit as printed: KG or LB.")

    @model_validator(mode="before")
    @classmethod
    def _drop_computed_fields(cls, data: Any) -> Any:
        return drop_computed_fields(cls, data)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pounds(self) -> Decimal:
        """The weight normalised to pounds."""
        return to_pounds(self.value, self.unit)


class WeightBasis(StrEnum):
    """Whether a document's weight includes packaging and pallets."""

    GROSS = "GROSS"  # goods + packaging + pallets (typical on a BOL)
    NET = "NET"  # goods only (typical on a factura / Carta Porte line)
    UNKNOWN = "UNKNOWN"


class PalletType(StrEnum):
    """Common wooden pallet footprints on US-Mexico lanes."""

    GMA_48X40 = "GMA_48X40"  # North American grocery standard
    ISO_1200X1000 = "ISO_1200X1000"  # common Mexican/industrial footprint
    EURO_1200X800 = "EURO_1200X800"
    UNKNOWN = "UNKNOWN"


#: Planning estimates for empty *wooden* pallets, in pounds. Real tare varies
#: with wood species, moisture content and condition, so these figures feed
#: an estimate only; the reconciliation threshold absorbs the residual error.
PALLET_TARE_LB: dict[PalletType, Decimal] = {
    PalletType.GMA_48X40: Decimal("40.00"),
    PalletType.ISO_1200X1000: Decimal("55.00"),
    PalletType.EURO_1200X800: Decimal("55.00"),
    PalletType.UNKNOWN: Decimal("45.00"),
}


def pallet_tare_lb(pallet_count: int | None, pallet_type: PalletType) -> Decimal:
    """Estimated combined tare of ``pallet_count`` empty wooden pallets."""
    if not pallet_count:
        return Decimal("0.00")
    return (PALLET_TARE_LB[pallet_type] * pallet_count).quantize(LB_QUANTUM)


def estimate_gross_lb(
    *,
    gross: Weight | None,
    net: Weight | None,
    pallet_count: int | None,
    pallet_type: PalletType,
) -> Decimal | None:
    """Best estimate of the shipment's gross weight in pounds.

    A stated gross weight wins. Otherwise the net weight is grossed up by the
    estimated tare of the wooden pallets, so a factura that declares only net
    merchandise weight can be compared with a BOL that declares scale weight.
    """
    if gross is not None:
        return gross.pounds
    if net is not None:
        return net.pounds + pallet_tare_lb(pallet_count, pallet_type)
    return None


def estimate_net_lb(
    *,
    gross: Weight | None,
    net: Weight | None,
    pallet_count: int | None,
    pallet_type: PalletType,
) -> Decimal | None:
    """Best estimate of net merchandise weight in pounds (gross less tare)."""
    if net is not None:
        return net.pounds
    if gross is not None:
        return max(Decimal("0.00"), gross.pounds - pallet_tare_lb(pallet_count, pallet_type))
    return None


def within_relative_tolerance(actual: Decimal, expected: Decimal, pct: Decimal) -> bool:
    """True if ``actual`` is within ``pct`` percent of ``expected``."""
    if expected == 0:
        return actual == 0
    return abs(actual - expected) / expected * 100 <= pct
