"""Tests for the structured ValueUnit / MinMaxUnit classes in models/common.py."""

import pytest

from specodex.models.common import (
    Current,
    Force,
    ForceRange,
    Mass,
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


class TestForceKgCoercion:
    """Force fields rewrite ``kg`` to ``kgf`` (catalog convention).

    Lintech and similar industrial vendors publish load capacities as
    ``703 kg`` when they mean kilogram-force, not mass. The Force
    family's ``pre_rewrite`` table coerces this BEFORE the family
    check so the existing ``kgf → N`` (×9.80665) normaliser handles
    the conversion in one pass. The rewrite is family-scoped — Mass
    fields still treat ``kg`` as canonical.
    """

    def test_force_dict_kg_coerces_to_N(self):
        class M(BaseModel):
            v: Force = None

        m = M(v={"value": 703, "unit": "kg"})
        assert m.v is not None
        assert m.v.unit == "N"
        # 703 kgf × 9.80665 = 6,892.0... N
        assert m.v.value == pytest.approx(6892.07, rel=1e-3)

    def test_force_dict_kgf_still_works(self):
        class M(BaseModel):
            v: Force = None

        m = M(v={"value": 100, "unit": "kgf"})
        assert m.v is not None
        assert m.v.unit == "N"
        assert m.v.value == pytest.approx(980.665, rel=1e-3)

    def test_force_dict_N_still_canonical(self):
        class M(BaseModel):
            v: Force = None

        m = M(v={"value": 500, "unit": "N"})
        assert m.v is not None
        assert m.v.unit == "N"
        assert m.v.value == 500.0

    def test_mass_field_kg_still_canonical(self):
        class M(BaseModel):
            v: Mass = None

        m = M(v={"value": 5, "unit": "kg"})
        assert m.v is not None
        assert m.v.unit == "kg"
        assert m.v.value == 5.0

    def test_voltage_field_rejects_kg(self):
        class M(BaseModel):
            v: Voltage = None

        m = M(v={"value": 100, "unit": "kg"})
        assert m.v is None

    def test_force_value_unit_instance_kg_rewrites(self):
        class M(BaseModel):
            v: Force = None

        vu = ValueUnit(value=50, unit="kg")
        m = M(v=vu)
        assert m.v is not None
        assert m.v.unit == "N"
        assert m.v.value == pytest.approx(490.33, rel=1e-3)

    def test_force_min_max_kg_coerces_via_kgf(self):
        class M(BaseModel):
            v: Force = None

        m = M(v={"min": 100, "max": 500, "unit": "kg"})
        assert m.v is not None
        assert m.v.unit == "N"
        # collapses to scalar = min after passing the family check
        assert m.v.value == pytest.approx(980.665, rel=1e-3)

    def test_force_range_kg_coerces_via_kgf(self):
        class M(BaseModel):
            v: ForceRange = None

        m = M(v={"min": 100, "max": 500, "unit": "kg"})
        assert m.v is not None
        assert m.v.unit == "N"
        assert m.v.min == pytest.approx(980.665, rel=1e-3)
        assert m.v.max == pytest.approx(4903.32, rel=1e-3)

    def test_force_kg_emits_warning(self, caplog):
        class M(BaseModel):
            v: Force = None

        with caplog.at_level("WARNING", logger="specodex.models.common"):
            M(v={"value": 703, "unit": "kg"})
        assert any(
            "force field" in rec.message.lower()
            and "'kg'" in rec.message
            and "'kgf'" in rec.message
            for rec in caplog.records
        )

    def test_force_kgf_emits_no_rewrite_warning(self, caplog):
        class M(BaseModel):
            v: Force = None

        with caplog.at_level("WARNING", logger="specodex.models.common"):
            M(v={"value": 100, "unit": "kgf"})
        assert not any("rewriting unit" in rec.message for rec in caplog.records)


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


@pytest.mark.unit
class TestLegacyCompactStringRecovery:
    """Regression (2026-07-22): 217 robot_arm rows + ~150 other rows on
    products-dev were invisible because ONE legacy compact-string field
    failed coercion and killed the whole row. Shapes below are copied
    verbatim from the failing DB rows."""

    # -- Unicode qualifiers (≤/≥) previously not stripped --
    def test_leq_qualifier_value_unit(self) -> None:
        assert _coerce_str_to_value_unit_dict("≤ 3;arc-min") == {
            "value": 3.0,
            "unit": "arc-min",
        }

    def test_leq_qualifier_no_space(self) -> None:
        got = _coerce_str_to_value_unit_dict("≤55;dB (1.000 rpm) / 65 dB (3.000 rpm)")
        assert got is not None
        assert got["value"] == 55.0

    def test_geq_qualifier(self) -> None:
        assert _coerce_str_to_value_unit_dict("≥ 20;kHz") == {
            "value": 20.0,
            "unit": "kHz",
        }

    # -- ± with degree glyph + literal "null" unit ("±180°;null") --
    def test_plus_minus_degree_null_unit_value_unit(self) -> None:
        assert _coerce_str_to_value_unit_dict("±180°;null") == {
            "value": 180.0,
            "unit": "°",
        }

    def test_plus_minus_range_min_max(self) -> None:
        assert _coerce_str_to_min_max_unit_dict("±180°;null") == {
            "min": -180.0,
            "max": 180.0,
            "unit": "°",
        }

    def test_plus_minus_plain_unit(self) -> None:
        assert _coerce_str_to_min_max_unit_dict("±0.02;mm") == {
            "min": -0.02,
            "max": 0.02,
            "unit": "mm",
        }

    # -- negative-to-positive range with glyph + null unit --
    def test_negative_range_degree_null_unit_min_max(self) -> None:
        assert _coerce_str_to_min_max_unit_dict("-90-135°;null") == {
            "min": -90.0,
            "max": 135.0,
            "unit": "°",
        }

    def test_negative_range_collapses_to_min_for_value_unit(self) -> None:
        # Matches the MinMaxUnit → ValueUnit collapse convention
        # (scalar = min when present).
        assert _coerce_str_to_value_unit_dict("-90-135°;null") == {
            "value": -90.0,
            "unit": "°",
        }

    # -- trailing parenthetical annotation on the value side --
    def test_trailing_parenthetical_stripped(self) -> None:
        assert _coerce_str_to_value_unit_dict("340 (360 option);degrees") == {
            "value": 340.0,
            "unit": "degrees",
        }

    def test_parenthetical_only_still_none(self) -> None:
        assert _coerce_str_to_value_unit_dict("(360 option);degrees") is None

    # -- slash-separated per-variant alternatives --
    def test_slash_alternatives_take_first(self) -> None:
        assert _coerce_str_to_value_unit_dict("±180 / ±165;°") == {
            "value": 180.0,
            "unit": "°",
        }

    # -- explicit + sign on the upper bound ("-160-+65;deg") --
    def test_signed_upper_bound_range(self) -> None:
        assert _coerce_str_to_min_max_unit_dict("-160-+65;deg") == {
            "min": -160.0,
            "max": 65.0,
            "unit": "deg",
        }

    def test_signed_upper_bound_collapses_for_value_unit(self) -> None:
        assert _coerce_str_to_value_unit_dict("-51-+190;deg") == {
            "value": -51.0,
            "unit": "deg",
        }

    # -- empty-first slash segment ("/-140;°") takes next parseable --
    def test_empty_first_slash_segment(self) -> None:
        assert _coerce_str_to_value_unit_dict("/-140;°") == {
            "value": -140.0,
            "unit": "°",
        }

    # -- real "null" unit with no recoverable glyph stays dead --
    def test_null_unit_without_glyph_still_none(self) -> None:
        assert _coerce_str_to_value_unit_dict("garbage;null") is None

    def test_row_level_recovery_robot_arm_joint(self) -> None:
        """The actual failing joints shape from products-dev."""
        from specodex.models.robot_arm import JointSpecs

        j = JointSpecs.model_validate(
            {
                "joint_name": "J1",
                "working_range": "±180°;null",
                "max_speed": {"value": "160", "unit": "°/s"},
            }
        )
        assert j.working_range is not None
        assert j.working_range.value == 180.0
        assert j.working_range.unit == "°"
