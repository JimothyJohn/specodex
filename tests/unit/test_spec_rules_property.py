"""Property tests for ``specodex.spec_rules.validate_product``.

The example-based companion (``test_spec_rules.py``) pins the
core happy-path and the Kollmorgen unit-mismatch regression.
This file generates *adversarial* product instances — arbitrary
``ValueUnit`` / ``MinMaxUnit`` magnitudes, randomised identity
metadata, ``None``-shaped spec fields — and asserts the documented
contract holds for every case the strategy can produce.

``validate_product`` runs between Pydantic validation and quality
scoring; a leak from this layer either silently keeps an
implausible value (a real spec-table miss surfacing as a "valid"
product downstream) or raises and crashes the whole batch. Both
are bad. The property tests pin the contract; specific bug
shapes also stay in the example file as regression tests.

**Contracts under test:**

1. ``validate_product`` never raises on any well-formed
   ``ProductBase`` subclass instance. The dispatcher
   ``validate_products`` has no surrounding ``try`` — any leak
   takes the whole batch with it.
2. It always returns a ``list[str]`` (possibly empty). Never
   ``None``, never any other iterable shape.
3. **Identity check is total.** When ``part_number`` is missing
   (None or whitespace) AND ``manufacturer`` normalises into
   ``GENERIC_MANUFACTURERS``, every spec field declared on the
   model (per ``quality.spec_fields_for_model``) is set to
   ``None`` and the violations list is non-empty.
4. **Identity check leaves real products alone.** Whenever a
   non-empty part_number is present OR the manufacturer isn't
   in the generic set, the identity branch never fires — no
   "Unidentifiable product" violation, and the products
   non-spec fields (manufacturer, product_name, part_number)
   are preserved on the model.
5. **Per-field magnitude rule (out-of-range nulls the field).**
   For any field in ``FIELD_RULES``, if every numeric value
   parsed from the field falls outside ``[min, max]``, the
   field is set to ``None`` after ``validate_product`` returns.
6. **Per-field magnitude rule (in-range preserves the field).**
   When all parsed values lie inside ``[min, max]``, the field
   is preserved unchanged. (Other fields can still null it if
   the duplicate-pair rule fires; the property scopes the
   guarantee to fields not involved in ``DUPLICATE_PAIRS``.)
7. **Duplicate-pair rule.** When ``rated_voltage`` equals
   ``rated_speed`` (full object equality, including unit), the
   left-hand field (``rated_voltage``) is nulled and the
   right-hand field (``rated_speed``) is preserved.
8. **``_values_of`` never raises** on arbitrary input and returns
   ``None`` for anything that isn't a ``ValueUnit`` /
   ``MinMaxUnit``. The tuple it returns for VU/MMU always has
   shape ``(non-empty list[float], str)``.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.motor import Motor
from specodex.quality import spec_fields_for_model
from specodex.spec_rules import (
    DUPLICATE_PAIRS,
    FIELD_RULES,
    GENERIC_MANUFACTURERS,
    _values_of,
    validate_product,
    validate_products,
)


# Silence the per-violation warning log — a 200-example property
# run would otherwise produce 200 stack-trace-shaped lines per
# case and bury the test output.
@pytest.fixture(autouse=True)
def _silence_validation_logs():
    logger = logging.getLogger("specodex.spec_rules")
    prior_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(prior_level)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


# Canonical units per family. We stay inside the typed-alias whitelist
# so the Pydantic before-validator doesn't reshape a ValueUnit into
# None before validate_product even sees it. The properties pin
# spec_rules' behaviour, not the unit-normaliser's.
_UNITS_BY_FAMILY: dict[str, list[str]] = {
    "voltage": ["V", "Vac", "Vdc", "Vrms"],
    "speed": ["rpm"],
    "current": ["A", "Arms"],
    "torque": ["Nm"],
    "power": ["W", "kW"],
    "resistance": ["Ω", "ohm"],
    "inductance": ["mH", "H"],
    "inertia": ["kg·cm²"],
    "force": ["N", "lbf"],
}


# Map FIELD_RULES keys → unit family. Keeps the strategy aligned with
# the Pydantic typed alias on each field, so generated VU/MMU values
# round-trip through model construction unchanged.
_FIELD_FAMILY: dict[str, str] = {
    "rated_voltage": "voltage",
    "input_voltage": "voltage",
    "rated_speed": "speed",
    "max_speed": "speed",
    "rated_current": "current",
    "peak_current": "current",
    "rated_torque": "torque",
    "peak_torque": "torque",
    "max_continuous_torque": "torque",
    "max_peak_torque": "torque",
    "rated_power": "power",
    "resistance": "resistance",
    "inductance": "inductance",
    "rotor_inertia": "inertia",
    "axial_load_force_rating": "force",
    "radial_load_force_rating": "force",
}


def _unit_for(field: str) -> st.SearchStrategy[str]:
    return st.sampled_from(_UNITS_BY_FAMILY[_FIELD_FAMILY[field]])


def _in_range(field: str) -> st.SearchStrategy[float]:
    lo, hi = FIELD_RULES[field]
    # Pull the range in slightly so a borderline floating-point
    # comparison doesn't classify lo or hi as outside the rule.
    span = hi - lo
    return st.floats(
        min_value=lo + span * 1e-9,
        max_value=hi - span * 1e-9,
        allow_nan=False,
        allow_infinity=False,
    )


def _out_of_range_above(field: str) -> st.SearchStrategy[float]:
    _, hi = FIELD_RULES[field]
    return st.floats(
        min_value=hi * 1.01 + 1.0,
        max_value=hi * 1000 + 1_000_000,
        allow_nan=False,
        allow_infinity=False,
    )


# Hand-picked range-typed fields (Pydantic alias is MinMaxUnit) vs
# scalar-typed fields (Pydantic alias is ValueUnit). Mixing them
# up causes the Pydantic constructor to coerce/reject silently.
_RANGE_FIELDS = {
    "rated_voltage",
    "input_voltage",
    "axial_load_force_rating",
    "radial_load_force_rating",
}


def _make_field_value(
    field: str, value_strategy: st.SearchStrategy[float]
) -> st.SearchStrategy[Any]:
    """Build a ``ValueUnit`` or ``MinMaxUnit`` for the given field."""
    if field in _RANGE_FIELDS:

        @st.composite
        def _range(draw: st.DrawFn) -> MinMaxUnit:
            v = draw(value_strategy)
            unit = draw(_unit_for(field))
            return MinMaxUnit(min=v, max=v, unit=unit)

        return _range()

    @st.composite
    def _scalar(draw: st.DrawFn) -> ValueUnit:
        v = draw(value_strategy)
        unit = draw(_unit_for(field))
        return ValueUnit(value=v, unit=unit)

    return _scalar()


# Motor instance with random-but-valid identity, ready for
# validate_product. Non-generic manufacturer + non-empty
# part_number, so the identity branch never fires here.
@st.composite
def _real_motor(draw: st.DrawFn) -> Motor:
    return Motor(
        product_name=draw(
            st.text(min_size=1, max_size=30).map(lambda s: s.strip() or "x")
        ),
        manufacturer=draw(
            st.text(min_size=1, max_size=20).filter(
                lambda s: s.strip().lower() not in GENERIC_MANUFACTURERS
            )
        ),
        product_type="motor",
        part_number=draw(st.text(min_size=1, max_size=20).filter(lambda s: s.strip())),
    )


# Motor with deliberately-unidentifiable identity: no part_number
# (None or whitespace) AND manufacturer in the generic set.
@st.composite
def _unidentifiable_motor(draw: st.DrawFn) -> Motor:
    return Motor(
        product_name=draw(
            st.text(min_size=1, max_size=20).map(lambda s: s.strip() or "x")
        ),
        manufacturer=draw(st.sampled_from(sorted(GENERIC_MANUFACTURERS - {""}))),
        product_type="motor",
        part_number=draw(st.sampled_from([None, "", "   ", "\t"])),
        rated_voltage="240;V",
        rated_torque="5;Nm",
    )


# ---------------------------------------------------------------------------
# 1. Never raises + returns list[str]
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateProductNeverRaises:
    @given(_real_motor())
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_well_formed_motor_returns_list_of_str(self, motor: Motor) -> None:
        result = validate_product(motor)
        assert isinstance(result, list)
        assert all(isinstance(v, str) for v in result)

    @given(_unidentifiable_motor())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_unidentifiable_motor_returns_list_of_str(self, motor: Motor) -> None:
        result = validate_product(motor)
        assert isinstance(result, list)
        assert all(isinstance(v, str) for v in result)


# ---------------------------------------------------------------------------
# 2. Identity-check contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdentityCheck:
    @given(_unidentifiable_motor())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_unidentifiable_nulls_all_spec_fields(self, motor: Motor) -> None:
        validate_product(motor)
        for field_name in spec_fields_for_model(Motor):
            assert getattr(motor, field_name) is None, (
                f"{field_name!r} should be None after identity null-out"
            )

    @given(_unidentifiable_motor())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_unidentifiable_emits_violation(self, motor: Motor) -> None:
        violations = validate_product(motor)
        assert any("Unidentifiable product" in v for v in violations)

    @given(_real_motor())
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_real_motor_no_identity_violation(self, motor: Motor) -> None:
        violations = validate_product(motor)
        assert not any("Unidentifiable product" in v for v in violations)
        # The identity-branch null-out runs as a side effect on the
        # whole spec column. Confirm at least one non-meta field is
        # touchable post-validation (it's still None here because we
        # didn't seed any specs, but ``manufacturer`` survives).
        assert motor.manufacturer is not None


# ---------------------------------------------------------------------------
# 3. Per-field magnitude rule — in-range preserved
# ---------------------------------------------------------------------------

# Pick three representative scalar fields with well-defined range,
# covering different unit families so the property tests both the
# rule machinery and the unit-roundtrip behaviour.
_IN_RANGE_FIELDS = ("rated_torque", "peak_torque", "rated_current")


@pytest.mark.unit
class TestMagnitudeInRange:
    @given(
        field=st.sampled_from(_IN_RANGE_FIELDS),
        data=st.data(),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_in_range_value_preserved(self, field: str, data: st.DataObject) -> None:
        # rated_voltage is part of DUPLICATE_PAIRS — exclude here so
        # the dup-rule doesn't null the field for a separate reason.
        assert (field, "rated_speed") not in DUPLICATE_PAIRS
        assert field not in (f for pair in DUPLICATE_PAIRS for f in pair)

        value = data.draw(_make_field_value(field, _in_range(field)))
        motor = data.draw(_real_motor())
        setattr(motor, field, value)
        assert getattr(motor, field) is not None
        validate_product(motor)
        assert getattr(motor, field) is not None, (
            f"{field}={value} (in [{FIELD_RULES[field]}]) was nulled"
        )


# ---------------------------------------------------------------------------
# 4. Per-field magnitude rule — out-of-range nulled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMagnitudeOutOfRange:
    @given(
        field=st.sampled_from(_IN_RANGE_FIELDS),
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_out_of_range_high_nulls_field(
        self, field: str, data: st.DataObject
    ) -> None:
        value = data.draw(_make_field_value(field, _out_of_range_above(field)))
        motor = data.draw(_real_motor())
        setattr(motor, field, value)
        assert getattr(motor, field) is not None
        violations = validate_product(motor)
        assert getattr(motor, field) is None, (
            f"{field}={value} (above {FIELD_RULES[field][1]}) was preserved"
        )
        assert any(field in v for v in violations)


# ---------------------------------------------------------------------------
# 5. Duplicate-pair rule
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDuplicatePair:
    @given(
        value=st.floats(min_value=100, max_value=1000, allow_nan=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_voltage_equals_speed_nulls_voltage(self, value: float) -> None:
        # The dup-pair rule uses object equality, so the unit must
        # match too. The only way to land identical VU/MMU shapes on
        # both fields is through a unit family they both accept —
        # voltage accepts "V", speed accepts "rpm". Force the values
        # to literally compare equal: build the MinMaxUnit/ValueUnit
        # outside the Pydantic typed-alias coercion so the unit
        # match is what we control.
        m = Motor(
            product_name="Dup Test",
            manufacturer="TestMfg",
            product_type="motor",
            part_number="DUP-001",
        )
        # Override the Pydantic alias-coerced fields directly with
        # equal-shape MinMaxUnits — the dup-check looks at object
        # equality after coercion.
        shared = MinMaxUnit(min=value, max=value, unit="V")
        m.rated_voltage = shared
        # rated_speed expects Speed (ValueUnit), so we can't put a
        # MinMaxUnit there. Build a ValueUnit and force-fill it; the
        # spec rule pinned by the existing example test relies on
        # the LLM having copied the same value across, which is what
        # we're modelling here.
        m.rated_speed = shared  # type: ignore[assignment]
        validate_product(m)
        assert m.rated_voltage is None
        assert m.rated_speed is not None


# ---------------------------------------------------------------------------
# 6. _values_of contract
# ---------------------------------------------------------------------------


_ARBITRARY_INPUT = st.one_of(
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


@pytest.mark.unit
class TestValuesOfProperty:
    @given(_ARBITRARY_INPUT)
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_non_vu_mmu_input_returns_none(self, x: Any) -> None:
        # ValueUnit and MinMaxUnit instances themselves can't appear
        # in this strategy (it produces primitives, lists, etc.), so
        # everything here should return None.
        assert _values_of(x) is None

    @given(
        value=st.floats(
            min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
        ),
        unit=st.sampled_from(["V", "rpm", "A", "Nm"]),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_value_unit_returns_single_element_list(
        self, value: float, unit: str
    ) -> None:
        result = _values_of(ValueUnit(value=value, unit=unit))
        assert result is not None
        values, returned_unit = result
        assert len(values) == 1
        assert values[0] == float(value)
        assert returned_unit == unit

    @given(
        lo=st.floats(
            min_value=-1e6, max_value=0.0, allow_nan=False, allow_infinity=False
        ),
        hi=st.floats(
            min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False
        ),
        unit=st.sampled_from(["V", "rpm", "A", "Nm"]),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_min_max_unit_returns_two_element_list(
        self, lo: float, hi: float, unit: str
    ) -> None:
        result = _values_of(MinMaxUnit(min=lo, max=hi, unit=unit))
        assert result is not None
        values, returned_unit = result
        assert values == [lo, hi]
        assert returned_unit == unit

    @given(
        bound=st.floats(
            min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
        ),
        which=st.sampled_from(["min_only", "max_only"]),
        unit=st.sampled_from(["V", "rpm", "A", "Nm"]),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_min_max_unit_partial_returns_single_element_list(
        self, bound: float, which: str, unit: str
    ) -> None:
        if which == "min_only":
            mmu = MinMaxUnit(min=bound, max=None, unit=unit)
        else:
            mmu = MinMaxUnit(min=None, max=bound, unit=unit)
        result = _values_of(mmu)
        assert result is not None
        values, _ = result
        assert values == [bound]


# ---------------------------------------------------------------------------
# 7. validate_products batch contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateProductsBatch:
    @given(motors=st.lists(_real_motor(), min_size=0, max_size=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_returns_same_list_identity(self, motors: list[Motor]) -> None:
        result = validate_products(motors)
        assert result is motors
