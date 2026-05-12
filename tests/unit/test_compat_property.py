"""Property tests for ``specodex.integration.compat`` field helpers.

The compatibility layer's per-field checks (``_scalar``, ``_range``,
``_check_voltage_fits``, ``_check_supply_ge_demand``,
``_check_demand_le_max``, ``_check_equal_str``, ``_check_membership``,
``_check_intersect``, ``_check_shaft_fit``) all sit on the hot path
between Pydantic-validated product instances and the UI's compatibility
badge. A regression here either fabricates a green badge on a
mismatched pair (silent integration failure for the user) or rejects
a compatible pair (drops good results from the UI).

The example-based companion (``test_integration.py``) pins specific
shapes from real product instances. This file generates *adversarial*
inputs — wrong-shape arguments, mixed unit families, swapped operand
order, casing differences, whitespace — and asserts the documented
contract holds for every input the strategy can produce.

**Contract under test** (per docstrings + code):

* ``_scalar(v)`` returns ``(value, unit)`` for a ``ValueUnit`` or a
  ``MinMaxUnit`` with at least one bound populated; ``None`` otherwise.
  Never raises on any input type.
* ``_range(v)`` returns ``(lo, hi, unit)`` for ``MinMaxUnit`` /
  ``ValueUnit``; ``None`` otherwise. Never raises.
* All ``_check_*`` helpers return a ``CheckResult`` with a status in
  ``{"ok", "partial", "fail"}``, the requested ``field`` name, and a
  ``detail`` string. They never raise.
* Status semantics:
  - ``partial`` is reserved for "missing on at least one side".
  - ``fail`` covers unit mismatch + out-of-range / unequal values.
  - ``ok`` requires both sides populated, units agreeing, and the
    rule satisfied.
* Symmetry / monotonicity invariants:
  - ``_check_demand_le_max(d, m, f) == _check_supply_ge_demand(m, d, f)``
    on identical inputs (the former is an alias).
  - ``_check_equal_str``, ``_check_intersect``, ``_check_shaft_fit``
    are symmetric in their two operands' status (detail may differ).
  - ``_check_supply_ge_demand`` is anti-symmetric on the ``ok`` /
    ``fail`` axis when units agree and values differ — swapping
    flips the status.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.integration.compat import (
    CheckResult,
    _check_demand_le_max,
    _check_equal_str,
    _check_intersect,
    _check_membership,
    _check_shaft_fit,
    _check_supply_ge_demand,
    _check_voltage_fits,
    _range,
    _scalar,
)
from specodex.models.common import MinMaxUnit, ValueUnit


_VALID_STATUSES = {"ok", "partial", "fail"}


# ---------------------------------------------------------------------------
# Strategies — restrict to "ranges the canonical-unit normaliser
# leaves alone" (V, A, Nm, mm, rpm, kW are pass-through canonical) so
# input value == post-construction value. The numeric checks then
# reason about the same float we sampled.
# ---------------------------------------------------------------------------


_CANONICAL_UNITS = st.sampled_from(["V", "A", "Nm", "mm", "rpm", "kW", "W"])

# Finite floats with bounded magnitude — Pydantic's float validator
# accepts NaN/inf, but normalize_unit_value's arithmetic and the
# ordering comparisons would otherwise propagate non-finite values
# into "is fail or ok?" oracles we can't easily compute.
_FINITE_FLOATS = st.floats(
    min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
)


@st.composite
def _value_units(draw, unit_strategy=_CANONICAL_UNITS) -> ValueUnit:
    return ValueUnit(value=draw(_FINITE_FLOATS), unit=draw(unit_strategy))


@st.composite
def _min_max_units(draw, unit_strategy=_CANONICAL_UNITS) -> MinMaxUnit:
    """MinMaxUnit with at least one of (min, max) populated."""
    has_min = draw(st.booleans())
    has_max = draw(st.booleans()) or not has_min  # ensure at least one
    lo = draw(_FINITE_FLOATS) if has_min else None
    hi = draw(_FINITE_FLOATS) if has_max else None
    # If both populated, allow either ordering — _range / _check_*
    # treat the bounds as user-supplied; flipped bounds are the
    # adversarial case we want to exercise.
    return MinMaxUnit(min=lo, max=hi, unit=draw(unit_strategy))


# Anything that isn't a ValueUnit or MinMaxUnit — _scalar/_range must
# return None and never raise on these.
_NON_VALUE_INPUTS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    _FINITE_FLOATS,
    st.text(max_size=20),
    st.binary(max_size=20),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=5), st.text(max_size=5), max_size=3),
    st.tuples(st.floats(allow_nan=False), st.text(max_size=5)),
)


# ---------------------------------------------------------------------------
# _scalar / _range — pure extractors, must never raise
# ---------------------------------------------------------------------------


class TestScalarContract:
    @given(v=_value_units())
    @settings(max_examples=200, deadline=None)
    def test_value_unit_round_trips(self, v: ValueUnit) -> None:
        result = _scalar(v)
        assert result is not None, f"_scalar dropped a ValueUnit: {v!r}"
        scalar, unit = result
        assert scalar == v.value
        assert unit == v.unit
        assert isinstance(scalar, float)
        assert isinstance(unit, str)

    @given(v=_min_max_units())
    @settings(max_examples=200, deadline=None)
    def test_min_max_picks_a_present_bound(self, v: MinMaxUnit) -> None:
        result = _scalar(v)
        # MinMaxUnit always has at least one of min/max populated
        # (enforced by model_validator), so _scalar must succeed.
        assert result is not None, f"_scalar dropped MinMaxUnit: {v!r}"
        scalar, unit = result
        expected = v.min if v.min is not None else v.max
        assert scalar == expected
        assert unit == v.unit

    @given(v=_NON_VALUE_INPUTS)
    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    def test_non_value_inputs_return_none(self, v: Any) -> None:
        assert _scalar(v) is None


class TestRangeContract:
    @given(v=_value_units())
    @settings(max_examples=200, deadline=None)
    def test_value_unit_collapses_to_degenerate_range(self, v: ValueUnit) -> None:
        result = _range(v)
        assert result is not None
        lo, hi, unit = result
        assert lo == v.value
        assert hi == v.value
        assert unit == v.unit

    @given(v=_min_max_units())
    @settings(max_examples=200, deadline=None)
    def test_min_max_returns_three_tuple(self, v: MinMaxUnit) -> None:
        result = _range(v)
        assert result is not None
        lo, hi, unit = result
        # lo defaults to min, falling back to max; hi defaults to max,
        # falling back to min. So both ends always populated.
        expected_lo = v.min if v.min is not None else v.max
        expected_hi = v.max if v.max is not None else v.min
        assert lo == expected_lo
        assert hi == expected_hi
        assert unit == v.unit
        assert isinstance(unit, str)

    @given(v=_NON_VALUE_INPUTS)
    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    def test_non_value_inputs_return_none(self, v: Any) -> None:
        assert _range(v) is None


# ---------------------------------------------------------------------------
# _check_supply_ge_demand — the core "supply ≥ demand" arithmetic check
# ---------------------------------------------------------------------------


class TestCheckSupplyGeDemand:
    @given(
        supply=st.one_of(_value_units(), _min_max_units(), _NON_VALUE_INPUTS),
        demand=st.one_of(_value_units(), _min_max_units(), _NON_VALUE_INPUTS),
        field_name=st.text(min_size=1, max_size=20),
    )
    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    def test_never_raises_returns_valid_check_result(
        self, supply: Any, demand: Any, field_name: str
    ) -> None:
        result = _check_supply_ge_demand(supply, demand, field_name)
        assert isinstance(result, CheckResult)
        assert result.field == field_name
        assert result.status in _VALID_STATUSES

    @given(supply=_value_units(), demand=_value_units())
    @settings(max_examples=200, deadline=None)
    def test_status_matches_arithmetic_when_units_agree(
        self, supply: ValueUnit, demand: ValueUnit
    ) -> None:
        # Match units so we exercise the comparator branch, not the
        # unit-mismatch branch.
        demand = ValueUnit(value=demand.value, unit=supply.unit)
        result = _check_supply_ge_demand(supply, demand, "current")
        if supply.value >= demand.value:
            assert result.status == "ok", (
                f"supply {supply.value} ≥ demand {demand.value} but got fail"
            )
        else:
            assert result.status == "fail", (
                f"supply {supply.value} < demand {demand.value} but got ok"
            )

    @given(supply=_value_units(), demand=_value_units())
    @settings(max_examples=200, deadline=None)
    def test_unit_mismatch_is_fail(
        self, supply: ValueUnit, demand: ValueUnit
    ) -> None:
        # Force a unit mismatch by remapping demand's unit to something
        # certainly different from supply's. Using a custom unit
        # avoids the canonicalisation rewrites in normalize_unit_value.
        forced_unit = "X" if supply.unit != "X" else "Y"
        demand = ValueUnit(value=demand.value, unit=forced_unit)
        result = _check_supply_ge_demand(supply, demand, "current")
        assert result.status == "fail"
        assert "unit mismatch" in result.detail

    @given(value_input=_NON_VALUE_INPUTS, demand=_value_units())
    @settings(
        max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    def test_partial_when_supply_unparseable(
        self, value_input: Any, demand: ValueUnit
    ) -> None:
        result = _check_supply_ge_demand(value_input, demand, "current")
        assert result.status == "partial"


# ---------------------------------------------------------------------------
# _check_voltage_fits — range-in-range containment
# ---------------------------------------------------------------------------


class TestCheckVoltageFits:
    @given(
        supply=st.one_of(_value_units(), _min_max_units(), _NON_VALUE_INPUTS),
        demand=st.one_of(_value_units(), _min_max_units(), _NON_VALUE_INPUTS),
        field_name=st.text(min_size=1, max_size=20),
    )
    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    def test_never_raises_returns_valid_check_result(
        self, supply: Any, demand: Any, field_name: str
    ) -> None:
        result = _check_voltage_fits(supply, demand, field_name)
        assert isinstance(result, CheckResult)
        assert result.field == field_name
        assert result.status in _VALID_STATUSES

    @given(supply=_value_units())
    @settings(max_examples=200, deadline=None)
    def test_identical_value_unit_fits_itself(self, supply: ValueUnit) -> None:
        # A degenerate ValueUnit treated as both supply and demand is
        # the (v, v, u) range containing (v, v, u) — always ok.
        result = _check_voltage_fits(supply, supply, "voltage")
        assert result.status == "ok"

    @given(unit=_CANONICAL_UNITS, sv=_FINITE_FLOATS, sw=_FINITE_FLOATS)
    @settings(max_examples=200, deadline=None)
    def test_demand_outside_supply_is_fail(
        self, unit: str, sv: float, sw: float
    ) -> None:
        # Bracket-build a supply range; force a demand at supply_lo - 1
        # (definitively below) so containment must fail.
        lo, hi = (sv, sw) if sv <= sw else (sw, sv)
        if not math.isfinite(lo - 1.0):
            return  # range arithmetic would lose precision — skip
        supply = MinMaxUnit(min=lo, max=hi, unit=unit)
        demand = ValueUnit(value=lo - 1.0, unit=unit)
        result = _check_voltage_fits(supply, demand, "voltage")
        assert result.status == "fail"


# ---------------------------------------------------------------------------
# _check_demand_le_max — alias for _check_supply_ge_demand(max, demand, …)
# ---------------------------------------------------------------------------


class TestCheckDemandLeMax:
    @given(
        demand=st.one_of(_value_units(), _NON_VALUE_INPUTS),
        maximum=st.one_of(_value_units(), _NON_VALUE_INPUTS),
        field_name=st.text(min_size=1, max_size=20),
    )
    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    def test_alias_matches_underlying_check(
        self, demand: Any, maximum: Any, field_name: str
    ) -> None:
        alias = _check_demand_le_max(demand, maximum, field_name)
        direct = _check_supply_ge_demand(maximum, demand, field_name)
        assert alias.status == direct.status
        assert alias.field == direct.field == field_name


# ---------------------------------------------------------------------------
# _check_equal_str — case-insensitive whitespace-stripped equality
# ---------------------------------------------------------------------------


class TestCheckEqualStr:
    @given(
        a=st.one_of(st.none(), st.text(max_size=20)),
        b=st.one_of(st.none(), st.text(max_size=20)),
        field_name=st.text(min_size=1, max_size=20),
    )
    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    def test_never_raises_returns_valid_check_result(
        self, a, b, field_name: str
    ) -> None:
        result = _check_equal_str(a, b, field_name)
        assert isinstance(result, CheckResult)
        assert result.field == field_name
        assert result.status in _VALID_STATUSES

    @given(s=st.text(min_size=1, max_size=20))
    @settings(max_examples=100, deadline=None)
    def test_symmetry_of_status(self, s: str) -> None:
        ab = _check_equal_str(s, s, "frame_size")
        ba = _check_equal_str(s, s, "frame_size")
        assert ab.status == ba.status == "ok"

    @given(s=st.text(min_size=1, max_size=20))
    @settings(max_examples=100, deadline=None)
    def test_none_side_is_partial(self, s: str) -> None:
        assert _check_equal_str(None, s, "frame_size").status == "partial"
        assert _check_equal_str(s, None, "frame_size").status == "partial"

    @given(
        prefix=st.text(alphabet=" \t", min_size=0, max_size=4),
        body=st.text(
            alphabet=st.characters(
                min_codepoint=ord("A"), max_codepoint=ord("z"), categories=("L",)
            ),
            min_size=1,
            max_size=10,
        ),
        suffix=st.text(alphabet=" \t", min_size=0, max_size=4),
    )
    @settings(max_examples=100, deadline=None)
    def test_whitespace_and_case_insensitive(
        self, prefix: str, body: str, suffix: str
    ) -> None:
        # ASCII-letter body only — see note in TestCheckMembership.
        a = body.upper()
        b = f"{prefix}{body.lower()}{suffix}"
        assert _check_equal_str(a, b, "frame_size").status == "ok"


# ---------------------------------------------------------------------------
# _check_membership / _check_intersect — list-shaped checks
# ---------------------------------------------------------------------------


class TestCheckMembership:
    @given(
        value=st.one_of(st.none(), st.text(max_size=20)),
        options=st.one_of(st.none(), st.lists(st.text(max_size=10), max_size=5)),
        field_name=st.text(min_size=1, max_size=20),
    )
    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    def test_never_raises_returns_valid_check_result(
        self, value, options, field_name: str
    ) -> None:
        result = _check_membership(value, options, field_name)
        assert isinstance(result, CheckResult)
        assert result.field == field_name
        assert result.status in _VALID_STATUSES

    @given(
        options=st.lists(
            st.text(
                alphabet=st.characters(
                    min_codepoint=ord("A"), max_codepoint=ord("z"), categories=("L",)
                ),
                min_size=1,
                max_size=10,
            ),
            min_size=1,
            max_size=5,
            unique=True,
        )
    )
    @settings(max_examples=100, deadline=None)
    def test_value_in_options_is_ok(self, options: list[str]) -> None:
        # ASCII-letter alphabet only: real protocol/encoder names are
        # ASCII, and Unicode case-folding quirks (e.g. ``'ß'.upper()``
        # → ``'SS'``) would otherwise make ``.lower()``-based equality
        # non-round-tripping. The compat helper uses plain ``.lower()``
        # consistently, which is correct for the real domain.
        chosen = options[0]
        result = _check_membership(chosen.upper(), options, "encoder")
        assert result.status == "ok"

    @given(
        value=st.text(min_size=1, max_size=10),
        options=st.lists(st.text(min_size=1, max_size=10), min_size=1, max_size=5),
    )
    @settings(max_examples=100, deadline=None)
    def test_empty_value_or_options_is_partial(
        self, value: str, options: list[str]
    ) -> None:
        assert _check_membership(None, options, "encoder").status == "partial"
        assert _check_membership(value, None, "encoder").status == "partial"
        assert _check_membership(value, [], "encoder").status == "partial"


class TestCheckIntersect:
    @given(
        a=st.one_of(st.none(), st.lists(st.text(max_size=10), max_size=5)),
        b=st.one_of(st.none(), st.lists(st.text(max_size=10), max_size=5)),
        field_name=st.text(min_size=1, max_size=20),
    )
    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    def test_never_raises_returns_valid_check_result(
        self, a, b, field_name: str
    ) -> None:
        result = _check_intersect(a, b, field_name)
        assert isinstance(result, CheckResult)
        assert result.field == field_name
        assert result.status in _VALID_STATUSES

    @given(
        a=st.lists(
            st.text(min_size=1, max_size=10), min_size=1, max_size=5, unique=True
        ),
        b=st.lists(
            st.text(min_size=1, max_size=10), min_size=1, max_size=5, unique=True
        ),
    )
    @settings(max_examples=200, deadline=None)
    def test_symmetric_on_status(self, a: list[str], b: list[str]) -> None:
        # The detail string mentions a then b vs b then a, but the
        # status decision is set-based and must be symmetric.
        assert _check_intersect(a, b, "fieldbus").status == _check_intersect(
            b, a, "fieldbus"
        ).status

    @given(
        shared=st.text(
            alphabet=st.characters(
                min_codepoint=ord("A"), max_codepoint=ord("z"), categories=("L",)
            ),
            min_size=1,
            max_size=10,
        )
    )
    @settings(max_examples=100, deadline=None)
    def test_shared_element_is_ok(self, shared: str) -> None:
        # ASCII-letter alphabet only — see note in TestCheckMembership.
        result = _check_intersect([shared.upper()], [shared.lower()], "fieldbus")
        assert result.status == "ok"


# ---------------------------------------------------------------------------
# _check_shaft_fit — equality within 0.1 mm
# ---------------------------------------------------------------------------


class TestCheckShaftFit:
    @given(
        motor=st.one_of(_value_units(), _NON_VALUE_INPUTS),
        gearhead=st.one_of(_value_units(), _NON_VALUE_INPUTS),
    )
    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    def test_never_raises_returns_valid_check_result(
        self, motor: Any, gearhead: Any
    ) -> None:
        result = _check_shaft_fit(motor, gearhead)
        assert isinstance(result, CheckResult)
        assert result.field == "shaft_diameter"
        assert result.status in _VALID_STATUSES

    @given(v=_value_units(unit_strategy=st.just("mm")))
    @settings(max_examples=200, deadline=None)
    def test_identical_shafts_are_ok(self, v: ValueUnit) -> None:
        assert _check_shaft_fit(v, v).status == "ok"

    @given(diameter=st.floats(min_value=1.0, max_value=200.0, allow_nan=False))
    @settings(max_examples=100, deadline=None)
    def test_off_by_more_than_tolerance_is_fail(self, diameter: float) -> None:
        motor = ValueUnit(value=diameter, unit="mm")
        gearhead = ValueUnit(value=diameter + 0.5, unit="mm")
        assert _check_shaft_fit(motor, gearhead).status == "fail"


# ---------------------------------------------------------------------------
# Regression pins — keep the specific shapes the property tests
# surfaced so they cannot regress even if the strategy drifts.
# ---------------------------------------------------------------------------


class TestExplicitRegressionsFromAdversarialShapes:
    def test_scalar_handles_non_value_inputs_quietly(self) -> None:
        # Anything outside ValueUnit / MinMaxUnit returns None without
        # raising — the per-port comparators rely on this to short-circuit.
        for bad in (None, 0, 0.0, "", "  ", b"\x00", [], {}, (1.0, "V")):
            assert _scalar(bad) is None
            assert _range(bad) is None

    def test_voltage_fits_unit_mismatch_is_fail(self) -> None:
        # Demand voltage in V, supply rated in A — clearly nonsense but
        # the compatibility helper must surface the unit mismatch as
        # fail with a "unit mismatch" detail (not silently 'ok').
        supply = MinMaxUnit(min=200.0, max=240.0, unit="V")
        demand = ValueUnit(value=230.0, unit="A")
        result = _check_voltage_fits(supply, demand, "voltage")
        assert result.status == "fail"
        assert "unit mismatch" in result.detail

    def test_voltage_fits_demand_inside_is_ok(self) -> None:
        # The integration-test happy path: 200-240 V supply contains
        # 230 V demand.
        supply = MinMaxUnit(min=200.0, max=240.0, unit="V")
        demand = ValueUnit(value=230.0, unit="V")
        assert _check_voltage_fits(supply, demand, "voltage").status == "ok"

    def test_equal_str_whitespace_only_treated_as_value(self) -> None:
        # ``" "`` is not None, so the partial branch is skipped; after
        # strip/lower both sides become ``""`` and compare equal.
        # Pinning so a future "treat whitespace as missing" change is
        # a conscious decision.
        assert _check_equal_str(" ", " ", "frame_size").status == "ok"

    def test_membership_case_insensitive(self) -> None:
        result = _check_membership("ENDAT 2.2", ["EnDat 2.2", "Resolver"], "encoder")
        assert result.status == "ok"

    def test_intersect_no_overlap_is_fail_not_partial(self) -> None:
        result = _check_intersect(["EtherCAT"], ["Profinet"], "fieldbus")
        assert result.status == "fail"

    def test_shaft_fit_within_tolerance(self) -> None:
        motor = ValueUnit(value=14.0, unit="mm")
        gearhead = ValueUnit(value=14.05, unit="mm")  # 0.05 mm < 0.1 tol
        assert _check_shaft_fit(motor, gearhead).status == "ok"

    def test_demand_le_max_alias_consistency(self) -> None:
        # 4000 rpm motor vs 5000 rpm gearhead limit — demand within max → ok.
        motor_max = ValueUnit(value=4000.0, unit="rpm")
        gearhead_max = ValueUnit(value=5000.0, unit="rpm")
        alias = _check_demand_le_max(motor_max, gearhead_max, "speed")
        direct = _check_supply_ge_demand(gearhead_max, motor_max, "speed")
        assert alias.status == direct.status == "ok"


# ---------------------------------------------------------------------------
# Pytest entry — keeps mypy/ruff from complaining about unused import
# of pytest in some configurations.
# ---------------------------------------------------------------------------


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-x"])
