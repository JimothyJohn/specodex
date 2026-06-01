"""Tests for the device-relations layer (`specodex.relations`).

Exercises the three public predicates (compatible_motors,
compatible_drives, compatible_gearheads) plus the internal range / shaft
helpers. Fixtures are constructed inline rather than pulled from real
DB rows because the compatibility logic should be testable independent
of catalogue contents — a Tolomatic actuator behaving correctly today
shouldn't bind the test to that vendor's data model.
"""

from __future__ import annotations

from specodex.models.drive import Drive
from specodex.models.electric_cylinder import ElectricCylinder
from specodex.models.gearhead import Gearhead
from specodex.models.linear_actuator import LinearActuator
from specodex.models.motor import Motor
from specodex.relations import (
    _range_within,
    _shaft_compatible,
    _value_gte,
    _value_in_range,
    compatible_actuators,
    compatible_drives,
    compatible_gearheads,
    compatible_motors,
)

MFG = "TestVendor"


def _motor(
    *,
    name: str = "M1",
    mount: str | None = "NEMA 23",
    rated_voltage_min: float | None = 200,
    rated_voltage_max: float | None = 240,
    rated_current: float | None = 3.0,
    rated_torque: float | None = 1.0,
    rated_speed: float | None = 3000,
    shaft_dia: float | None = 14.0,
    encoder_protocol: str | None = "endat_2_2",
) -> Motor:
    """Build a Motor with sensible defaults — pass kwargs to override.

    Master schema: ``Motor.encoder_feedback_support`` is ``Optional[str]``.
    The relations predicate also supports the structured ``EncoderFeedback``
    shape that DOUBLE_TAP introduces, but tests here exercise the master
    shape until DOUBLE_TAP merges.
    """
    return Motor(
        product_name=name,
        product_type="motor",
        manufacturer=MFG,
        part_number=f"PN-{name}",
        motor_mount_pattern=mount,
        rated_voltage=(
            {"min": rated_voltage_min, "max": rated_voltage_max, "unit": "V"}
            if rated_voltage_min is not None or rated_voltage_max is not None
            else None
        ),
        rated_current={"value": rated_current, "unit": "A"} if rated_current else None,
        rated_torque={"value": rated_torque, "unit": "Nm"} if rated_torque else None,
        rated_speed={"value": rated_speed, "unit": "rpm"} if rated_speed else None,
        shaft_diameter={"value": shaft_dia, "unit": "mm"} if shaft_dia else None,
        encoder_feedback_support=encoder_protocol,
    )


def _drive(
    *,
    name: str = "D1",
    input_voltage_min: float = 200,
    input_voltage_max: float = 240,
    rated_current: float = 5.0,
    encoder_protocols: list[str] | None = None,
) -> Drive:
    if encoder_protocols is None:
        encoder_protocols = ["endat_2_2"]
    return Drive(
        product_name=name,
        product_type="drive",
        manufacturer=MFG,
        part_number=f"PN-{name}",
        input_voltage={"min": input_voltage_min, "max": input_voltage_max, "unit": "V"},
        rated_current={"value": rated_current, "unit": "A"},
        encoder_feedback_support=encoder_protocols,
    )


def _gearhead(
    *,
    name: str = "G1",
    input_mounts: list[str] | None = None,
    input_shaft_dia: float = 14.0,
) -> Gearhead:
    if input_mounts is None:
        input_mounts = ["NEMA 23"]
    return Gearhead(
        product_name=name,
        product_type="gearhead",
        manufacturer=MFG,
        part_number=f"PN-{name}",
        input_motor_mount=input_mounts,
        input_shaft_diameter={"value": input_shaft_dia, "unit": "mm"},
    )


def _linear_actuator(
    *,
    name: str = "LA1",
    mounts: list[str] | None = None,
    stroke_mm: float | None = None,
    push_force_n: float | None = None,
    linear_speed_mm_s: float | None = None,
) -> LinearActuator:
    if mounts is None:
        mounts = ["NEMA 23"]
    return LinearActuator(
        product_name=name,
        product_type="linear_actuator",
        manufacturer=MFG,
        part_number=f"PN-{name}",
        compatible_motor_mounts=mounts,
        stroke={"value": stroke_mm, "unit": "mm"} if stroke_mm is not None else None,
        max_push_force=(
            {"value": push_force_n, "unit": "N"} if push_force_n is not None else None
        ),
        max_linear_speed=(
            {"value": linear_speed_mm_s, "unit": "mm/s"}
            if linear_speed_mm_s is not None
            else None
        ),
    )


def _electric_cylinder(
    *,
    name: str = "EC1",
    mount: str | None = "NEMA 23",
) -> ElectricCylinder:
    return ElectricCylinder(
        product_name=name,
        product_type="electric_cylinder",
        manufacturer=MFG,
        part_number=f"PN-{name}",
        motor_mount_pattern=mount,
    )


# ---------------------------------------------------------------------------
# Internal predicate tests — narrow, value-shape-focused.
# ---------------------------------------------------------------------------


class TestValueInRange:
    def test_value_inside_range(self):
        from specodex.models.common import MinMaxUnit, ValueUnit

        assert _value_in_range(
            ValueUnit(value=220, unit="V"),
            MinMaxUnit(min=200, max=240, unit="V"),
        )

    def test_value_below_range(self):
        from specodex.models.common import MinMaxUnit, ValueUnit

        assert not _value_in_range(
            ValueUnit(value=180, unit="V"),
            MinMaxUnit(min=200, max=240, unit="V"),
        )

    def test_value_above_range(self):
        from specodex.models.common import MinMaxUnit, ValueUnit

        assert not _value_in_range(
            ValueUnit(value=480, unit="V"),
            MinMaxUnit(min=200, max=240, unit="V"),
        )

    def test_none_inputs(self):
        assert not _value_in_range(None, None)


class TestRangeWithin:
    def test_range_fully_inside(self):
        from specodex.models.common import MinMaxUnit

        assert _range_within(
            MinMaxUnit(min=210, max=230, unit="V"),
            MinMaxUnit(min=200, max=240, unit="V"),
        )

    def test_range_low_end_below_outer(self):
        from specodex.models.common import MinMaxUnit

        assert not _range_within(
            MinMaxUnit(min=180, max=230, unit="V"),
            MinMaxUnit(min=200, max=240, unit="V"),
        )

    def test_range_high_end_above_outer(self):
        from specodex.models.common import MinMaxUnit

        assert not _range_within(
            MinMaxUnit(min=210, max=260, unit="V"),
            MinMaxUnit(min=200, max=240, unit="V"),
        )

    def test_single_point_inner(self):
        # Motor rated at exactly 220V (min == max) should fit a 200-240 drive.
        from specodex.models.common import MinMaxUnit

        assert _range_within(
            MinMaxUnit(min=220, max=220, unit="V"),
            MinMaxUnit(min=200, max=240, unit="V"),
        )

    def test_none_inputs(self):
        # Pydantic prevents constructing a MinMaxUnit with both min and max
        # None, so the "empty range" defensive branch in _range_within is
        # unreachable through model construction. Cover the None case
        # directly instead — that's the actual integration risk
        # (e.g. a record with no voltage info at all).
        from specodex.models.common import MinMaxUnit

        assert not _range_within(None, MinMaxUnit(min=200, max=240, unit="V"))
        assert not _range_within(MinMaxUnit(min=200, max=240, unit="V"), None)


class TestValueGte:
    def test_a_greater_than_b(self):
        from specodex.models.common import ValueUnit

        assert _value_gte(
            ValueUnit(value=5.0, unit="A"),
            ValueUnit(value=3.0, unit="A"),
        )

    def test_a_equal_to_b(self):
        from specodex.models.common import ValueUnit

        assert _value_gte(
            ValueUnit(value=3.0, unit="A"),
            ValueUnit(value=3.0, unit="A"),
        )

    def test_a_less_than_b(self):
        from specodex.models.common import ValueUnit

        assert not _value_gte(
            ValueUnit(value=2.0, unit="A"),
            ValueUnit(value=3.0, unit="A"),
        )


class TestShaftCompatible:
    def test_exact_match(self):
        from specodex.models.common import ValueUnit

        assert _shaft_compatible(
            ValueUnit(value=14.0, unit="mm"),
            ValueUnit(value=14.0, unit="mm"),
        )

    def test_within_tolerance(self):
        from specodex.models.common import ValueUnit

        assert _shaft_compatible(
            ValueUnit(value=14.0, unit="mm"),
            ValueUnit(value=14.05, unit="mm"),
        )

    def test_outside_tolerance(self):
        from specodex.models.common import ValueUnit

        assert not _shaft_compatible(
            ValueUnit(value=14.0, unit="mm"),
            ValueUnit(value=15.0, unit="mm"),
        )


# ---------------------------------------------------------------------------
# Public predicate tests — exercise the full record-shape comparisons.
# ---------------------------------------------------------------------------


class TestCompatibleMotors:
    def test_linear_actuator_returns_matching_mount(self):
        actuator = _linear_actuator(mounts=["NEMA 23", "NEMA 34"])
        m23 = _motor(name="M23", mount="NEMA 23")
        m34 = _motor(name="M34", mount="NEMA 34")
        m17 = _motor(name="M17", mount="NEMA 17")

        result = compatible_motors(actuator, [m23, m34, m17])

        assert {m.product_name for m in result} == {"M23", "M34"}

    def test_electric_cylinder_single_mount(self):
        cyl = _electric_cylinder(mount="NEMA 23")
        m23 = _motor(mount="NEMA 23")
        m34 = _motor(name="M34", mount="NEMA 34")

        assert compatible_motors(cyl, [m23, m34]) == [m23]

    def test_actuator_with_no_mounts_returns_empty(self):
        actuator = _linear_actuator(mounts=[])
        m23 = _motor(mount="NEMA 23")

        assert compatible_motors(actuator, [m23]) == []

    def test_motor_with_no_mount_excluded(self):
        actuator = _linear_actuator(mounts=["NEMA 23"])
        m_unknown = _motor(mount=None)

        assert compatible_motors(actuator, [m_unknown]) == []

    def test_min_torque_filter(self):
        actuator = _linear_actuator(mounts=["NEMA 23"])
        weak = _motor(name="weak", rated_torque=0.5)
        strong = _motor(name="strong", rated_torque=2.0)
        from specodex.models.common import ValueUnit

        result = compatible_motors(
            actuator, [weak, strong], min_torque=ValueUnit(value=1.0, unit="Nm")
        )

        assert [m.product_name for m in result] == ["strong"]

    def test_min_speed_filter(self):
        actuator = _linear_actuator(mounts=["NEMA 23"])
        slow = _motor(name="slow", rated_speed=1500)
        fast = _motor(name="fast", rated_speed=4500)
        from specodex.models.common import ValueUnit

        result = compatible_motors(
            actuator, [slow, fast], min_speed=ValueUnit(value=3000, unit="rpm")
        )

        assert [m.product_name for m in result] == ["fast"]


class TestCompatibleDrives:
    def test_basic_match(self):
        motor = _motor()
        drive = _drive()

        assert compatible_drives(motor, [drive]) == [drive]

    def test_voltage_below_drive_input(self):
        # Motor rated 100-120V, drive accepts 200-240V → not compatible.
        motor = _motor(rated_voltage_min=100, rated_voltage_max=120)
        drive = _drive(input_voltage_min=200, input_voltage_max=240)

        assert compatible_drives(motor, [drive]) == []

    def test_drive_undersized_current(self):
        # Drive rated 2A, motor demands 3A → not compatible.
        motor = _motor(rated_current=3.0)
        drive = _drive(rated_current=2.0)

        assert compatible_drives(motor, [drive]) == []

    def test_encoder_protocol_mismatch(self):
        motor = _motor(encoder_protocol="endat_2_2")
        drive = _drive(encoder_protocols=["biss_c"])

        assert compatible_drives(motor, [drive]) == []

    def test_motor_with_no_encoder_excluded(self):
        motor = _motor(encoder_protocol=None)
        drive = _drive()

        assert compatible_drives(motor, [drive]) == []

    def test_motor_missing_voltage_excluded(self):
        motor = _motor(rated_voltage_min=None, rated_voltage_max=None)
        drive = _drive()

        assert compatible_drives(motor, [drive]) == []


class TestCompatibleGearheads:
    def test_basic_match(self):
        motor = _motor(mount="NEMA 23", shaft_dia=14.0)
        gear = _gearhead(input_mounts=["NEMA 23"], input_shaft_dia=14.0)

        assert compatible_gearheads(motor, [gear]) == [gear]

    def test_mount_mismatch(self):
        motor = _motor(mount="NEMA 17")
        gear = _gearhead(input_mounts=["NEMA 23"])

        assert compatible_gearheads(motor, [gear]) == []

    def test_shaft_outside_tolerance(self):
        motor = _motor(shaft_dia=14.0)
        gear = _gearhead(input_shaft_dia=15.0)

        assert compatible_gearheads(motor, [gear]) == []

    def test_motor_with_no_mount_excluded(self):
        motor = _motor(mount=None)
        gear = _gearhead(input_mounts=["NEMA 23"])

        assert compatible_gearheads(motor, [gear]) == []

    def test_gearhead_with_no_input_mount_excluded(self):
        motor = _motor(mount="NEMA 23")
        # Construct directly rather than via the _gearhead helper so we
        # actually get input_motor_mount=None instead of the helper's
        # default replacement.
        gear = Gearhead(
            product_name="G_no_mount",
            product_type="gearhead",
            manufacturer=MFG,
            part_number="PN-noMount",
            input_motor_mount=None,
            input_shaft_diameter={"value": 14.0, "unit": "mm"},
        )

        assert compatible_gearheads(motor, [gear]) == []

    def test_filters_only_matching_in_population(self):
        motor = _motor(mount="NEMA 23", shaft_dia=14.0)
        match = _gearhead(name="match", input_mounts=["NEMA 23"], input_shaft_dia=14.0)
        wrong_mount = _gearhead(name="wrong_mount", input_mounts=["NEMA 17"])
        wrong_shaft = _gearhead(
            name="wrong_shaft", input_mounts=["NEMA 23"], input_shaft_dia=12.0
        )

        result = compatible_gearheads(motor, [match, wrong_mount, wrong_shaft])
        assert [g.product_name for g in result] == ["match"]


class TestCompatibleActuators:
    def test_no_floors_returns_all(self):
        # "Blank = no constraint applied" — with no floors set, every
        # actuator passes, including ones with no spec fields on file.
        a1 = _linear_actuator(name="A1", stroke_mm=200, push_force_n=300)
        a2 = _linear_actuator(name="A2")  # no spec fields at all

        result = compatible_actuators([a1, a2])

        assert {a.product_name for a in result} == {"A1", "A2"}

    def test_min_stroke_filters(self):
        short = _linear_actuator(name="short", stroke_mm=150)
        exact = _linear_actuator(name="exact", stroke_mm=200)
        long = _linear_actuator(name="long", stroke_mm=400)

        result = compatible_actuators([short, exact, long], min_stroke_mm=200)

        # Boundary value (==) passes; below is excluded.
        assert {a.product_name for a in result} == {"exact", "long"}

    def test_min_peak_force_filters(self):
        weak = _linear_actuator(name="weak", push_force_n=100)
        strong = _linear_actuator(name="strong", push_force_n=500)

        result = compatible_actuators([weak, strong], min_peak_force_n=200)

        assert [a.product_name for a in result] == ["strong"]

    def test_min_peak_velocity_filters(self):
        slow = _linear_actuator(name="slow", linear_speed_mm_s=500)
        fast = _linear_actuator(name="fast", linear_speed_mm_s=2000)

        result = compatible_actuators([slow, fast], min_peak_velocity_mm_s=1000)

        assert [a.product_name for a in result] == ["fast"]

    def test_combined_floors_all_must_pass(self):
        # Clears stroke + force but not velocity → excluded. Only the
        # actuator that clears all three survives.
        partial = _linear_actuator(
            name="partial", stroke_mm=300, push_force_n=400, linear_speed_mm_s=500
        )
        full = _linear_actuator(
            name="full", stroke_mm=300, push_force_n=400, linear_speed_mm_s=1500
        )

        result = compatible_actuators(
            [partial, full],
            min_stroke_mm=200,
            min_peak_force_n=300,
            min_peak_velocity_mm_s=1000,
        )

        assert [a.product_name for a in result] == ["full"]

    def test_missing_field_excluded_when_floor_set(self):
        # Precision over recall: a floor that's set excludes actuators
        # missing the field the floor needs.
        no_force = _linear_actuator(name="no_force", stroke_mm=300)

        assert compatible_actuators([no_force], min_peak_force_n=100) == []

    def test_unit_mismatch_excluded_when_floor_set(self):
        # max_linear_speed is a generic ValueUnit (no canonical
        # normalisation). A record carrying m/s is excluded when the
        # floor is expressed in mm/s — comparing across units would be a
        # silent precision bug.
        wrong_unit = LinearActuator(
            product_name="wrong_unit",
            product_type="linear_actuator",
            manufacturer=MFG,
            part_number="PN-wrongUnit",
            compatible_motor_mounts=["NEMA 23"],
            max_linear_speed={"value": 2.0, "unit": "m/s"},
        )

        assert compatible_actuators([wrong_unit], min_peak_velocity_mm_s=1000) == []

    def test_unset_floor_does_not_exclude_missing_field(self):
        # An actuator with no force field is NOT excluded when only the
        # stroke floor is set — unset dimensions apply no constraint.
        no_force = _linear_actuator(name="no_force", stroke_mm=300)

        result = compatible_actuators([no_force], min_stroke_mm=200)

        assert [a.product_name for a in result] == ["no_force"]
