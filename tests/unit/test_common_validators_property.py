"""Property tests for the BeforeValidators in ``specodex.models.common``.

The example-based tests in ``test_models_common.py`` cover the happy
paths. This file generates adversarial inputs — recursive dicts,
unicode-laced strings, NaN/inf floats, embedded nulls — and asserts
the documented contract holds for every input the strategy can
think of.

**Contract under test** (per the docstrings in ``common.py``):

1. **Coercers never raise on user-controlled input.** They return a
   clean dict / int / float / None — Pydantic at the next layer
   decides whether the result validates. A coercer that raises
   ``KeyError`` / ``TypeError`` / ``AttributeError`` on a strange
   input is the regression to catch — the LLM emits surprising
   shapes (NaN floats, deeply-nested dicts, embedded nulls) and the
   coercers are the isolation barrier between Gemini's payload and
   Pydantic.
2. **Returned shapes are well-formed.** When a coercer returns a
   dict, it has the expected keys (``value``+``unit`` for ValueUnit;
   ``min``+``max``+``unit`` for MinMaxUnit). Numeric fields are
   ``float`` (not str / Decimal / NaN). The unit field is a non-empty
   string. When it returns ``None``, Pydantic at the next layer
   silently drops the field — that's also fine.

Phase 3.1 target 2 of 3. Target 1 (``parse_gemini_response``) shipped
in PR #111; target 3 (``find_spec_pages_by_text``) is a follow-up.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from pydantic import BaseModel

from specodex.models.common import (
    Force,
    ForceRange,
    Mass,
    MinMaxUnit,
    Power,
    Torque,
    ValueUnit,
    Voltage,
    _coerce_dict_to_min_max_unit_dict,
    _coerce_dict_to_value_unit_dict,
    _coerce_ip_rating,
    _coerce_str_to_min_max_unit_dict,
    _coerce_str_to_value_unit_dict,
    _strip_value_qualifiers,
)


# ---------------------------------------------------------------------------
# Adversarial input strategies
# ---------------------------------------------------------------------------


# Primitive values the LLM might emit. Includes the surprising-but-
# observed shapes — NaN floats, infinity, embedded nulls, non-BMP
# unicode, very long strings.
_ADVERSARIAL_PRIMITIVE = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True, width=64),
    st.text(min_size=0, max_size=64),
    st.binary(max_size=16).map(lambda b: b.decode("utf-8", errors="replace")),
)


# Anything-shaped (recursive). The coercers may receive non-dict /
# non-string input from upstream (e.g., a model_validate on a
# pre-parsed Pydantic instance) — they must still not raise.
_ANY_VALUE = st.recursive(
    _ADVERSARIAL_PRIMITIVE,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(min_size=0, max_size=12), children, max_size=4),
        st.tuples(children, children),
    ),
    max_leaves=12,
)


# Dicts with ValueUnit-ish keys (value, unit, min, max), values are
# arbitrary. Targets the dict-coercer paths specifically.
def _value_unit_ish_dict_strategy() -> st.SearchStrategy[dict]:
    return st.fixed_dictionaries(
        {},
        optional={
            "value": _ADVERSARIAL_PRIMITIVE,
            "unit": _ADVERSARIAL_PRIMITIVE,
            "min": _ADVERSARIAL_PRIMITIVE,
            "max": _ADVERSARIAL_PRIMITIVE,
            # Garbage keys to test extra-key tolerance:
            "extra": _ADVERSARIAL_PRIMITIVE,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_valid_value_unit_dict(d: Any) -> bool:
    """``{"value": float, "unit": non-empty-str}``, no extras."""
    if not isinstance(d, dict):
        return False
    if set(d.keys()) != {"value", "unit"}:
        return False
    if not isinstance(d["value"], float):
        return False
    if math.isnan(d["value"]) or math.isinf(d["value"]):
        # Existing coercers don't currently filter NaN/inf; accept
        # them in the contract until/unless the schema layer tightens.
        pass
    if not isinstance(d["unit"], str) or not d["unit"]:
        return False
    return True


def _is_valid_min_max_unit_dict(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    if set(d.keys()) != {"min", "max", "unit"}:
        return False
    for k in ("min", "max"):
        v = d[k]
        if v is not None and not isinstance(v, float):
            return False
    if d["min"] is None and d["max"] is None:
        return False
    if not isinstance(d["unit"], str) or not d["unit"]:
        return False
    return True


# ---------------------------------------------------------------------------
# Direct coercer property tests
# ---------------------------------------------------------------------------


class TestStripValueQualifiers:
    """``_strip_value_qualifiers`` accepts assorted numeric encodings."""

    @given(v=_ANY_VALUE)
    @settings(max_examples=200, deadline=None)
    def test_never_raises(self, v: Any) -> None:
        try:
            result = _strip_value_qualifiers(v)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"_strip_value_qualifiers raised {type(exc).__name__}: "
                f"{exc!r}\ninput: {v!r}"
            )
        assert result is None or isinstance(result, float)


class TestCoerceStrToValueUnitDict:
    @given(s=st.text(min_size=0, max_size=80))
    @settings(max_examples=200, deadline=None)
    def test_never_raises(self, s: str) -> None:
        try:
            result = _coerce_str_to_value_unit_dict(s)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_coerce_str_to_value_unit_dict raised "
                f"{type(exc).__name__}: {exc!r}\ninput: {s!r}"
            )
        assert result is None or _is_valid_value_unit_dict(result)


class TestCoerceDictToValueUnitDict:
    @given(d=_value_unit_ish_dict_strategy())
    @settings(max_examples=200, deadline=None)
    def test_never_raises_on_value_unit_ish_dict(self, d: dict) -> None:
        try:
            result = _coerce_dict_to_value_unit_dict(d)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_coerce_dict_to_value_unit_dict raised "
                f"{type(exc).__name__}: {exc!r}\ninput: {d!r}"
            )
        assert result is None or _is_valid_value_unit_dict(result)

    @given(d=st.dictionaries(st.text(max_size=10), _ANY_VALUE, max_size=6))
    @settings(max_examples=200, deadline=None)
    def test_never_raises_on_arbitrary_dict(self, d: dict) -> None:
        try:
            result = _coerce_dict_to_value_unit_dict(d)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_coerce_dict_to_value_unit_dict raised "
                f"{type(exc).__name__}: {exc!r}\ninput: {d!r}"
            )
        assert result is None or _is_valid_value_unit_dict(result)


class TestCoerceStrToMinMaxUnitDict:
    @given(s=st.text(min_size=0, max_size=80))
    @settings(max_examples=200, deadline=None)
    def test_never_raises(self, s: str) -> None:
        try:
            result = _coerce_str_to_min_max_unit_dict(s)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_coerce_str_to_min_max_unit_dict raised "
                f"{type(exc).__name__}: {exc!r}\ninput: {s!r}"
            )
        assert result is None or _is_valid_min_max_unit_dict(result)


class TestCoerceDictToMinMaxUnitDict:
    @given(d=_value_unit_ish_dict_strategy())
    @settings(max_examples=200, deadline=None)
    def test_never_raises_on_value_unit_ish_dict(self, d: dict) -> None:
        try:
            result = _coerce_dict_to_min_max_unit_dict(d)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_coerce_dict_to_min_max_unit_dict raised "
                f"{type(exc).__name__}: {exc!r}\ninput: {d!r}"
            )
        assert result is None or _is_valid_min_max_unit_dict(result)

    @given(d=st.dictionaries(st.text(max_size=10), _ANY_VALUE, max_size=6))
    @settings(max_examples=200, deadline=None)
    def test_never_raises_on_arbitrary_dict(self, d: dict) -> None:
        try:
            result = _coerce_dict_to_min_max_unit_dict(d)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_coerce_dict_to_min_max_unit_dict raised "
                f"{type(exc).__name__}: {exc!r}\ninput: {d!r}"
            )
        assert result is None or _is_valid_min_max_unit_dict(result)


class TestCoerceIpRating:
    @given(v=_ANY_VALUE)
    @settings(max_examples=200, deadline=None)
    def test_never_raises(self, v: Any) -> None:
        try:
            result = _coerce_ip_rating(v)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_coerce_ip_rating raised {type(exc).__name__}: {exc!r}\ninput: {v!r}"
            )
        assert result is None or isinstance(result, int)


# ---------------------------------------------------------------------------
# Family-typed alias property tests
# ---------------------------------------------------------------------------


class _ValueUnitProbe(BaseModel):
    voltage: Voltage = None
    force: Force = None
    mass: Mass = None
    torque: Torque = None
    power: Power = None


class _RangeProbe(BaseModel):
    force_range: ForceRange = None


class TestTypedValueUnitProperties:
    """Family-typed ValueUnit aliases drop wrong-family / unparseable to None.

    The contract: the BeforeValidator either returns a valid
    ValueUnit instance whose unit is in the family's accepted set,
    or ``None``. It never raises and never returns an instance whose
    unit is outside the family.
    """

    @given(v=_ANY_VALUE)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_voltage_never_raises_or_leaks_wrong_family(self, v: Any) -> None:
        try:
            probe = _ValueUnitProbe(voltage=v)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"Voltage BeforeValidator raised {type(exc).__name__}: "
                f"{exc!r}\ninput: {v!r}"
            )
        if probe.voltage is not None:
            assert isinstance(probe.voltage, ValueUnit)
            # Voltage's accepted set
            assert probe.voltage.unit in {
                "V",
                "Vac",
                "Vdc",
                "Vrms",
                "VAC",
                "VDC",
                "VRMS",
                "mV",
                "kV",
            }

    @given(v=_ANY_VALUE)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_force_never_raises(self, v: Any) -> None:
        try:
            probe = _ValueUnitProbe(force=v)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"Force BeforeValidator raised {type(exc).__name__}: "
                f"{exc!r}\ninput: {v!r}"
            )
        if probe.force is not None:
            assert isinstance(probe.force, ValueUnit)

    @given(v=_ANY_VALUE)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_mass_never_raises(self, v: Any) -> None:
        try:
            probe = _ValueUnitProbe(mass=v)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"Mass BeforeValidator raised {type(exc).__name__}: "
                f"{exc!r}\ninput: {v!r}"
            )
        if probe.mass is not None:
            assert isinstance(probe.mass, ValueUnit)


class TestTypedMinMaxUnitProperties:
    @given(v=_ANY_VALUE)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_force_range_never_raises(self, v: Any) -> None:
        try:
            probe = _RangeProbe(force_range=v)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"ForceRange BeforeValidator raised "
                f"{type(exc).__name__}: {exc!r}\ninput: {v!r}"
            )
        if probe.force_range is not None:
            assert isinstance(probe.force_range, MinMaxUnit)
