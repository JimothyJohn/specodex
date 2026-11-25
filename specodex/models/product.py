# This module defines a base Pydantic model for tangible products, encapsulating
# common attributes that are shared across different types of hardware like drives,
# motors, etc. By creating a common base model, we ensure consistency,
# reduce code duplication, and improve maintainability of product-related models.

from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, BeforeValidator, Field, model_validator
from typing_extensions import Annotated


from specodex.models.commercial import SourcedFigure
from specodex.models.common import (
    Length,
    LenientValueUnit,
    Mass,
    ProductType,
    ValueUnit,
)
from specodex.placeholders import is_placeholder


def _coerce_placeholder_to_none(v: Any) -> Any:
    """Replace placeholder strings ("N/A", "TBD", ...) with None at ingest.

    Keeps the database free of "N/A" literals, which would otherwise inflate
    quality scores and render as garbage in the frontend table.
    """
    return None if is_placeholder(v) else v


PlaceholderSanitizedStr = Annotated[
    Optional[str],
    BeforeValidator(_coerce_placeholder_to_none),
]


# --- Physical envelope ------------------------------------------------------
#
# The envelope answers "how big and heavy is the thing I'm ordering" — a
# bounding box read off the outline drawing, not a mating dimension. It is
# allowed to be approximate; interface geometry (shaft, pilot, bolt circle,
# threads) lives in dedicated per-type fields and must be exact.
#
# Length and mass are deliberately NOT normalised globally (specodex/units.py
# excludes them: cable length in m vs shaft diameter in mm). Here the field
# context is unambiguous, so the envelope normalises itself: every axis to
# mm, weight to kg. That is what makes "largest dimension" and weight
# sortable across vendors that publish in inches and pounds.

_LENGTH_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "in.": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    '"': 25.4,
    "ft": 304.8,
    "μm": 1e-3,
    "um": 1e-3,
}

_MASS_TO_KG: dict[str, float] = {
    "kg": 1.0,
    "Kg": 1.0,
    "KG": 1.0,
    "kgs": 1.0,
    "g": 1e-3,
    "lb": 0.45359237,
    "lbs": 0.45359237,
    "lb.": 0.45359237,
    "lbs.": 0.45359237,
    "oz": 0.028349523125,
}

# Sanity ceilings: anything beyond these is an extraction error (a part
# number or a µm value read as mm), not a real product. 20 m covers the
# largest gantry robots and cabinet lineups; 50 t covers large motors.
_MAX_ENVELOPE_MM = 20_000.0
_MAX_WEIGHT_KG = 50_000.0


def _round_sig(value: float) -> float:
    """Round to 4 significant figures — envelope dims are not precision data,
    and 25.4 conversions otherwise leave noise like 522.478000001."""
    if value == 0:
        return 0.0
    return float(f"{value:.4g}")


def _to_canonical(
    v: Any, table: dict[str, float], canonical: str, ceiling: float
) -> Optional[dict[str, Any]]:
    """Coerce one envelope quantity to ``{"value", "unit": canonical}``.

    Accepts a ValueUnit, a ``{value, unit}`` dict, or a ``"60 mm"`` /
    ``"60;mm"`` string. Returns None for anything unparseable, in an
    unknown unit, non-finite, non-positive, or above ``ceiling`` — the
    field degrades to None instead of killing the row.
    """
    if v is None or is_placeholder(v):
        return None
    if isinstance(v, ValueUnit):
        value, unit = v.value, v.unit
    else:
        if isinstance(v, dict) and isinstance(v.get("unit"), str):
            # Normalise spellings before ValueUnit sees them so "inches"
            # isn't lost to an alias set that doesn't know it.
            v = {**v, "unit": v["unit"].strip()}
        try:
            parsed = ValueUnit.model_validate(v)
        except (ValueError, TypeError):
            return None
        value, unit = parsed.value, parsed.unit
    factor = table.get(unit.strip()) if isinstance(unit, str) else None
    if factor is None:
        return None
    try:
        out = float(value) * factor
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or out <= 0 or out > ceiling:
        return None
    return {"value": _round_sig(out), "unit": canonical}


def _coerce_envelope_length(v: Any) -> Any:
    return _to_canonical(v, _LENGTH_TO_MM, "mm", _MAX_ENVELOPE_MM)


def _coerce_weight(v: Any) -> Any:
    return _to_canonical(v, _MASS_TO_KG, "kg", _MAX_WEIGHT_KG)


def _coerce_dimensions_container(v: Any) -> Any:
    """Anything that isn't an object (Gemini sometimes emits the envelope as
    one ``"120 x 60 x 60 mm"`` string) drops the field, not the row."""
    if v is None or isinstance(v, (dict, BaseModel)):
        return v
    return None


EnvelopeLength = Annotated[Length, BeforeValidator(_coerce_envelope_length)]
ProductWeight = Annotated[Mass, BeforeValidator(_coerce_weight)]


class Dimensions(BaseModel):
    """Overall physical envelope (bounding box), each axis in mm.

    Axis convention: ``length`` along the product's main axis (motor and
    gearhead: along the shaft, flange face to rear; actuator: overall
    length at the stroke the row describes); ``width`` / ``height`` across
    it (for a square flange both equal the flange edge). Drives and
    cabinet parts: width × height × depth-as-length, as mounted.

    Legacy rows stored ``{"width": 60.0, "length": 120.0, "unit": "in"}``
    — bare floats plus one shared, unnormalised unit. The before-validator
    lifts that shape into per-axis ValueUnits so old DynamoDB rows,
    Gemini's habit of emitting the flat shape, and the new shape all load
    the same way.
    """

    length: EnvelopeLength = None
    width: EnvelopeLength = None
    height: EnvelopeLength = None

    @model_validator(mode="before")
    @classmethod
    def _lift_shared_unit(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        shared = data.get("unit")
        if not isinstance(shared, str) or not shared.strip():
            return {k: data.get(k) for k in ("length", "width", "height")}
        lifted: dict[str, Any] = {}
        for axis in ("length", "width", "height"):
            raw = data.get(axis)
            if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
                lifted[axis] = {"value": raw, "unit": shared}
            elif isinstance(raw, str) and _BARE_NUMBER.fullmatch(raw.strip()):
                lifted[axis] = {"value": raw.strip(), "unit": shared}
            else:
                lifted[axis] = raw
        return lifted

    def largest(self) -> Optional[float]:
        """Largest known axis in mm, or None when no axis is known."""
        known = [a.value for a in (self.length, self.width, self.height) if a]
        return max(known) if known else None

    def is_empty(self) -> bool:
        return self.length is None and self.width is None and self.height is None


_BARE_NUMBER = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")


class ProductBase(BaseModel):
    """
    A base model for products with common attributes, designed for DynamoDB.

    This model uses a composite primary key (PK, SK) to align with DynamoDB's
    single-table design best practices.

    Attributes:
        PK: The Partition Key. Formatted as 'PRODUCT#<product_type>'.
        SK: The Sort Key. Formatted as 'PRODUCT#<product_id>'.
        product_id: The unique identifier (UUID) for the product.
    """

    model_config = {"populate_by_name": True}

    # PK/SK are persistence-layer concerns — kept as plain @property so they
    # don't appear in `model_dump()` or the generated TS interface, but are
    # still readable as attributes for the DynamoDB writer (see
    # specodex/db/dynamo.py:_serialize_item which assigns them explicitly).
    # Per MODELGEN.md OQ1 (resolved 2026-05-07): "drop the computed-field
    # decorator and compute PK/SK on read in the API."
    @property
    def PK(self) -> str:
        return f"PRODUCT#{self.product_type.upper()}"

    @property
    def SK(self) -> str:
        return f"PRODUCT#{self.product_id}"

    # AI-generated comment: product_id is auto-generated by the program, not by the LLM.
    # Making it Optional ensures the LLM won't try to create UUIDs, which it does incorrectly.
    # The default_factory ensures a UUID is always generated if not provided.
    product_id: UUID = Field(
        default_factory=uuid4, description="Unique identifier (auto-generated)"
    )
    product_type: ProductType = Field(
        ..., description="Type of product (e.g., 'motor', 'drive')"
    )
    product_name: str = Field(..., description="Product name")
    product_family: PlaceholderSanitizedStr = Field(
        None, description="Product family or sub-series"
    )
    part_number: PlaceholderSanitizedStr = Field(None, description="Part number")
    manufacturer: str = Field(..., description="Manufacturer name")
    release_year: Optional[int] = None
    dimensions: Annotated[
        Optional[Dimensions], BeforeValidator(_coerce_dimensions_container)
    ] = Field(
        None,
        description=(
            "Overall envelope from the outline/dimension drawing: length "
            "along the main axis (shaft axis for motors and gearheads), "
            "width and height across it. Approximate is fine."
        ),
    )
    weight: ProductWeight = Field(
        None, description="Product mass (e.g., in kg). Normalised to kg."
    )
    max_dimension: Length = Field(
        None,
        description=(
            "Largest known axis of `dimensions`, in mm. Derived on "
            "validation, never LLM-extracted (llm_schema.EXCLUDED_FIELDS)."
        ),
    )
    msrp: LenientValueUnit = None
    msrp_source_url: Optional[str] = Field(
        None,
        description="URL the MSRP was scraped from. Populated by price-enrich.",
    )
    msrp_fetched_at: Optional[str] = Field(
        None, description="ISO 8601 timestamp when MSRP was last fetched."
    )
    availability: Optional[
        Literal[
            "in_stock",
            "back_order",
            "out_of_stock",
            "pre_order",
            "limited",
            "discontinued",
        ]
    ] = Field(
        None,
        description=(
            "Stock availability observed at a distributor, mapped from "
            "schema.org ItemAvailability. This is a per-seller, "
            "point-in-time snapshot — NOT an intrinsic product lead time "
            "(no honest public numeric lead time exists per part). Read it "
            "with availability_source_url + availability_fetched_at for "
            "provenance; it goes stale. Populated by availability-enrich."
        ),
    )
    availability_source_url: Optional[str] = Field(
        None,
        description="URL the availability status was scraped from.",
    )
    availability_fetched_at: Optional[str] = Field(
        None, description="ISO 8601 timestamp when availability was last fetched."
    )
    price_estimate: Optional[SourcedFigure] = Field(
        None,
        description=(
            "Inferred price when no listed msrp exists. Computed by "
            "specodex.pricing.inference from DB comparables — never "
            "LLM-extracted (see llm_schema.EXCLUDED_FIELDS). Kept distinct "
            "from msrp so an estimate can never masquerade as a listed "
            "price; carries its confidence tier and comparable citations."
        ),
    )
    lead_time_estimate: Optional[SourcedFigure] = Field(
        None,
        description=(
            "Inferred lead time (e.g. {'value': 6, 'unit': 'weeks'}) with "
            "confidence + citations. Computed, never LLM-extracted. The "
            "stocked signal itself lives in `availability`; this field "
            "covers published vendor/family lead-time statements and "
            "their provenance."
        ),
    )
    warranty: LenientValueUnit = None
    lead_time: LenientValueUnit = Field(
        None,
        description=(
            "Expected delivery / lead time for the product. Typically a "
            "ValueUnit with unit='days' (e.g. {'value': 30, 'unit': 'days'}). "
            "Sourced from manufacturer or distributor data, not the datasheet."
        ),
    )
    datasheet_url: Optional[str] = Field(
        None, description="URL of the source datasheet"
    )
    pages: Optional[list[int]] = Field(
        None,
        description="1-indexed PDF pages where this product's specs were found. Used for #page=N deep-linking.",
    )

    @model_validator(mode="after")
    def _derive_envelope(self) -> "ProductBase":
        # An all-None Dimensions (Gemini's empty-object stub, or a legacy
        # {"unit": "mm"} row) carries no information — store nothing.
        if self.dimensions is not None and self.dimensions.is_empty():
            self.dimensions = None
        # Always recomputed from `dimensions` so a stale stored value can't
        # outlive an edit to the axes.
        largest = self.dimensions.largest() if self.dimensions else None
        self.max_dimension = (
            None if largest is None else ValueUnit(value=largest, unit="mm")
        )
        return self
