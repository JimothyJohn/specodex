"""Property tests for ``specodex.units.normalize_unit_value``.

The example-based companion (``test_units.py:TestNormalizeUnitValue``)
pins the happy path for every documented alias. This file generates
*adversarial* ``(value, unit)`` pairs — pathological floats (NaN, ±inf,
subnormals, very large/small magnitudes), arbitrary unit strings
(empty, whitespace-only, unicode, random ASCII), and random sampling
from the known alias map — and asserts the documented contract holds
for every input the strategy produces.

``normalize_unit_value`` sits between LLM JSON parsing
(``parse_gemini_response``) and Pydantic ``ValueUnit`` /
``MinMaxUnit`` validation (``specodex/models/common.py:314,362,365``).
A raised exception here takes the whole product row with it; a
returned wrong shape silently mis-canonicalises stored specs. Both
are bugs the property test catches more cleanly than enumerated
cases.

**Contracts under test:**

1. ``normalize_unit_value(value, unit)`` never raises for any
   ``(float, str)`` input — including non-finite floats (NaN, ±inf)
   and arbitrary unit strings (empty, whitespace-only, unicode).
2. The returned tuple always has shape ``(float, str)``.
3. **Unknown units pass through unchanged** — the numeric value is
   identical to the input, and the returned unit is ``unit.strip()``.
4. **Canonical units pass through unchanged** for finite values.
   The canonical unit ("Nm", "W", "A", …) is NOT in ``_ALIAS_MAP``
   by construction; this property pins that invariant.
5. **Whitespace tolerance** — leading/trailing whitespace around a
   known alias is stripped before lookup.
6. **Linear conversions multiply by the documented factor** —
   for every alias with a numeric multiplier, the result is within
   floating-point tolerance of ``input * multiplier``.
7. **Non-finite preservation** — NaN, +inf, −inf inputs do not
   raise. (Regression: ``_round_converted`` used to ``OverflowError``
   on ±inf and ``ValueError`` on NaN; fixed in this commit.)
8. **Linear-conversion sign symmetry** — for non-temperature
   conversions, ``normalize_unit_value(-v, u)`` negates the
   numeric output of ``normalize_unit_value(v, u)``.
"""

from __future__ import annotations

import logging
import math

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.units import UNIT_CONVERSIONS, _ALIAS_MAP, normalize_unit_value


# Silence the per-conversion INFO log — a 300-example property run
# would otherwise emit hundreds of routine "Unit conversion: …" lines.
@pytest.fixture(autouse=True)
def _silence_unit_logs():
    logger = logging.getLogger("specodex.units")
    prior_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(prior_level)


KNOWN_ALIASES: list[str] = sorted(_ALIAS_MAP.keys())
CANONICAL_UNITS: list[str] = sorted(UNIT_CONVERSIONS.keys())
LINEAR_ALIASES: list[str] = sorted(
    a for a, (_canonical, mult) in _ALIAS_MAP.items() if mult is not None
)


# --- Strategies -----------------------------------------------------------

_arbitrary_unit = st.one_of(
    st.sampled_from(KNOWN_ALIASES),
    st.sampled_from(CANONICAL_UNITS),
    st.just(""),
    st.just("   "),
    st.text(min_size=0, max_size=10),
    st.sampled_from(KNOWN_ALIASES).map(lambda u: f"  {u}  "),
    st.sampled_from(KNOWN_ALIASES).map(lambda u: f"\t{u}\n"),
)

_arbitrary_value = st.one_of(
    st.floats(allow_nan=True, allow_infinity=True, width=64),
    st.integers(min_value=-(2**53), max_value=2**53).map(float),
)


# --- Contract 1: never raises --------------------------------------------


@given(value=_arbitrary_value, unit=_arbitrary_unit)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_never_raises(value: float, unit: str) -> None:
    normalize_unit_value(value, unit)


# --- Contract 2: return shape --------------------------------------------


@given(value=_arbitrary_value, unit=_arbitrary_unit)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_returns_float_str_tuple(value: float, unit: str) -> None:
    result = normalize_unit_value(value, unit)
    assert isinstance(result, tuple)
    assert len(result) == 2
    val, u = result
    assert isinstance(val, float)
    assert isinstance(u, str)


# --- Contract 3: unknown units pass through ------------------------------


@given(
    value=st.floats(
        allow_nan=False, allow_infinity=False, min_value=-1e100, max_value=1e100
    ),
    unit=st.text(min_size=0, max_size=10).filter(
        lambda s: s.strip() not in _ALIAS_MAP
    ),
)
@settings(max_examples=200)
def test_unknown_unit_passes_through(value: float, unit: str) -> None:
    val, u = normalize_unit_value(value, unit)
    assert val == value
    assert u == unit.strip()


# --- Contract 4: canonical units pass through ----------------------------


@given(
    value=st.floats(
        allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6
    ),
    unit=st.sampled_from(CANONICAL_UNITS),
)
@settings(max_examples=100)
def test_canonical_units_pass_through(value: float, unit: str) -> None:
    val, u = normalize_unit_value(value, unit)
    assert val == value
    assert u == unit


# --- Contract 5: whitespace tolerance ------------------------------------


@given(
    value=st.floats(
        allow_nan=False, allow_infinity=False, min_value=-1e3, max_value=1e3
    ),
    alias=st.sampled_from(KNOWN_ALIASES),
)
@settings(max_examples=200)
def test_whitespace_around_alias_tolerated(value: float, alias: str) -> None:
    canonical_expected, _ = _ALIAS_MAP[alias]
    val_clean, u_clean = normalize_unit_value(value, alias)
    val_ws, u_ws = normalize_unit_value(value, f"  {alias}  ")
    assert u_clean == canonical_expected
    assert u_ws == canonical_expected
    # Same arithmetic path, so the numeric outputs are equal.
    if math.isfinite(val_clean) and math.isfinite(val_ws):
        assert val_clean == val_ws


# --- Contract 6: linear-conversion correctness ---------------------------


@given(
    value=st.floats(
        allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6
    ),
    alias=st.sampled_from(LINEAR_ALIASES),
)
@settings(max_examples=200)
def test_linear_conversion_correctness(value: float, alias: str) -> None:
    canonical, multiplier = _ALIAS_MAP[alias]
    assert multiplier is not None  # temperature handled separately
    val_out, u_out = normalize_unit_value(value, alias)
    assert u_out == canonical
    expected = value * multiplier
    if not math.isfinite(expected):
        # Multiplication overflow — the function should still not raise.
        # _round_converted preserves non-finite values; the property is
        # satisfied as long as we got here without an exception.
        assert math.isinf(val_out) or math.isnan(val_out)
        return
    if expected == 0:
        assert val_out == 0
        return
    rel = abs(val_out - expected) / abs(expected)
    # _round_converted keeps ~5 significant figures, so 1e-4 is the
    # tightest tolerance that holds across the full magnitude range.
    assert rel < 1e-4, (value, alias, val_out, expected)


# --- Contract 7: non-finite preservation ---------------------------------


@given(unit=_arbitrary_unit)
@settings(max_examples=100)
def test_nan_input_does_not_raise(unit: str) -> None:
    """Regression: ``_round_converted`` raised ``ValueError`` on NaN
    because ``math.floor(math.log10(nan))`` cannot convert NaN to int.
    """
    val, u = normalize_unit_value(float("nan"), unit)
    assert isinstance(val, float)
    assert isinstance(u, str)


@given(unit=_arbitrary_unit, sign=st.sampled_from([1.0, -1.0]))
@settings(max_examples=100)
def test_infinity_input_does_not_raise(unit: str, sign: float) -> None:
    """Regression: ``_round_converted`` raised ``OverflowError`` on ±inf
    because ``math.floor(math.log10(inf))`` cannot convert inf to int.
    """
    val, u = normalize_unit_value(sign * math.inf, unit)
    assert isinstance(val, float)
    assert isinstance(u, str)


# --- Contract 8: linear-conversion sign symmetry -------------------------


@given(
    value=st.floats(
        allow_nan=False,
        allow_infinity=False,
        min_value=1e-6,
        max_value=1e3,
    ),
    alias=st.sampled_from(LINEAR_ALIASES),
)
@settings(max_examples=100)
def test_linear_sign_symmetry(value: float, alias: str) -> None:
    val_pos, _ = normalize_unit_value(value, alias)
    val_neg, _ = normalize_unit_value(-value, alias)
    # _round_converted rounds to 5 sig figs; tolerate that as a tiny
    # asymmetry near boundaries.
    assert val_neg == pytest.approx(-val_pos, rel=1e-6, abs=1e-12)
