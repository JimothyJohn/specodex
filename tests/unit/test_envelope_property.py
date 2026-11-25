"""Property tests for the physical envelope coercers on ProductBase.

``dimensions`` and ``weight`` eat Gemini output and legacy DynamoDB rows.
Contract, for ANY input:

1. Constructing a product never raises because of these two fields.
2. ``dimensions`` is None or has at least one axis; every present axis is
   a finite, positive ValueUnit in ``mm`` at or below the 20 m ceiling.
3. ``weight`` is None or a finite, positive ValueUnit in ``kg``.
4. ``max_dimension`` equals the largest present axis (None iff no axis).
5. Serialise → re-validate is a fixed point (rows written by this code
   reload unchanged — the migration depends on this).
"""

from __future__ import annotations

import logging
import math
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from specodex.models.motor import Motor

_UNITS = st.sampled_from(
    [
        "mm",
        "cm",
        "m",
        "in",
        "in.",
        "inch",
        "inches",
        "ft",
        "um",
        "μm",
        '"',
        "kg",
        "g",
        "lb",
        "lbs",
        "oz",
        "Kg",
        "N",
        "",
        " mm ",
        "MM",
        "furlong",
    ]
)

_numbers = st.one_of(
    st.integers(min_value=-(10**12), max_value=10**12),
    st.floats(allow_nan=True, allow_infinity=True),
    st.decimals(allow_nan=False, allow_infinity=False, places=3),
    st.just(Decimal("1E+400")),
    st.booleans(),
)

_scalar_junk = st.one_of(
    st.none(),
    _numbers,
    st.text(max_size=20),
    st.sampled_from(
        ["N/A", "null", "-", "TBD", "60 mm", "1.6;kg", "3.5 lb", "1.2.3 mm", "∞ mm"]
    ),
)

_value_unit = st.fixed_dictionaries(
    {
        "value": st.one_of(_numbers, st.text(max_size=8), st.none()),
        "unit": st.one_of(_UNITS, st.none(), st.integers()),
    }
)

_axis = st.one_of(
    _scalar_junk,
    _value_unit,
    st.dictionaries(st.text(max_size=5), _scalar_junk, max_size=3),
)

_dimensions = st.one_of(
    _scalar_junk,
    st.lists(_scalar_junk, max_size=3),
    st.fixed_dictionaries(
        {},
        optional={
            "length": _axis,
            "width": _axis,
            "height": _axis,
            "unit": st.one_of(_UNITS, st.none(), st.integers()),
            "depth": _axis,
        },
    ),
)

_weight = st.one_of(_scalar_junk, _value_unit)


@pytest.fixture(autouse=True)
def _silence_validation_logs():
    logging.disable(logging.WARNING)
    yield
    logging.disable(logging.NOTSET)


def _check_axis(axis) -> float:
    assert axis.unit == "mm"
    assert math.isfinite(axis.value)
    assert 0 < axis.value <= 20_000
    return axis.value


@pytest.mark.unit
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(dims=_dimensions, weight=_weight)
def test_envelope_contract(dims, weight) -> None:
    m = Motor(product_name="M", manufacturer="Acme", dimensions=dims, weight=weight)

    present = []
    if m.dimensions is not None:
        for axis in (m.dimensions.length, m.dimensions.width, m.dimensions.height):
            if axis is not None:
                present.append(_check_axis(axis))
        assert present, "empty Dimensions must collapse to None"

    if present:
        assert m.max_dimension is not None
        assert m.max_dimension.unit == "mm"
        assert m.max_dimension.value == max(present)
    else:
        assert m.max_dimension is None

    if m.weight is not None:
        assert m.weight.unit == "kg"
        assert math.isfinite(m.weight.value)
        assert 0 < m.weight.value <= 50_000

    dumped = m.model_dump(mode="json")
    again = Motor.model_validate(dumped)
    assert again.model_dump(mode="json") == dumped
