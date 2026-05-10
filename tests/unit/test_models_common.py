"""Tests for the structured ValueUnit / MinMaxUnit classes in models/common.py."""

import pytest

from specodex.models.common import (
    Current,
    MinMaxUnit,
    Voltage,
    ValueUnit,
    VoltageRange,
    _coerce_dict_to_min_max_unit_dict,
    _coerce_dict_to_value_unit_dict,
    _coerce_ip_rating,
    _coerce_str_to_min_max_unit_dict,
    _coerce_str_to_value_unit_dict,
)
from pydantic import BaseModel
from typing import Optional


class TestValueUnit:
    """The structured ValueUnit BaseModel."""

    def test_dict_with_value_and_unit(self):
        v = ValueUnit.model_validate({"value": 100, "unit": "W"})
        assert v.value == 100.0
        assert v.unit == "W"

    def test_dict_cleans_plus_signs(self):
        v = ValueUnit.model_validate({"value": "100+", "unit": "W"})
        assert v.value == 100.0
        assert v.unit == "W"

    def test_dict_cleans_tilde(self):
        v = ValueUnit.model_validate({"value": "~50", "unit": "rpm"})
        assert v.value == 50.0
        assert v.unit == "rpm"

    def test_dict_with_min_collapses_to_value(self):
        v = ValueUnit.model_validate({"min": 10, "unit": "V"})
        assert v.value == 10.0
        assert v.unit == "V"

    def test_dict_with_min_max_collapses_to_min(self):
        v = ValueUnit.model_validate({"min": 10, "max": 50, "unit": "V"})
        assert v.value == 10.0
        assert v.unit == "V"

    def test_space_separated_string(self):
        v = ValueUnit.model_validate("100 W")
        assert v.value == 100.0
        assert v.unit == "W"

    def test_compact_string_legacy(self):
        v = ValueUnit.model_validate("100;W")
        assert v.value == 100.0
        assert v.unit == "W"

    def test_normalizes_unit(self):
        v = ValueUnit.model_validate({"value": 500, "unit": "mNm"})
        assert v.value == 0.5
        assert v.unit == "Nm"

    def test_empty_dict_rejected(self):
        with pytest.raises(Exception):
            ValueUnit.model_validate({})

    def test_unit_only_dict_rejected(self):
        with pytest.raises(Exception):
            ValueUnit.model_validate({"unit": "V"})

    def test_serialises_as_dict(self):
        v = ValueUnit(value=100, unit="W")
        assert v.model_dump() == {"value": 100.0, "unit": "W"}

    def test_scientific_notation_passes_through(self):
        """The semicolon-canary case from the original bug report."""
        v = ValueUnit.model_validate({"value": 5.5e-5, "unit": "kg·cm²"})
        assert v.value == 5.5e-5
        assert v.unit == "kg·cm²"


class TestMinMaxUnit:
    """The structured MinMaxUnit BaseModel."""

    def test_dict_with_min_max_unit(self):
        v = MinMaxUnit.model_validate({"min": 0, "max": 100, "unit": "°C"})
        assert v.min == 0.0
        assert v.max == 100.0
        assert v.unit == "°C"

    def test_dict_with_min_only(self):
        v = MinMaxUnit.model_validate({"min": -20, "unit": "°C"})
        assert v.min == -20.0
        assert v.max is None
        assert v.unit == "°C"

    def test_dict_with_value_unit_collapses(self):
        """ValueUnit shape arriving on a MinMaxUnit field becomes min-only."""
        v = MinMaxUnit.model_validate({"value": 24, "unit": "V"})
        assert v.min == 24.0
        assert v.max is None
        assert v.unit == "V"

    def test_compact_range_string(self):
        v = MinMaxUnit.model_validate("100-240;V")
        assert v.min == 100.0
        assert v.max == 240.0
        assert v.unit == "V"

    def test_to_separator_in_string(self):
        v = MinMaxUnit.model_validate("10 to 50;V")
        assert v.min == 10.0
        assert v.max == 50.0

    def test_unit_only_dict_rejected(self):
        with pytest.raises(Exception):
            MinMaxUnit.model_validate({"unit": "V"})

    def test_no_min_max_rejected(self):
        with pytest.raises(Exception):
            MinMaxUnit.model_validate({"value": None, "unit": "V"})


class TestTypedAliases:
    """Typed aliases (Voltage, Current, ...) drop wrong-family units to None."""

    class _Probe(BaseModel):
        v: Optional[ValueUnit] = None  # type: ignore[assignment]
        # Wrap typed alias inside a real model so the BeforeValidator runs.

    def test_voltage_accepts_voltage_unit(self):
        class M(BaseModel):
            v: Voltage = None

        m = M(v={"value": 100, "unit": "V"})
        assert m.v.value == 100.0
        assert m.v.unit == "V"

    def test_voltage_rejects_wrong_family(self):
        class M(BaseModel):
            v: Voltage = None

        m = M(v={"value": 100, "unit": "rpm"})
        assert m.v is None

    def test_current_accepts_mA_normalised(self):
        class M(BaseModel):
            v: Current = None

        m = M(v={"value": 500, "unit": "mA"})
        assert m.v.value == 0.5
        assert m.v.unit == "A"

    def test_voltage_range_accepts(self):
        class M(BaseModel):
            v: VoltageRange = None

        m = M(v={"min": 100, "max": 240, "unit": "V"})
        assert m.v.min == 100.0
        assert m.v.max == 240.0
        assert m.v.unit == "V"

    def test_voltage_range_rejects_wrong_family(self):
        class M(BaseModel):
            v: VoltageRange = None

        m = M(v={"min": 0, "max": 50, "unit": "°C"})
        assert m.v is None


class TestStrCoercer:
    def test_compact(self):
        assert _coerce_str_to_value_unit_dict("100;W") == {"value": 100.0, "unit": "W"}

    def test_space(self):
        assert _coerce_str_to_value_unit_dict("100 W") == {"value": 100.0, "unit": "W"}

    def test_empty_returns_none(self):
        assert _coerce_str_to_value_unit_dict("") is None

    def test_garbage_returns_none(self):
        assert _coerce_str_to_value_unit_dict("approx 5") is None


class TestDictCoercer:
    def test_value_unit(self):
        assert _coerce_dict_to_value_unit_dict({"value": 1, "unit": "V"}) == {
            "value": 1.0,
            "unit": "V",
        }

    def test_empty_returns_none(self):
        assert _coerce_dict_to_value_unit_dict({}) is None

    def test_min_max_collapses(self):
        d = _coerce_dict_to_value_unit_dict({"min": 1, "max": 5, "unit": "V"})
        assert d == {"value": 1.0, "unit": "V"}


class TestMinMaxStrCoercer:
    def test_range(self):
        assert _coerce_str_to_min_max_unit_dict("10-50;V") == {
            "min": 10.0,
            "max": 50.0,
            "unit": "V",
        }

    def test_to_separator(self):
        assert _coerce_str_to_min_max_unit_dict("10 to 50;V") == {
            "min": 10.0,
            "max": 50.0,
            "unit": "V",
        }


class TestMinMaxDictCoercer:
    def test_min_max(self):
        assert _coerce_dict_to_min_max_unit_dict({"min": 1, "max": 5, "unit": "V"}) == {
            "min": 1.0,
            "max": 5.0,
            "unit": "V",
        }

    def test_unit_only_returns_none(self):
        assert _coerce_dict_to_min_max_unit_dict({"unit": "V"}) is None


class TestCoerceIpRating:
    def test_none_passthrough(self):
        assert _coerce_ip_rating(None) is None

    def test_int_passthrough(self):
        assert _coerce_ip_rating(54) == 54

    def test_plain_digit_string(self):
        assert _coerce_ip_rating("54") == 54

    def test_ip_prefix_string(self):
        assert _coerce_ip_rating("IP54") == 54

    def test_ip_prefix_lowercase(self):
        assert _coerce_ip_rating("ip67") == 67

    def test_legacy_value_dict(self):
        assert _coerce_ip_rating({"value": 65, "unit": "IP"}) == 65

    def test_legacy_min_dict(self):
        assert _coerce_ip_rating({"min": 54, "unit": ""}) == 54

    def test_garbage_becomes_none(self):
        assert _coerce_ip_rating("unknown") is None

    def test_list_input_becomes_none(self):
        """Hypothesis-found regression: list input was returning the list
        unchanged instead of None. The docstring says "Anything else
        becomes None" but the original fall-through `return v` violated
        that contract for lists, tuples, and floats."""
        assert _coerce_ip_rating([]) is None
        assert _coerce_ip_rating([1, 2, 3]) is None

    def test_tuple_input_becomes_none(self):
        assert _coerce_ip_rating((54,)) is None

    def test_float_input_becomes_none(self):
        assert _coerce_ip_rating(54.0) is None

    def test_bool_input_becomes_none(self):
        """bool is an int subclass in Python — without an explicit check,
        ``True``/``False`` slip through ``isinstance(v, int)`` and become
        IP ratings of 1/0. Real IP ratings are two digits; treat bools
        as garbage."""
        assert _coerce_ip_rating(True) is None
        assert _coerce_ip_rating(False) is None

    def test_bare_dict_becomes_none(self):
        assert _coerce_ip_rating({"unit": "IP"}) is None
