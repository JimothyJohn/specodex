"""Shared Pydantic types for product models.

``ValueUnit`` and ``MinMaxUnit`` are real ``BaseModel`` classes — every
numeric spec is carried end-to-end as ``{value, unit}`` or
``{min, max, unit}``. The same shape Gemini emits, the same shape
DynamoDB stores, the same shape the frontend consumes. No compact
``"value;unit"`` strings.

Per-quantity narrowed aliases (``Voltage``, ``Current``, ...) wrap
``Optional[ValueUnit]`` (or ``Optional[MinMaxUnit]``) with a
``BeforeValidator`` that coerces forgiving inputs (LLM dicts,
space-separated strings, qualifier-prefixed numbers) and rejects
wrong-family units to ``None`` so the quality filter can drop the row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, List, Literal, Optional

from pydantic import BaseModel, BeforeValidator, model_validator

from specodex.units import normalize_unit_value

_logger = logging.getLogger(__name__)


ProductType = Literal[
    "motor",
    "drive",
    "gearhead",
    "robot_arm",
    "contactor",
    "electric_cylinder",
    "linear_actuator",
]


class Datasheet(BaseModel):
    """Represents information about a product datasheet."""

    url: Optional[str] = None
    pages: Optional[List[int]] = None


# ---------------------------------------------------------------------------
# Marker dataclasses — used by ``llm_schema.py`` to detect ValueUnit /
# MinMaxUnit family fields when generating the Gemini response schema.
# Pydantic strips the outer ``Annotated`` off ``field.annotation``, so
# detection keys on ``field.metadata`` instead of identity.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitFamily:
    """A physical-quantity family: canonical unit + accepted aliases."""

    name: str
    canonical: str
    accepted: frozenset[str]

    def contains(self, unit: str) -> bool:
        return unit == self.canonical or unit in self.accepted


@dataclass(frozen=True)
class ValueUnitMarker:
    """Marker for ValueUnit-family fields in a FieldInfo's metadata."""

    family: "UnitFamily | None" = None


@dataclass(frozen=True)
class MinMaxUnitMarker:
    """Marker for MinMaxUnit-family fields in a FieldInfo's metadata."""

    family: "UnitFamily | None" = None


# ---------------------------------------------------------------------------
# Input coercers — accept the assorted shapes Gemini emits and produce a
# clean dict the BaseModel can validate. Returning ``None`` from any
# coercer signals "drop the field" — the caller's BeforeValidator picks
# that up at the field level.
# ---------------------------------------------------------------------------


def _strip_value_qualifiers(v: Any) -> Optional[float]:
    """Coerce a possibly-qualified numeric input to a plain float.

    Accepts: int/float, Decimal (for DynamoDB read paths), "100", "100+",
    "+100", "~50", ">100". Returns ``None`` for non-numeric strings
    ("approx 5", "N/A").
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        cleaned = v.strip().strip("+~><")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _coerce_str_to_value_unit_dict(s: str) -> Optional[dict]:
    """Parse a "value unit" or "value;unit" string into ``{value, unit}``."""
    s = s.strip()
    if not s:
        return None
    if ";" in s:
        parts = s.split(";", 1)
        if len(parts) != 2:
            return None
        val_str, unit = parts[0].strip(), parts[1].strip()
        if not unit:
            return None
        val = _strip_value_qualifiers(val_str)
        if val is None:
            return None
        return {"value": val, "unit": unit}
    parts = s.split()
    if len(parts) >= 2:
        val = _strip_value_qualifiers(parts[0])
        unit = " ".join(parts[1:])
        if val is None or not unit:
            return None
        return {"value": val, "unit": unit}
    return None


def _coerce_dict_to_value_unit_dict(d: dict) -> Optional[dict]:
    """Coerce assorted dict shapes to a clean ``{value, unit}`` dict."""
    if not d:
        return None
    val_raw = d.get("value")
    unit_raw = d.get("unit")
    unit = str(unit_raw).strip() if unit_raw is not None else ""

    if val_raw is not None and unit:
        val = _strip_value_qualifiers(val_raw)
        if val is None:
            return None
        return {"value": val, "unit": unit}

    # ValueUnit field receiving min/max input — collapse to scalar.
    min_val = _strip_value_qualifiers(d.get("min"))
    max_val = _strip_value_qualifiers(d.get("max"))
    if unit and (min_val is not None or max_val is not None):
        scalar = min_val if min_val is not None else max_val
        return {"value": scalar, "unit": unit}
    return None


def _coerce_str_to_min_max_unit_dict(s: str) -> Optional[dict]:
    """Parse a "min-max;unit" / "value;unit" string into a MinMaxUnit dict."""
    s = s.strip()
    if not s or ";" not in s:
        # Fall back to the value-unit shape if it looks like one
        return None
    parts = s.split(";", 1)
    if len(parts) != 2:
        return None
    range_part, unit = parts[0].strip(), parts[1].strip()
    if not unit:
        return None
    range_part = range_part.replace(" to ", "-")
    # Try range "lo-hi" (handle leading negative on lo).
    import re

    m = re.match(r"^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)$", range_part)
    if m:
        try:
            return {
                "min": float(m.group(1)),
                "max": float(m.group(2)),
                "unit": unit,
            }
        except ValueError:
            return None
    # Single value
    val = _strip_value_qualifiers(range_part)
    if val is None:
        return None
    return {"min": val, "max": None, "unit": unit}


def _coerce_dict_to_min_max_unit_dict(d: dict) -> Optional[dict]:
    """Coerce assorted dict shapes to a clean ``{min, max, unit}`` dict."""
    if not d:
        return None
    unit_raw = d.get("unit")
    unit = str(unit_raw).strip() if unit_raw is not None else ""
    if not unit:
        return None
    min_val = _strip_value_qualifiers(d.get("min"))
    max_val = _strip_value_qualifiers(d.get("max"))
    if min_val is not None or max_val is not None:
        return {"min": min_val, "max": max_val, "unit": unit}
    val = _strip_value_qualifiers(d.get("value"))
    if val is not None:
        return {"min": val, "max": None, "unit": unit}
    return None


# ---------------------------------------------------------------------------
# Structured ValueUnit / MinMaxUnit classes
# ---------------------------------------------------------------------------


class ValueUnit(BaseModel):
    """A numeric value paired with a unit — the canonical scalar spec shape.

    Pydantic accepts forgiving input forms (dicts with extra keys,
    space-separated strings, qualifier-prefixed numbers) and normalises
    the unit to its canonical form (mNm → Nm) on construction. The
    serialised form is always ``{"value": <float>, "unit": "<str>"}``.
    """

    model_config = {"populate_by_name": True}

    value: float
    unit: str

    @model_validator(mode="before")
    @classmethod
    def _coerce_input(cls, data: Any) -> Any:
        if data is None or isinstance(data, ValueUnit):
            return data
        if isinstance(data, MinMaxUnit):
            scalar = data.min if data.min is not None else data.max
            if scalar is None:
                raise ValueError("MinMaxUnit has no min/max to collapse to scalar")
            return {"value": scalar, "unit": data.unit}
        if isinstance(data, str):
            coerced = _coerce_str_to_value_unit_dict(data)
            if coerced is None:
                raise ValueError(f"could not parse {data!r} as value+unit")
            return coerced
        if isinstance(data, dict):
            coerced = _coerce_dict_to_value_unit_dict(data)
            if coerced is None:
                raise ValueError(f"could not extract value+unit from {data!r}")
            return coerced
        return data

    @model_validator(mode="after")
    def _normalize_unit(self) -> "ValueUnit":
        new_value, new_unit = normalize_unit_value(self.value, self.unit)
        if new_value != self.value:
            self.value = new_value
        if new_unit != self.unit:
            self.unit = new_unit
        return self


class MinMaxUnit(BaseModel):
    """A numeric range paired with a shared unit — canonical range spec shape.

    At least one of ``min`` / ``max`` must be present; either may be
    ``None`` for half-open intervals (e.g. ``max=85, min=None`` for "up
    to 85 °C"). Serialised form is ``{"min": <num|null>, "max": <num|null>,
    "unit": "<str>"}``.
    """

    model_config = {"populate_by_name": True}

    min: Optional[float] = None
    max: Optional[float] = None
    unit: str

    @model_validator(mode="before")
    @classmethod
    def _coerce_input(cls, data: Any) -> Any:
        if data is None or isinstance(data, MinMaxUnit):
            return data
        if isinstance(data, ValueUnit):
            return {"min": data.value, "max": None, "unit": data.unit}
        if isinstance(data, str):
            coerced = _coerce_str_to_min_max_unit_dict(data)
            if coerced is None:
                raise ValueError(f"could not parse {data!r} as min-max+unit")
            return coerced
        if isinstance(data, dict):
            coerced = _coerce_dict_to_min_max_unit_dict(data)
            if coerced is None:
                raise ValueError(f"could not extract min/max+unit from {data!r}")
            return coerced
        return data

    @model_validator(mode="after")
    def _normalize_unit(self) -> "MinMaxUnit":
        if self.min is None and self.max is None:
            raise ValueError("MinMaxUnit must have at least one of min or max")
        canonical_unit = self.unit
        if self.min is not None:
            new_min, canonical_unit = normalize_unit_value(self.min, self.unit)
            self.min = new_min
        if self.max is not None:
            new_max, canonical_unit = normalize_unit_value(self.max, self.unit)
            self.max = new_max
        if canonical_unit != self.unit:
            self.unit = canonical_unit
        return self


# ---------------------------------------------------------------------------
# IpRating — bare int with forgiving input coercion. Unrelated to
# ValueUnit/MinMaxUnit; lives here for proximity to the other field
# aliases.
# ---------------------------------------------------------------------------


def _coerce_ip_rating(v: Any) -> Any:
    """Coerce legacy IP-rating shapes to a plain int.

    Accepts:
        int  54              → 54
        str  "54"            → 54
        str  "IP54" / "ip54" → 54
        dict {"value": 54}   → 54 (legacy TS serialisation)
    Anything else becomes None so Pydantic validation doesn't crash on
    a dict-shaped LLM mis-extraction.
    """
    if v is None or isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().upper().removeprefix("IP").strip()
        try:
            return int(s)
        except ValueError:
            return None
    if isinstance(v, dict):
        for key in ("value", "min"):
            inner = v.get(key)
            if inner is not None:
                return _coerce_ip_rating(inner)
        return None
    return v


IpRating = Annotated[Optional[int], BeforeValidator(_coerce_ip_rating)]


# ---------------------------------------------------------------------------
# Per-quantity ValueUnit / MinMaxUnit aliases
#
# These narrow the canonical types to a single physical-quantity family
# and reject wrong-family units at validation time (e.g. "5 rpm" on a
# Current field becomes None). Conventions:
#
#   - Canonical unit matches ``specodex/units.py`` ``UNIT_CONVERSIONS``
#     so normalisation and family-check agree.
#   - Each family lists every form the LLM might emit — both aliases
#     that normalise to the canonical (mA → A) and aliases that pass
#     through unchanged (Vac, Arms, ohm).
#   - Fields whose quantity is fuzzy (``warranty``, ``msrp``, ``backlash``
#     in arcmin, compound units like V/krpm) stay on plain ValueUnit.
# ---------------------------------------------------------------------------


VOLTAGE = UnitFamily(
    "voltage",
    "V",
    frozenset({"V", "Vac", "Vdc", "Vrms", "VAC", "VDC", "VRMS", "mV", "kV"}),
)
CURRENT = UnitFamily(
    "current",
    "A",
    frozenset({"A", "mA", "μA", "uA", "Arms", "Adc", "ARMS"}),
)
POWER = UnitFamily(
    "power",
    "W",
    frozenset({"W", "mW", "kW", "hp", "HP", "VA", "kVA"}),
)
TORQUE = UnitFamily(
    "torque",
    "Nm",
    frozenset(
        {
            "Nm",
            "N-m",
            "N·m",
            "mNm",
            "mN-m",
            "mN·m",
            "μNm",
            "oz-in",
            "oz·in",
            "ozin",
            "lb-ft",
            "lb·ft",
            "lbft",
            "lb-in",
            "lb·in",
            "lbin",
            "kgf·cm",
            "kgf.cm",
            "kgfcm",
            "kNm",
        }
    ),
)
SPEED = UnitFamily(
    "speed",
    "rpm",
    frozenset({"rpm", "RPM", "rad/s", "rps"}),
)
FORCE = UnitFamily(
    "force",
    "N",
    frozenset({"N", "mN", "kN", "lbf", "kgf"}),
)
LENGTH = UnitFamily(
    "length",
    "mm",
    frozenset({"mm", "m", "cm", "in", "inch", "ft", "μm", "um"}),
)
MASS = UnitFamily(
    "mass",
    "kg",
    frozenset({"kg", "g", "lb", "oz"}),
)
TEMPERATURE = UnitFamily(
    "temperature",
    "°C",
    frozenset({"°C", "C", "°F", "F", "K"}),
)
FREQUENCY = UnitFamily(
    "frequency",
    "Hz",
    frozenset({"Hz", "kHz", "MHz", "GHz"}),
)
INERTIA = UnitFamily(
    "inertia",
    "kg·cm²",
    frozenset(
        {
            "kg·cm²",
            "kg-cm²",
            "kgcm²",
            "g·cm²",
            "g-cm²",
            "gcm²",
            "g.cm²",
            "g·cm2",
            "gcm2",
            "kg·m²",
            "kg-m²",
            "kgm²",
            "kg.m²",
            "kg·m2",
            "kgm2",
            "oz-in²",
            "oz·in²",
            "oz-in2",
            "oz·in2",
        }
    ),
)
RESISTANCE = UnitFamily(
    "resistance",
    "Ω",
    frozenset({"Ω", "mΩ", "kΩ", "ohm", "ohms", "Ohm", "Ohms"}),
)
INDUCTANCE = UnitFamily(
    "inductance",
    "mH",
    frozenset({"mH", "H", "μH", "uH", "nH"}),
)


def _typed_value_unit(family: UnitFamily):
    """Build a ValueUnit Annotated narrowed to one quantity family.

    The BeforeValidator coerces forgiving inputs into a ValueUnit
    instance (or returns ``None`` if the input is unparseable / wrong
    family). A wrong-family unit returns None so the quality filter
    can drop the row, rather than raising and killing the whole
    extraction.
    """

    def _coerce(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, ValueUnit):
            return v if family.contains(v.unit) else None
        if isinstance(v, MinMaxUnit):
            if not family.contains(v.unit):
                return None
            scalar = v.min if v.min is not None else v.max
            if scalar is None:
                return None
            return ValueUnit(value=scalar, unit=v.unit)
        try:
            instance = ValueUnit.model_validate(v)
        except (ValueError, TypeError):
            return None
        return instance if family.contains(instance.unit) else None

    return Annotated[
        Optional[ValueUnit],
        BeforeValidator(_coerce),
        ValueUnitMarker(family=family),
    ]


def _typed_min_max_unit(family: UnitFamily):
    """Build a MinMaxUnit Annotated narrowed to one quantity family."""

    def _coerce(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, MinMaxUnit):
            return v if family.contains(v.unit) else None
        if isinstance(v, ValueUnit):
            if not family.contains(v.unit):
                return None
            return MinMaxUnit(min=v.value, max=None, unit=v.unit)
        try:
            instance = MinMaxUnit.model_validate(v)
        except (ValueError, TypeError):
            return None
        return instance if family.contains(instance.unit) else None

    return Annotated[
        Optional[MinMaxUnit],
        BeforeValidator(_coerce),
        MinMaxUnitMarker(family=family),
    ]


# --- Scalar quantity types ---------------------------------------------------
Voltage = _typed_value_unit(VOLTAGE)
Current = _typed_value_unit(CURRENT)
Power = _typed_value_unit(POWER)
Torque = _typed_value_unit(TORQUE)
Speed = _typed_value_unit(SPEED)
Force = _typed_value_unit(FORCE)
Length = _typed_value_unit(LENGTH)
Mass = _typed_value_unit(MASS)
Temperature = _typed_value_unit(TEMPERATURE)
Frequency = _typed_value_unit(FREQUENCY)
Inertia = _typed_value_unit(INERTIA)
Resistance = _typed_value_unit(RESISTANCE)
Inductance = _typed_value_unit(INDUCTANCE)


# --- Range quantity types ----------------------------------------------------
VoltageRange = _typed_min_max_unit(VOLTAGE)
CurrentRange = _typed_min_max_unit(CURRENT)
TemperatureRange = _typed_min_max_unit(TEMPERATURE)
FrequencyRange = _typed_min_max_unit(FREQUENCY)
ForceRange = _typed_min_max_unit(FORCE)


def find_value_unit_marker(metadata) -> Optional[ValueUnitMarker]:
    """Return the ValueUnitMarker in a Pydantic FieldInfo.metadata, if any."""
    for m in metadata or ():
        if isinstance(m, ValueUnitMarker):
            return m
    return None


def find_min_max_unit_marker(metadata) -> Optional[MinMaxUnitMarker]:
    """Return the MinMaxUnitMarker in a Pydantic FieldInfo.metadata, if any."""
    for m in metadata or ():
        if isinstance(m, MinMaxUnitMarker):
            return m
    return None
