"""Property tests for ``specodex.integration.compat`` helpers.

The example-based companion (``test_integration.py``) pins specific
happy-path and missing-field shapes. This file generates *adversarial*
inputs — arbitrary objects fed to ``_scalar`` / ``_range``, random
ValueUnit/MinMaxUnit pairs fed to the per-field comparators, and
random ``CheckResult`` mixes fed to ``_roll_up`` — and asserts the
documented contract holds for every case the strategy can produce.

**Contracts under test:**

1. ``_scalar`` and ``_range`` never raise on any input. They return
   ``None`` for anything that isn't a ``ValueUnit`` / ``MinMaxUnit``,
   the documented tuple shape otherwise.
2. Every ``_check_*`` helper returns a ``CheckResult`` whose status
   is one of ``{"ok", "partial", "fail"}`` and never raises — these
   are called from ``check()`` with no surrounding ``try`` block,
   so any leak crashes the compat report.
3. ``_check_supply_ge_demand`` is monotonic in its scalar comparison:
   when both sides agree on unit and supply >= demand, it returns
   ``ok``; when supply < demand, it returns ``fail``. (Pins the
   contract called out in the docstring; a sign flip is exactly
   the kind of regression an example test wouldn't catch.)
4. ``_check_voltage_fits`` is reflexive: a port compared against
   itself never returns ``fail``.
5. ``_roll_up`` follows the documented worst-status precedence:
   any ``fail`` ⇒ ``fail``; otherwise any ``partial`` ⇒ ``partial``;
   otherwise ``ok``. Empty input rolls up to ``ok`` (vacuous truth).
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.integration.compat import (
    CheckResult,
    _check_equal_str,
    _check_intersect,
    _check_membership,
    _check_shaft_fit,
    _check_supply_ge_demand,
    _check_voltage_fits,
    _range,
    _roll_up,
    _scalar,
)
from specodex.models.common import MinMaxUnit, ValueUnit


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


# Stick to canonical units so ValueUnit/MinMaxUnit's after-validator
# normalisation doesn't reshape inputs underneath us. The properties
# pin _scalar/_range behaviour, not the unit-normaliser's.
_CANONICAL_UNITS = st.sampled_from(["V", "A", "W", "mm", "Nm", "rpm"])


# Reasonable scalar magnitudes — keep clear of NaN/inf so the
# range-comparison properties hold (NaN < x is always False, which
# would muddle the supply >= demand pin). Hypothesis surfaces NaN
# adversarially via the "arbitrary other input" strategy instead.
_SCALARS = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)


@st.composite
def _value_unit(draw: st.DrawFn) -> ValueUnit:
    return ValueUnit(value=draw(_SCALARS), unit=draw(_CANONICAL_UNITS))


@st.composite
def _min_max_unit(draw: st.DrawFn) -> MinMaxUnit:
    """A MinMaxUnit with at least one bound populated.

    Three shapes are emitted with equal weight: full range (both
    bounds), min-only (max is None), max-only (min is None). When
    both are present, ``min <= max`` is preserved so the range stays
    well-formed.
    """
    shape = draw(st.sampled_from(["both", "min_only", "max_only"]))
    unit = draw(_CANONICAL_UNITS)
    if shape == "both":
        lo = draw(_SCALARS)
        hi = draw(_SCALARS.filter(lambda x: x >= lo))
        return MinMaxUnit(min=lo, max=hi, unit=unit)
    if shape == "min_only":
        return MinMaxUnit(min=draw(_SCALARS), max=None, unit=unit)
    return MinMaxUnit(min=None, max=draw(_SCALARS), unit=unit)


# Adversarial "anything else" — what the dispatcher might hand to
# _scalar/_range if a port adapter regresses (None, primitives, lists,
# dicts, NaN/inf floats, tuples, raw strings).
_ARBITRARY_OTHER = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=20),
    st.binary(max_size=20),
    st.lists(st.integers(), max_size=5),
    st.dictionaries(st.text(max_size=5), st.integers(), max_size=3),
    st.tuples(st.floats(allow_nan=True), st.text(max_size=5)),
)


_ANY_SCALAR_INPUT = st.one_of(_value_unit(), _min_max_unit(), _ARBITRARY_OTHER)


_STATUSES = st.sampled_from(["ok", "partial", "fail"])


@st.composite
def _check_result(draw: st.DrawFn) -> CheckResult:
    return CheckResult(
        field=draw(st.text(min_size=0, max_size=10)),
        status=draw(_STATUSES),
        detail=draw(st.text(min_size=0, max_size=20)),
    )


# ---------------------------------------------------------------------------
# _scalar / _range contracts
# ---------------------------------------------------------------------------


class TestScalarExtractorContract:
    """``_scalar`` is the boundary between raw port values and the
    field-check pipeline. A leak (exception, wrong shape) crashes the
    compat report; an unwarranted ``None`` silently masks mismatches.
    """

    @given(v=_ANY_SCALAR_INPUT)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises_and_returns_documented_shape(self, v: Any) -> None:
        try:
            result = _scalar(v)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(f"_scalar raised {type(exc).__name__}: {exc!r}\ninput: {v!r}")
        assert result is None or (
            isinstance(result, tuple)
            and len(result) == 2
            and isinstance(result[0], (int, float))
            and isinstance(result[1], str)
        ), f"_scalar returned {result!r} of unexpected shape for {v!r}"

    @given(v=_value_unit())
    @settings(max_examples=100, deadline=None)
    def test_value_unit_yields_value_unit_tuple(self, v: ValueUnit) -> None:
        result = _scalar(v)
        assert result == (v.value, v.unit), (
            f"_scalar(ValueUnit) lost data: got {result!r}, expected "
            f"{(v.value, v.unit)!r}"
        )

    @given(v=_min_max_unit())
    @settings(max_examples=100, deadline=None)
    def test_min_max_unit_collapses_to_present_bound(self, v: MinMaxUnit) -> None:
        result = _scalar(v)
        expected_scalar = v.min if v.min is not None else v.max
        # MinMaxUnit guarantees at least one bound is set, so the
        # collapse never produces None here.
        assert result == (expected_scalar, v.unit), (
            f"_scalar(MinMaxUnit) collapse wrong: got {result!r}, expected "
            f"{(expected_scalar, v.unit)!r}"
        )

    @given(v=_ARBITRARY_OTHER)
    @settings(max_examples=200, deadline=None)
    def test_non_port_value_returns_none(self, v: Any) -> None:
        assert _scalar(v) is None, (
            f"_scalar accepted non-port value {v!r} and returned a tuple — "
            "the contract is None for anything that isn't ValueUnit / MinMaxUnit"
        )


class TestRangeExtractorContract:
    @given(v=_ANY_SCALAR_INPUT)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises_and_returns_documented_shape(self, v: Any) -> None:
        try:
            result = _range(v)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"_range raised {type(exc).__name__}: {exc!r}\ninput: {v!r}")
        assert result is None or (
            isinstance(result, tuple)
            and len(result) == 3
            and isinstance(result[0], (int, float))
            and isinstance(result[1], (int, float))
            and isinstance(result[2], str)
        ), f"_range returned {result!r} of unexpected shape for {v!r}"

    @given(v=_value_unit())
    @settings(max_examples=100, deadline=None)
    def test_value_unit_yields_point_range(self, v: ValueUnit) -> None:
        result = _range(v)
        assert result == (v.value, v.value, v.unit), (
            f"_range(ValueUnit) should widen to a point range, got {result!r}"
        )

    @given(v=_min_max_unit())
    @settings(max_examples=100, deadline=None)
    def test_min_max_unit_uses_other_bound_as_fallback(self, v: MinMaxUnit) -> None:
        result = _range(v)
        lo = v.min if v.min is not None else v.max
        hi = v.max if v.max is not None else v.min
        assert result == (lo, hi, v.unit), (
            f"_range(MinMaxUnit) fallback wrong: got {result!r}, expected "
            f"{(lo, hi, v.unit)!r}"
        )
        # The lo/hi ordering should never be inverted on the output.
        assert result[0] <= result[1], (
            f"_range produced inverted bounds {result!r} for {v!r}"
        )

    @given(v=_ARBITRARY_OTHER)
    @settings(max_examples=200, deadline=None)
    def test_non_port_value_returns_none(self, v: Any) -> None:
        assert _range(v) is None, (
            f"_range accepted non-port value {v!r} and returned a tuple"
        )


# ---------------------------------------------------------------------------
# Per-field check contracts
# ---------------------------------------------------------------------------


_CHECK_ARG = st.one_of(_value_unit(), _min_max_unit(), st.none(), _ARBITRARY_OTHER)


class TestCheckHelpersNeverRaise:
    """Each ``_check_*`` helper is called from a comparator with no
    surrounding try/except. The contract: always return a CheckResult
    with a documented status, never raise.
    """

    @given(supply=_CHECK_ARG, demand=_CHECK_ARG)
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_voltage_fits(self, supply: Any, demand: Any) -> None:
        try:
            r = _check_voltage_fits(supply, demand, "voltage")
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_check_voltage_fits raised {type(exc).__name__}: {exc!r}\n"
                f"supply={supply!r}, demand={demand!r}"
            )
        assert isinstance(r, CheckResult)
        assert r.status in {"ok", "partial", "fail"}
        assert r.field == "voltage"

    @given(supply=_CHECK_ARG, demand=_CHECK_ARG)
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_supply_ge_demand(self, supply: Any, demand: Any) -> None:
        try:
            r = _check_supply_ge_demand(supply, demand, "current")
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_check_supply_ge_demand raised {type(exc).__name__}: "
                f"{exc!r}\nsupply={supply!r}, demand={demand!r}"
            )
        assert isinstance(r, CheckResult)
        assert r.status in {"ok", "partial", "fail"}
        assert r.field == "current"

    @given(motor_shaft=_CHECK_ARG, gearhead_bore=_CHECK_ARG)
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_shaft_fit(self, motor_shaft: Any, gearhead_bore: Any) -> None:
        try:
            r = _check_shaft_fit(motor_shaft, gearhead_bore)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_check_shaft_fit raised {type(exc).__name__}: {exc!r}\n"
                f"motor={motor_shaft!r}, bore={gearhead_bore!r}"
            )
        assert isinstance(r, CheckResult)
        assert r.status in {"ok", "partial", "fail"}
        assert r.field == "shaft_diameter"

    @given(
        a=st.one_of(st.none(), st.text(max_size=30), _ARBITRARY_OTHER),
        b=st.one_of(st.none(), st.text(max_size=30), _ARBITRARY_OTHER),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_equal_str(self, a: Any, b: Any) -> None:
        # The helper is typed Optional[str], so non-string inputs are
        # a type violation the caller is responsible for filtering.
        # We restrict to the documented inputs (None | str) here.
        a_typed = a if a is None or isinstance(a, str) else None
        b_typed = b if b is None or isinstance(b, str) else None
        try:
            r = _check_equal_str(a_typed, b_typed, "frame_size")
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_check_equal_str raised {type(exc).__name__}: {exc!r}\n"
                f"a={a_typed!r}, b={b_typed!r}"
            )
        assert isinstance(r, CheckResult)
        assert r.status in {"ok", "partial", "fail"}

    @given(
        value=st.one_of(st.none(), st.text(max_size=20)),
        options=st.one_of(
            st.none(),
            st.lists(st.text(max_size=15), max_size=5),
        ),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_membership(
        self, value: Optional[str], options: Optional[List[str]]
    ) -> None:
        try:
            r = _check_membership(value, options, "ac_dc")
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_check_membership raised {type(exc).__name__}: {exc!r}\n"
                f"value={value!r}, options={options!r}"
            )
        assert isinstance(r, CheckResult)
        assert r.status in {"ok", "partial", "fail"}

    @given(
        a=st.one_of(st.none(), st.lists(st.text(max_size=12), max_size=5)),
        b=st.one_of(st.none(), st.lists(st.text(max_size=12), max_size=5)),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_intersect(self, a: Optional[List[str]], b: Optional[List[str]]) -> None:
        try:
            r = _check_intersect(a, b, "fieldbus")
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_check_intersect raised {type(exc).__name__}: {exc!r}\n"
                f"a={a!r}, b={b!r}"
            )
        assert isinstance(r, CheckResult)
        assert r.status in {"ok", "partial", "fail"}


# ---------------------------------------------------------------------------
# Field-check semantic properties (not just "doesn't crash")
# ---------------------------------------------------------------------------


class TestSupplyGeDemandSemantics:
    """``_check_supply_ge_demand`` is the workhorse of every electrical
    compat check (current, power, torque-as-current proxy). The sign
    of the inequality has to be right or every drive looks compatible
    with every motor (or none of them does).
    """

    @given(
        value=_SCALARS,
        unit=_CANONICAL_UNITS,
        delta=st.floats(min_value=0.001, max_value=1e5),
    )
    @settings(max_examples=200, deadline=None)
    def test_supply_strictly_greater_is_ok(
        self, value: float, unit: str, delta: float
    ) -> None:
        supply = ValueUnit(value=value + delta, unit=unit)
        demand = ValueUnit(value=value, unit=unit)
        r = _check_supply_ge_demand(supply, demand, "current")
        assert r.status == "ok", (
            f"supply={value + delta} ≥ demand={value} {unit} should be ok, "
            f"got {r.status} ({r.detail!r})"
        )

    @given(
        value=_SCALARS,
        unit=_CANONICAL_UNITS,
        delta=st.floats(min_value=0.001, max_value=1e5),
    )
    @settings(max_examples=200, deadline=None)
    def test_supply_strictly_less_is_fail(
        self, value: float, unit: str, delta: float
    ) -> None:
        supply = ValueUnit(value=value, unit=unit)
        demand = ValueUnit(value=value + delta, unit=unit)
        r = _check_supply_ge_demand(supply, demand, "current")
        assert r.status == "fail", (
            f"supply={value} < demand={value + delta} {unit} should be fail, "
            f"got {r.status} ({r.detail!r})"
        )

    @given(value=_SCALARS, unit=_CANONICAL_UNITS)
    @settings(max_examples=100, deadline=None)
    def test_supply_equals_demand_is_ok(self, value: float, unit: str) -> None:
        """``>=`` is inclusive — equal supply and demand must succeed."""
        supply = ValueUnit(value=value, unit=unit)
        demand = ValueUnit(value=value, unit=unit)
        r = _check_supply_ge_demand(supply, demand, "current")
        assert r.status == "ok", (
            f"supply == demand should be ok (inclusive >=), got {r.status}"
        )

    @given(
        s_val=_SCALARS,
        s_unit=_CANONICAL_UNITS,
        d_val=_SCALARS,
        d_unit=_CANONICAL_UNITS,
    )
    @settings(max_examples=200, deadline=None)
    def test_unit_mismatch_is_fail(
        self, s_val: float, s_unit: str, d_val: float, d_unit: str
    ) -> None:
        """Different units must never silently return ok — comparing
        kW to A is a category error, not a numeric one."""
        if s_unit == d_unit:
            return  # skip; covered by the comparison-direction tests
        supply = ValueUnit(value=s_val, unit=s_unit)
        demand = ValueUnit(value=d_val, unit=d_unit)
        r = _check_supply_ge_demand(supply, demand, "current")
        assert r.status == "fail", (
            f"unit mismatch ({s_unit} vs {d_unit}) should fail, got {r.status}"
        )


class TestVoltageFitsReflexive:
    """A port compared against itself must never be flagged ``fail`` —
    "the same drive is compatible with itself" is the floor the
    comparator can't fall through.
    """

    @given(v=_min_max_unit())
    @settings(max_examples=100, deadline=None)
    def test_port_against_self_is_not_fail(self, v: MinMaxUnit) -> None:
        r = _check_voltage_fits(v, v, "voltage")
        assert r.status != "fail", (
            f"voltage range vs itself returned fail: {r.detail!r}"
        )

    @given(v=_value_unit())
    @settings(max_examples=100, deadline=None)
    def test_value_unit_against_self_is_ok(self, v: ValueUnit) -> None:
        # ValueUnit widens to a point range — a point fits within itself.
        r = _check_voltage_fits(v, v, "voltage")
        assert r.status == "ok", (
            f"voltage point vs itself should be ok, got {r.status} ({r.detail!r})"
        )


# ---------------------------------------------------------------------------
# _roll_up precedence
# ---------------------------------------------------------------------------


class TestRollUpPrecedence:
    """``_roll_up`` is the entry point for both per-pair and overall
    report status. Its precedence has to match the docstring: any fail
    poisons the roll-up, any partial degrades it, otherwise ok.
    """

    @given(checks=st.lists(_check_result(), min_size=0, max_size=10))
    @settings(max_examples=300, deadline=None)
    def test_status_is_documented_value(self, checks: List[CheckResult]) -> None:
        assert _roll_up(checks) in {"ok", "partial", "fail"}

    @given(checks=st.lists(_check_result(), min_size=1, max_size=10))
    @settings(max_examples=300, deadline=None)
    def test_worst_status_wins(self, checks: List[CheckResult]) -> None:
        statuses = {c.status for c in checks}
        rolled = _roll_up(checks)
        if "fail" in statuses:
            assert rolled == "fail"
        elif "partial" in statuses:
            assert rolled == "partial"
        else:
            assert rolled == "ok"

    def test_empty_input_is_ok(self) -> None:
        """Vacuous truth — no checks means nothing failed."""
        assert _roll_up([]) == "ok"

    @given(n=st.integers(min_value=1, max_value=10))
    @settings(max_examples=50, deadline=None)
    def test_all_ok_rolls_up_to_ok(self, n: int) -> None:
        checks = [CheckResult(f"f{i}", "ok", "") for i in range(n)]
        assert _roll_up(checks) == "ok"

    @given(n=st.integers(min_value=1, max_value=10), bad_idx=st.integers(min_value=0))
    @settings(max_examples=100, deadline=None)
    def test_one_fail_anywhere_poisons_the_roll_up(self, n: int, bad_idx: int) -> None:
        idx = bad_idx % n
        checks = [CheckResult(f"f{i}", "ok", "") for i in range(n)]
        checks[idx] = CheckResult("bad", "fail", "")
        assert _roll_up(checks) == "fail"
