"""Property tests for ``specodex.relations`` compatibility predicates.

The example-based companion (``test_relations.py``) pins specific
mount/voltage/encoder shapes against happy-path fixtures. This file
generates *adversarial* inputs — arbitrary ``ValueUnit`` / ``MinMaxUnit``
pairs fed to the internal predicates, plus randomised motor/drive/
gearhead/actuator records fed to the public ``compatible_*`` queries —
and asserts the documented contracts hold for every case the strategy
can produce.

**Contracts under test:**

1. Every internal predicate (``_value_in_range``, ``_range_within``,
   ``_value_gte``, ``_shaft_compatible``, ``_meets_floor``,
   ``_encoder_protocol_intersect``) is total: it returns ``bool`` and
   never raises, even on adversarial inputs. These are called from the
   public queries with no surrounding ``try`` block, so any leak crashes
   the whole compatibility list.
2. **Missing-data exclusion** (the module's central design rule):
   any ``None`` input to the internal predicates returns ``False``.
   A mismatched unit returns ``False``. Recall loss is preferred over
   wrong-hardware-in-BOM precision loss.
3. **Reflexivity** of the symmetric predicates on finite, well-formed
   inputs: ``_value_gte(v, v)`` is ``True``; ``_shaft_compatible(v, v)``
   is ``True``; ``_range_within(r, r)`` is ``True`` when ``r`` has both
   bounds set.
4. **Monotonicity** of ``_value_gte`` — when units agree, the result
   matches the numeric ``>=`` comparison. A sign flip here would silently
   pair every motor with every drive (or none of them).
5. **Public queries** are subset-preserving: every returned product is
   a member of the input db. Passing ``None`` for every optional floor
   to ``compatible_actuators`` returns the full input list (vacuous
   filter). Adding constraints can only shrink the candidate set —
   never grow it.
6. **Mount-precision rule**: ``compatible_motors`` never returns a motor
   whose ``motor_mount_pattern`` is absent from the actuator's mount
   set. ``compatible_gearheads`` never returns a gearhead whose
   ``input_motor_mount`` list excludes the motor's mount. Missing
   mount data on either side yields an empty list — never a "permissive
   fallback" result.
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.drive import Drive
from specodex.models.encoder import EncoderFeedback
from specodex.models.gearhead import Gearhead
from specodex.models.linear_actuator import LinearActuator
from specodex.models.motor import Motor
from specodex.relations import (
    _encoder_protocol_intersect,
    _meets_floor,
    _range_within,
    _shaft_compatible,
    _value_gte,
    _value_in_range,
    compatible_actuators,
    compatible_drives,
    compatible_gearheads,
    compatible_motors,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


# Stick to canonical units so the ValueUnit / MinMaxUnit after-validator
# normalisation doesn't reshape inputs underneath the property. The
# properties pin relations.py's predicate behaviour, not the unit
# normaliser's.
_CANONICAL_UNITS = st.sampled_from(["V", "A", "mm", "Nm", "rpm", "N", "mm/s"])


# Reasonable scalar magnitudes — NaN/inf is excluded from the *semantic*
# properties (NaN >= anything is False, which breaks reflexivity) and
# surfaces only via the adversarial "anything else" branch where the
# contract under test is just "doesn't raise".
_FINITE_SCALARS = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)
_POSITIVE_FINITE = st.floats(
    min_value=0.001, max_value=1e6, allow_nan=False, allow_infinity=False
)


@st.composite
def _value_unit(draw: st.DrawFn) -> ValueUnit:
    return ValueUnit(value=draw(_FINITE_SCALARS), unit=draw(_CANONICAL_UNITS))


@st.composite
def _bounded_min_max_unit(draw: st.DrawFn) -> MinMaxUnit:
    """A MinMaxUnit with both bounds populated and ``min <= max``.

    Constrained to the bounded shape so the ``_range_within`` and
    ``_value_in_range`` reflexivity properties hold. One-sided ranges
    are exercised by the model-validator behaviour elsewhere; here we
    pin the algebra.
    """
    unit = draw(_CANONICAL_UNITS)
    lo = draw(_FINITE_SCALARS)
    hi = draw(_FINITE_SCALARS.filter(lambda x: x >= lo))
    return MinMaxUnit(min=lo, max=hi, unit=unit)


@st.composite
def _any_min_max_unit(draw: st.DrawFn) -> MinMaxUnit:
    """A MinMaxUnit with at least one bound populated (the validator
    rejects the all-None case). One-sided ranges included so the
    never-raise / missing-data properties cover the full Pydantic-
    accepted shape.
    """
    shape = draw(st.sampled_from(["both", "min_only", "max_only"]))
    unit = draw(_CANONICAL_UNITS)
    if shape == "both":
        lo = draw(_FINITE_SCALARS)
        hi = draw(_FINITE_SCALARS.filter(lambda x: x >= lo))
        return MinMaxUnit(min=lo, max=hi, unit=unit)
    if shape == "min_only":
        return MinMaxUnit(min=draw(_FINITE_SCALARS), max=None, unit=unit)
    return MinMaxUnit(min=None, max=draw(_FINITE_SCALARS), unit=unit)


# Test-only mount vocabulary kept small so collisions across motor /
# actuator / gearhead are common enough to exercise the matching paths.
_MOUNTS = st.sampled_from(["NEMA 17", "NEMA 23", "NEMA 34", "IEC 80", "IEC 90"])
# Canonical EncoderProtocol literal values (see specodex/models/encoder.py).
# Drive `encoder_feedback_support` is typed `List[EncoderProtocol]`, so the
# protocols on both sides must come from the literal. Using a raw free-text
# string (e.g. "biss_c") on the motor side would round-trip through
# `EncoderFeedback`'s coercion and the protocol field would land as `None`
# unless the string matched a known synonym — see the encoder.py parser.
_PROTOCOLS = st.sampled_from(["endat_2_2", "biss_c", "hiperface", "tamagawa_t_format"])


# ---------------------------------------------------------------------------
# Internal predicate contracts
# ---------------------------------------------------------------------------


class TestValueInRangeContract:
    """``_value_in_range`` is the workhorse for "rated voltage falls
    inside drive input envelope" checks. The contract: total function,
    ``False`` on None / unit mismatch, matches arithmetic otherwise.
    """

    @given(
        value=st.one_of(st.none(), _value_unit()),
        rng=st.one_of(st.none(), _any_min_max_unit()),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises_returns_bool(
        self, value: Optional[ValueUnit], rng: Optional[MinMaxUnit]
    ) -> None:
        try:
            result = _value_in_range(value, rng)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"_value_in_range raised {type(exc).__name__}: {exc!r}\n"
                f"value={value!r}, rng={rng!r}"
            )
        assert isinstance(result, bool)

    @given(rng=_any_min_max_unit())
    @settings(max_examples=100, deadline=None)
    def test_none_value_is_false(self, rng: MinMaxUnit) -> None:
        assert _value_in_range(None, rng) is False

    @given(value=_value_unit())
    @settings(max_examples=100, deadline=None)
    def test_none_range_is_false(self, value: ValueUnit) -> None:
        assert _value_in_range(value, None) is False

    @given(value=_value_unit(), rng=_bounded_min_max_unit())
    @settings(max_examples=200, deadline=None)
    def test_unit_mismatch_is_false(self, value: ValueUnit, rng: MinMaxUnit) -> None:
        if value.unit == rng.unit:
            return
        assert _value_in_range(value, rng) is False, (
            f"unit mismatch ({value.unit} vs {rng.unit}) should be False"
        )

    @given(point=_FINITE_SCALARS, unit=_CANONICAL_UNITS)
    @settings(max_examples=100, deadline=None)
    def test_point_inside_collapsed_range_is_true(
        self, point: float, unit: str
    ) -> None:
        """A value sitting exactly at the (min == max) collapse-point of
        a range must be in range — the inclusive boundary is the whole
        reason the predicate uses ``<`` and ``>`` not ``<=`` and ``>=``.
        """
        v = ValueUnit(value=point, unit=unit)
        r = MinMaxUnit(min=point, max=point, unit=unit)
        assert _value_in_range(v, r) is True

    @given(value=_value_unit(), rng=_bounded_min_max_unit())
    @settings(max_examples=200, deadline=None)
    def test_matches_arithmetic_when_units_agree(
        self, value: ValueUnit, rng: MinMaxUnit
    ) -> None:
        if value.unit != rng.unit:
            return
        # rng built via _bounded_min_max_unit has both bounds set.
        assert rng.min is not None and rng.max is not None
        expected = rng.min <= value.value <= rng.max
        assert _value_in_range(value, rng) is expected


class TestRangeWithinContract:
    @given(
        inner=st.one_of(st.none(), _any_min_max_unit()),
        outer=st.one_of(st.none(), _any_min_max_unit()),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises_returns_bool(
        self, inner: Optional[MinMaxUnit], outer: Optional[MinMaxUnit]
    ) -> None:
        try:
            result = _range_within(inner, outer)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_range_within raised {type(exc).__name__}: {exc!r}\n"
                f"inner={inner!r}, outer={outer!r}"
            )
        assert isinstance(result, bool)

    @given(outer=_any_min_max_unit())
    @settings(max_examples=100, deadline=None)
    def test_none_inner_is_false(self, outer: MinMaxUnit) -> None:
        assert _range_within(None, outer) is False

    @given(inner=_any_min_max_unit())
    @settings(max_examples=100, deadline=None)
    def test_none_outer_is_false(self, inner: MinMaxUnit) -> None:
        assert _range_within(inner, None) is False

    @given(inner=_bounded_min_max_unit(), outer=_bounded_min_max_unit())
    @settings(max_examples=200, deadline=None)
    def test_unit_mismatch_is_false(self, inner: MinMaxUnit, outer: MinMaxUnit) -> None:
        if inner.unit == outer.unit:
            return
        assert _range_within(inner, outer) is False

    @given(r=_bounded_min_max_unit())
    @settings(max_examples=100, deadline=None)
    def test_reflexive_on_bounded_range(self, r: MinMaxUnit) -> None:
        """A fully-bounded range fits inside itself. This is the floor
        of every voltage compat check (the same drive is compatible
        with the same motor)."""
        assert _range_within(r, r) is True

    @given(inner=_bounded_min_max_unit(), outer=_bounded_min_max_unit())
    @settings(max_examples=200, deadline=None)
    def test_matches_arithmetic_on_bounded_inputs(
        self, inner: MinMaxUnit, outer: MinMaxUnit
    ) -> None:
        if inner.unit != outer.unit:
            return
        assert inner.min is not None and inner.max is not None
        assert outer.min is not None and outer.max is not None
        expected = inner.min >= outer.min and inner.max <= outer.max
        assert _range_within(inner, outer) is expected


class TestValueGteContract:
    @given(
        a=st.one_of(st.none(), _value_unit()),
        b=st.one_of(st.none(), _value_unit()),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises_returns_bool(
        self, a: Optional[ValueUnit], b: Optional[ValueUnit]
    ) -> None:
        try:
            result = _value_gte(a, b)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_value_gte raised {type(exc).__name__}: {exc!r}\na={a!r}, b={b!r}"
            )
        assert isinstance(result, bool)

    @given(v=_value_unit())
    @settings(max_examples=100, deadline=None)
    def test_none_inputs_are_false(self, v: ValueUnit) -> None:
        assert _value_gte(None, v) is False
        assert _value_gte(v, None) is False
        assert _value_gte(None, None) is False

    @given(a=_value_unit(), b=_value_unit())
    @settings(max_examples=200, deadline=None)
    def test_unit_mismatch_is_false(self, a: ValueUnit, b: ValueUnit) -> None:
        if a.unit == b.unit:
            return
        assert _value_gte(a, b) is False

    @given(v=_value_unit())
    @settings(max_examples=100, deadline=None)
    def test_reflexive_on_finite_values(self, v: ValueUnit) -> None:
        # Hypothesis strategy excludes NaN, so reflexivity holds.
        assert _value_gte(v, v) is True

    @given(
        a_val=_FINITE_SCALARS,
        b_val=_FINITE_SCALARS,
        unit=_CANONICAL_UNITS,
    )
    @settings(max_examples=200, deadline=None)
    def test_matches_arithmetic_when_units_agree(
        self, a_val: float, b_val: float, unit: str
    ) -> None:
        a = ValueUnit(value=a_val, unit=unit)
        b = ValueUnit(value=b_val, unit=unit)
        assert _value_gte(a, b) is (a_val >= b_val)


class TestShaftCompatibleContract:
    @given(
        motor=st.one_of(st.none(), _value_unit()),
        gearhead=st.one_of(st.none(), _value_unit()),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises_returns_bool(
        self, motor: Optional[ValueUnit], gearhead: Optional[ValueUnit]
    ) -> None:
        try:
            result = _shaft_compatible(motor, gearhead)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_shaft_compatible raised {type(exc).__name__}: {exc!r}\n"
                f"motor={motor!r}, gearhead={gearhead!r}"
            )
        assert isinstance(result, bool)

    @given(v=_value_unit())
    @settings(max_examples=100, deadline=None)
    def test_none_inputs_are_false(self, v: ValueUnit) -> None:
        assert _shaft_compatible(None, v) is False
        assert _shaft_compatible(v, None) is False

    @given(a=_value_unit(), b=_value_unit())
    @settings(max_examples=200, deadline=None)
    def test_unit_mismatch_is_false(self, a: ValueUnit, b: ValueUnit) -> None:
        if a.unit == b.unit:
            return
        assert _shaft_compatible(a, b) is False

    @given(v=_value_unit())
    @settings(max_examples=100, deadline=None)
    def test_reflexive_on_finite(self, v: ValueUnit) -> None:
        assert _shaft_compatible(v, v) is True

    @given(a=_value_unit(), b=_value_unit())
    @settings(max_examples=200, deadline=None)
    def test_symmetric(self, a: ValueUnit, b: ValueUnit) -> None:
        """The 0.1mm tolerance is on the absolute difference, so the
        predicate must be symmetric — swapping arguments cannot change
        the verdict."""
        assert _shaft_compatible(a, b) == _shaft_compatible(b, a)


class TestMeetsFloorContract:
    @given(
        value=st.one_of(st.none(), _value_unit()),
        floor=_FINITE_SCALARS,
        unit=_CANONICAL_UNITS,
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises_returns_bool(
        self, value: Optional[ValueUnit], floor: float, unit: str
    ) -> None:
        try:
            result = _meets_floor(value, floor, unit)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_meets_floor raised {type(exc).__name__}: {exc!r}\n"
                f"value={value!r}, floor={floor!r}, unit={unit!r}"
            )
        assert isinstance(result, bool)

    @given(floor=_FINITE_SCALARS, unit=_CANONICAL_UNITS)
    @settings(max_examples=100, deadline=None)
    def test_none_value_is_false(self, floor: float, unit: str) -> None:
        assert _meets_floor(None, floor, unit) is False

    @given(value=_value_unit(), floor=_FINITE_SCALARS, unit=_CANONICAL_UNITS)
    @settings(max_examples=200, deadline=None)
    def test_unit_mismatch_is_false(
        self, value: ValueUnit, floor: float, unit: str
    ) -> None:
        if value.unit == unit:
            return
        assert _meets_floor(value, floor, unit) is False

    @given(v=_value_unit())
    @settings(max_examples=100, deadline=None)
    def test_reflexive_at_own_value(self, v: ValueUnit) -> None:
        # A value clears its own value-as-floor, by inclusive >=.
        assert _meets_floor(v, v.value, v.unit) is True

    @given(
        v_val=_FINITE_SCALARS,
        floor=_FINITE_SCALARS,
        unit=_CANONICAL_UNITS,
    )
    @settings(max_examples=200, deadline=None)
    def test_matches_arithmetic_when_units_agree(
        self, v_val: float, floor: float, unit: str
    ) -> None:
        v = ValueUnit(value=v_val, unit=unit)
        assert _meets_floor(v, floor, unit) is (v_val >= floor)


# ---------------------------------------------------------------------------
# _encoder_protocol_intersect — narrow predicate, motor/drive composition
# ---------------------------------------------------------------------------


def _motor(
    *,
    mount: Optional[str] = "NEMA 23",
    rated_voltage: Optional[MinMaxUnit] = None,
    rated_current: Optional[ValueUnit] = None,
    rated_torque: Optional[ValueUnit] = None,
    rated_speed: Optional[ValueUnit] = None,
    shaft_dia: Optional[ValueUnit] = None,
    encoder_protocol: Optional[str] = "endat_2_2",
) -> Motor:
    # Build the EncoderFeedback explicitly so the resulting motor's
    # ``encoder_feedback_support.protocol`` is exactly the literal we
    # asked for. Passing a raw string would route through the encoder.py
    # synonym parser, which round-trips ``"biss_c"`` to ``None`` because
    # the canonical synonym is hyphenated (``"biss-c"`` → ``"biss_c"``).
    encoder: Optional[EncoderFeedback]
    if encoder_protocol is None:
        encoder = None
    else:
        encoder = EncoderFeedback(device="unknown", protocol=encoder_protocol)
    return Motor(
        product_name="m",
        product_type="motor",
        manufacturer="TestVendor",
        part_number="m-pn",
        motor_mount_pattern=mount,
        rated_voltage=rated_voltage,
        rated_current=rated_current,
        rated_torque=rated_torque,
        rated_speed=rated_speed,
        shaft_diameter=shaft_dia,
        encoder_feedback_support=encoder,
    )


def _drive(
    *,
    input_voltage: MinMaxUnit,
    rated_current: ValueUnit,
    encoder_protocols: Optional[List[str]] = None,
) -> Drive:
    return Drive(
        product_name="d",
        product_type="drive",
        manufacturer="TestVendor",
        part_number="d-pn",
        input_voltage=input_voltage,
        rated_current=rated_current,
        encoder_feedback_support=encoder_protocols,
    )


def _gearhead(
    *,
    input_mounts: List[str],
    input_shaft_dia: ValueUnit,
) -> Gearhead:
    return Gearhead(
        product_name="g",
        product_type="gearhead",
        manufacturer="TestVendor",
        part_number="g-pn",
        input_motor_mount=input_mounts,
        input_shaft_diameter=input_shaft_dia,
    )


def _linear_actuator(
    *,
    mounts: List[str],
    stroke: Optional[ValueUnit] = None,
    push_force: Optional[ValueUnit] = None,
    linear_speed: Optional[ValueUnit] = None,
) -> LinearActuator:
    return LinearActuator(
        product_name="la",
        product_type="linear_actuator",
        manufacturer="TestVendor",
        part_number="la-pn",
        compatible_motor_mounts=mounts,
        stroke=stroke,
        max_push_force=push_force,
        max_linear_speed=linear_speed,
    )


_PROTOCOL_OR_NONE = st.one_of(st.none(), _PROTOCOLS)


class TestEncoderProtocolIntersectContract:
    @given(
        motor_proto=_PROTOCOL_OR_NONE,
        drive_protos=st.one_of(
            st.none(), st.lists(_PROTOCOLS, max_size=4, unique=True)
        ),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises_returns_bool(
        self, motor_proto: Optional[str], drive_protos: Optional[List[str]]
    ) -> None:
        motor = _motor(encoder_protocol=motor_proto)
        # Drive needs voltage+current to construct; encoder list may be None.
        drive = _drive(
            input_voltage=MinMaxUnit(min=200, max=240, unit="V"),
            rated_current=ValueUnit(value=5.0, unit="A"),
            encoder_protocols=drive_protos,
        )
        try:
            result = _encoder_protocol_intersect(motor, drive)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_encoder_protocol_intersect raised {type(exc).__name__}: {exc!r}\n"
                f"motor_proto={motor_proto!r}, drive_protos={drive_protos!r}"
            )
        assert isinstance(result, bool)

    @given(drive_protos=st.lists(_PROTOCOLS, min_size=1, max_size=4, unique=True))
    @settings(max_examples=100, deadline=None)
    def test_none_motor_protocol_is_false(self, drive_protos: List[str]) -> None:
        motor = _motor(encoder_protocol=None)
        drive = _drive(
            input_voltage=MinMaxUnit(min=200, max=240, unit="V"),
            rated_current=ValueUnit(value=5.0, unit="A"),
            encoder_protocols=drive_protos,
        )
        assert _encoder_protocol_intersect(motor, drive) is False

    @given(motor_proto=_PROTOCOLS)
    @settings(max_examples=100, deadline=None)
    def test_none_drive_protocols_is_false(self, motor_proto: str) -> None:
        motor = _motor(encoder_protocol=motor_proto)
        drive = _drive(
            input_voltage=MinMaxUnit(min=200, max=240, unit="V"),
            rated_current=ValueUnit(value=5.0, unit="A"),
            encoder_protocols=None,
        )
        assert _encoder_protocol_intersect(motor, drive) is False

    @given(
        motor_proto=_PROTOCOLS,
        drive_protos=st.lists(_PROTOCOLS, min_size=1, max_size=4, unique=True),
    )
    @settings(max_examples=200, deadline=None)
    def test_matches_membership(
        self, motor_proto: str, drive_protos: List[str]
    ) -> None:
        motor = _motor(encoder_protocol=motor_proto)
        drive = _drive(
            input_voltage=MinMaxUnit(min=200, max=240, unit="V"),
            rated_current=ValueUnit(value=5.0, unit="A"),
            encoder_protocols=drive_protos,
        )
        assert _encoder_protocol_intersect(motor, drive) is (
            motor_proto in drive_protos
        )


# ---------------------------------------------------------------------------
# Public query contracts
# ---------------------------------------------------------------------------


@st.composite
def _actuator_strategy(draw: st.DrawFn) -> LinearActuator:
    """Random LinearActuator with possibly-missing fields. Each spec
    field is independently present or None, modelling real catalogue
    sparsity."""
    n_mounts = draw(st.integers(min_value=0, max_value=3))
    if n_mounts == 0:
        mounts: List[str] = []
    else:
        mounts = draw(
            st.lists(_MOUNTS, min_size=n_mounts, max_size=n_mounts, unique=True)
        )
    stroke = draw(
        st.one_of(
            st.none(),
            st.builds(lambda v: ValueUnit(value=v, unit="mm"), _POSITIVE_FINITE),
        )
    )
    push_force = draw(
        st.one_of(
            st.none(),
            st.builds(lambda v: ValueUnit(value=v, unit="N"), _POSITIVE_FINITE),
        )
    )
    speed = draw(
        st.one_of(
            st.none(),
            st.builds(lambda v: ValueUnit(value=v, unit="mm/s"), _POSITIVE_FINITE),
        )
    )
    return _linear_actuator(
        mounts=mounts,
        stroke=stroke,
        push_force=push_force,
        linear_speed=speed,
    )


@st.composite
def _motor_strategy(draw: st.DrawFn) -> Motor:
    return _motor(
        mount=draw(st.one_of(st.none(), _MOUNTS)),
        rated_voltage=draw(
            st.one_of(st.none(), st.just(MinMaxUnit(min=200, max=240, unit="V")))
        ),
        rated_current=draw(
            st.one_of(
                st.none(),
                st.builds(lambda v: ValueUnit(value=v, unit="A"), _POSITIVE_FINITE),
            )
        ),
        rated_torque=draw(
            st.one_of(
                st.none(),
                st.builds(lambda v: ValueUnit(value=v, unit="Nm"), _POSITIVE_FINITE),
            )
        ),
        rated_speed=draw(
            st.one_of(
                st.none(),
                st.builds(lambda v: ValueUnit(value=v, unit="rpm"), _POSITIVE_FINITE),
            )
        ),
        shaft_dia=draw(
            st.one_of(
                st.none(),
                st.builds(lambda v: ValueUnit(value=v, unit="mm"), _POSITIVE_FINITE),
            )
        ),
        encoder_protocol=draw(_PROTOCOL_OR_NONE),
    )


@st.composite
def _drive_strategy(draw: st.DrawFn) -> Drive:
    # Drive requires input_voltage and rated_current per schema.
    lo = draw(_POSITIVE_FINITE)
    hi = draw(_POSITIVE_FINITE.filter(lambda x: x >= lo))
    return _drive(
        input_voltage=MinMaxUnit(min=lo, max=hi, unit="V"),
        rated_current=ValueUnit(value=draw(_POSITIVE_FINITE), unit="A"),
        encoder_protocols=draw(
            st.one_of(
                st.none(),
                st.lists(_PROTOCOLS, max_size=3, unique=True),
            )
        ),
    )


@st.composite
def _gearhead_strategy(draw: st.DrawFn) -> Gearhead:
    n_mounts = draw(st.integers(min_value=0, max_value=3))
    mounts = (
        []
        if n_mounts == 0
        else draw(st.lists(_MOUNTS, min_size=n_mounts, max_size=n_mounts, unique=True))
    )
    return _gearhead(
        input_mounts=mounts,
        input_shaft_dia=ValueUnit(value=draw(_POSITIVE_FINITE), unit="mm"),
    )


class TestCompatibleActuatorsContract:
    @given(
        db=st.lists(_actuator_strategy(), max_size=8),
        min_stroke=st.one_of(st.none(), _POSITIVE_FINITE),
        min_force=st.one_of(st.none(), _POSITIVE_FINITE),
        min_velocity=st.one_of(st.none(), _POSITIVE_FINITE),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_output_is_subset_of_input(
        self,
        db: List[LinearActuator],
        min_stroke: Optional[float],
        min_force: Optional[float],
        min_velocity: Optional[float],
    ) -> None:
        result = compatible_actuators(
            db,
            min_stroke_mm=min_stroke,
            min_peak_force_n=min_force,
            min_peak_velocity_mm_s=min_velocity,
        )
        assert isinstance(result, list)
        # Identity-preserving subset: every returned actuator is in the
        # input by object identity (not just equality), and the relative
        # order is preserved.
        input_ids = [id(a) for a in db]
        for actuator in result:
            assert id(actuator) in input_ids, (
                "compatible_actuators returned an object not in the input db"
            )
        result_positions = [input_ids.index(id(a)) for a in result]
        assert result_positions == sorted(result_positions), (
            "compatible_actuators reordered input — should be filter, not sort"
        )

    @given(db=st.lists(_actuator_strategy(), max_size=8))
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_no_floors_returns_full_input(self, db: List[LinearActuator]) -> None:
        """Empty filter ≡ identity. Build's "blank = no constraint
        applied" rule depends on this — every form field starts None,
        and the unconstrained query must surface every catalogue row."""
        result = compatible_actuators(db)
        assert result == db

    @given(
        db=st.lists(_actuator_strategy(), max_size=6),
        min_stroke_a=_POSITIVE_FINITE,
        min_stroke_b=_POSITIVE_FINITE,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_tighter_floor_is_subset_of_looser(
        self,
        db: List[LinearActuator],
        min_stroke_a: float,
        min_stroke_b: float,
    ) -> None:
        """Monotonicity: raising any floor can only shrink the result.
        If a candidate clears the higher bar, it cleared the lower one
        too. Catches sign flips in the floor comparator."""
        lo, hi = sorted([min_stroke_a, min_stroke_b])
        loose = compatible_actuators(db, min_stroke_mm=lo)
        tight = compatible_actuators(db, min_stroke_mm=hi)
        loose_ids = {id(a) for a in loose}
        for actuator in tight:
            assert id(actuator) in loose_ids, (
                f"tighter floor surfaced an actuator that the looser floor "
                f"({lo} vs {hi}) excluded — monotonicity violated"
            )

    @given(
        db=st.lists(_actuator_strategy(), max_size=6),
        min_stroke=st.one_of(st.none(), _POSITIVE_FINITE),
        min_force=st.one_of(st.none(), _POSITIVE_FINITE),
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_idempotent(
        self,
        db: List[LinearActuator],
        min_stroke: Optional[float],
        min_force: Optional[float],
    ) -> None:
        """Filtering the filtered list gives the same list — every
        candidate already cleared the floors, so re-running the same
        floors against the result is a no-op."""
        first = compatible_actuators(
            db, min_stroke_mm=min_stroke, min_peak_force_n=min_force
        )
        second = compatible_actuators(
            first, min_stroke_mm=min_stroke, min_peak_force_n=min_force
        )
        assert second == first


class TestCompatibleMotorsContract:
    @given(
        actuator=_actuator_strategy(),
        motor_db=st.lists(_motor_strategy(), max_size=8),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_output_is_subset_of_motor_db(
        self, actuator: LinearActuator, motor_db: List[Motor]
    ) -> None:
        result = compatible_motors(actuator, motor_db)
        assert isinstance(result, list)
        input_ids = [id(m) for m in motor_db]
        for motor in result:
            assert id(motor) in input_ids

    @given(motor_db=st.lists(_motor_strategy(), max_size=8))
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_actuator_with_no_mounts_returns_empty(self, motor_db: List[Motor]) -> None:
        """The "exclude on missing data" rule applied to the actuator
        side: an actuator with empty `compatible_motor_mounts` returns
        no candidates, never the full motor_db."""
        actuator = _linear_actuator(mounts=[])
        assert compatible_motors(actuator, motor_db) == []

    @given(
        actuator=_actuator_strategy(),
        motor_db=st.lists(_motor_strategy(), max_size=8),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_every_returned_motor_mount_is_compatible(
        self, actuator: LinearActuator, motor_db: List[Motor]
    ) -> None:
        """The core mount-precision rule: a returned motor must have a
        non-None mount that appears in the actuator's mount set. A
        permissive fallback here would silently mis-pair hardware."""
        actuator_mounts = set(actuator.compatible_motor_mounts or [])
        result = compatible_motors(actuator, motor_db)
        for motor in result:
            assert motor.motor_mount_pattern is not None
            assert motor.motor_mount_pattern in actuator_mounts


class TestCompatibleDrivesContract:
    @given(
        motor=_motor_strategy(),
        drive_db=st.lists(_drive_strategy(), max_size=8),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_output_is_subset_of_drive_db(
        self, motor: Motor, drive_db: List[Drive]
    ) -> None:
        result = compatible_drives(motor, drive_db)
        assert isinstance(result, list)
        input_ids = [id(d) for d in drive_db]
        for drive in result:
            assert id(drive) in input_ids

    @given(drive_db=st.lists(_drive_strategy(), max_size=8))
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_motor_missing_voltage_returns_empty(self, drive_db: List[Drive]) -> None:
        motor = _motor(
            rated_voltage=None,
            rated_current=ValueUnit(value=3.0, unit="A"),
        )
        assert compatible_drives(motor, drive_db) == []

    @given(drive_db=st.lists(_drive_strategy(), max_size=8))
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_motor_missing_current_returns_empty(self, drive_db: List[Drive]) -> None:
        motor = _motor(
            rated_voltage=MinMaxUnit(min=200, max=240, unit="V"),
            rated_current=None,
        )
        assert compatible_drives(motor, drive_db) == []


class TestCompatibleGearheadsContract:
    @given(
        motor=_motor_strategy(),
        gearhead_db=st.lists(_gearhead_strategy(), max_size=8),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_output_is_subset_of_gearhead_db(
        self, motor: Motor, gearhead_db: List[Gearhead]
    ) -> None:
        result = compatible_gearheads(motor, gearhead_db)
        assert isinstance(result, list)
        input_ids = [id(g) for g in gearhead_db]
        for gearhead in result:
            assert id(gearhead) in input_ids

    @given(gearhead_db=st.lists(_gearhead_strategy(), max_size=8))
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_motor_missing_mount_returns_empty(
        self, gearhead_db: List[Gearhead]
    ) -> None:
        """The mount-precision rule applied to the gearhead side: a
        motor with no `motor_mount_pattern` excludes every gearhead,
        not the permissive "show every gearhead" fallback."""
        motor = _motor(mount=None)
        assert compatible_gearheads(motor, gearhead_db) == []

    @given(
        motor=_motor_strategy(),
        gearhead_db=st.lists(_gearhead_strategy(), max_size=8),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_every_returned_gearhead_accepts_motor_mount(
        self, motor: Motor, gearhead_db: List[Gearhead]
    ) -> None:
        """A returned gearhead's `input_motor_mount` list must contain
        the motor's `motor_mount_pattern`. Missing-data rows that
        slipped past this check would mis-pair hardware."""
        result = compatible_gearheads(motor, gearhead_db)
        for gearhead in result:
            assert motor.motor_mount_pattern is not None
            assert gearhead.input_motor_mount is not None
            assert motor.motor_mount_pattern in gearhead.input_motor_mount


# ---------------------------------------------------------------------------
# Adversarial "never raises" — arbitrary garbage to the internal predicates.
# ---------------------------------------------------------------------------


_ARBITRARY_OTHER: Any = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=10),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=5), st.integers(), max_size=2),
)


class TestInternalPredicatesNeverRaiseOnNoneInputs:
    """The internal predicates are typed as ``Optional[...]`` and the
    public queries call them without try/except. The contract is
    narrower than ``_scalar``'s — they take only ``None`` or
    well-typed Pydantic instances — but the ``None`` path must be
    routinely tested for the documented missing-data rule.
    """

    def test_value_in_range_none_none(self) -> None:
        assert _value_in_range(None, None) is False

    def test_range_within_none_none(self) -> None:
        assert _range_within(None, None) is False

    def test_value_gte_none_none(self) -> None:
        assert _value_gte(None, None) is False

    def test_shaft_compatible_none_none(self) -> None:
        assert _shaft_compatible(None, None) is False

    @given(floor=_FINITE_SCALARS, unit=_CANONICAL_UNITS)
    @settings(max_examples=50, deadline=None)
    def test_meets_floor_none_value(self, floor: float, unit: str) -> None:
        assert _meets_floor(None, floor, unit) is False
