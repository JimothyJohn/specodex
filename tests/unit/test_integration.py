"""Tests for the integration/port/compat layer."""

from __future__ import annotations

import pytest

from specodex.integration import check, ports_for
from specodex.integration.ports import (
    ElectricalPowerPort,
    FeedbackPort,
    FieldbusPort,
    MechanicalShaftPort,
)
from specodex.models.contactor import Contactor
from specodex.models.drive import Drive
from specodex.models.gearhead import Gearhead
from specodex.models.motor import Motor


MFG = "TestCo"


def _motor(**over) -> Motor:
    defaults = dict(
        product_name="TM-1",
        manufacturer=MFG,
        part_number="M-001",
        rated_voltage="200-240;V",
        rated_current="5;A",
        rated_power="1000;W",
        max_speed="3000;rpm",
        rated_torque="3;Nm",
        peak_torque="9;Nm",
        shaft_diameter="14;mm",
        frame_size="60",
        encoder_feedback_support="EnDat 2.2",
        type="ac servo",
    )
    defaults.update(over)
    return Motor(**defaults)


def _drive(**over) -> Drive:
    defaults = dict(
        product_name="TD-1",
        manufacturer=MFG,
        part_number="D-001",
        input_voltage="200-240;V",
        rated_current="10;A",
        rated_power="2000;W",
        encoder_feedback_support=["EnDat 2.2", "Resolver"],
        fieldbus=["EtherCAT"],
    )
    defaults.update(over)
    return Drive(**defaults)


def _gearhead(**over) -> Gearhead:
    defaults = dict(
        product_name="TG-1",
        manufacturer=MFG,
        part_number="G-001",
        product_type="gearhead",
        gear_ratio=10.0,
        frame_size="60",
        input_shaft_diameter="14;mm",
        max_input_speed="4000;rpm",
        max_continuous_torque="30;Nm",
    )
    defaults.update(over)
    return Gearhead(**defaults)


def _contactor(**over) -> Contactor:
    defaults = dict(
        product_name="TC-1",
        manufacturer=MFG,
        part_number="C-001",
        product_type="contactor",
        rated_operational_voltage_max="240;V",
        ie_ac3_400v="10;A",
        motor_power_ac3_400v_kw="2.2;kW",
    )
    defaults.update(over)
    return Contactor(**defaults)


class TestPortAdapters:
    def test_motor_exposes_three_ports(self) -> None:
        ports = ports_for(_motor())
        assert set(ports) == {"power_input", "shaft_output", "feedback"}
        assert isinstance(ports["power_input"], ElectricalPowerPort)
        assert isinstance(ports["shaft_output"], MechanicalShaftPort)
        assert isinstance(ports["feedback"], FeedbackPort)

    def test_drive_exposes_four_ports(self) -> None:
        ports = ports_for(_drive())
        assert set(ports) == {"mains_input", "motor_output", "feedback", "fieldbus"}
        assert isinstance(ports["fieldbus"], FieldbusPort)

    def test_gearhead_exposes_two_shaft_ports(self) -> None:
        ports = ports_for(_gearhead())
        assert set(ports) == {"shaft_input", "shaft_output"}
        assert ports["shaft_input"].direction == "input"
        assert ports["shaft_output"].direction == "output"


class TestDriveMotorCompat:
    def test_happy_path(self) -> None:
        r = check(_drive(), _motor())
        assert r.status == "ok"
        # The drive→motor power pair should be in results
        pair = next(
            (
                x
                for x in r.results
                if "motor_output" in x.from_port and "power_input" in x.to_port
            ),
            None,
        )
        assert pair is not None
        assert pair.status == "ok"

    def test_drive_undersized_current_fails(self) -> None:
        r = check(_drive(rated_current="2;A"), _motor(rated_current="5;A"))
        assert r.status == "fail"
        pair = next(x for x in r.results if "motor_output" in x.from_port)
        current = next(c for c in pair.checks if c.field == "current")
        assert current.status == "fail"

    def test_encoder_not_supported_fails(self) -> None:
        r = check(
            _drive(encoder_feedback_support=["Resolver"]),
            _motor(encoder_feedback_support="EnDat 2.2"),
        )
        pair = next(x for x in r.results if "feedback" in x.from_port)
        assert pair.status == "fail"

    def test_unit_aware_power_compare(self) -> None:
        # Drive in kW, motor in W → units module normalises both to W
        r = check(_drive(rated_power="2;kW"), _motor(rated_power="1000;W"))
        pair = next(x for x in r.results if "motor_output" in x.from_port)
        power = next(c for c in pair.checks if c.field == "power")
        assert power.status == "ok", power.detail


class TestMotorGearheadCompat:
    def test_happy_path(self) -> None:
        r = check(_motor(), _gearhead())
        pair = next(
            (
                x
                for x in r.results
                if "shaft_output" in x.from_port and "shaft_input" in x.to_port
            ),
            None,
        )
        assert pair is not None
        assert pair.status == "ok", [c.detail for c in pair.checks]

    def test_shaft_diameter_mismatch_fails(self) -> None:
        r = check(
            _motor(shaft_diameter="10;mm"), _gearhead(input_shaft_diameter="14;mm")
        )
        pair = next(x for x in r.results if "shaft_output" in x.from_port)
        shaft = next(c for c in pair.checks if c.field == "shaft_diameter")
        assert shaft.status == "fail"

    def test_frame_size_mismatch_fails(self) -> None:
        r = check(_motor(frame_size="42"), _gearhead(frame_size="60"))
        pair = next(x for x in r.results if "shaft_output" in x.from_port)
        frame = next(c for c in pair.checks if c.field == "frame_size")
        assert frame.status == "fail"

    def test_motor_overspeeds_gearhead_fails(self) -> None:
        r = check(_motor(max_speed="5000;rpm"), _gearhead(max_input_speed="4000;rpm"))
        pair = next(x for x in r.results if "shaft_output" in x.from_port)
        speed = next(c for c in pair.checks if c.field == "speed")
        assert speed.status == "fail"


class TestContactorMotorCompat:
    def test_happy_path(self) -> None:
        r = check(_contactor(), _motor())
        pair = next(
            (
                x
                for x in r.results
                if "load_output" in x.from_port and "power_input" in x.to_port
            ),
            None,
        )
        assert pair is not None
        # Voltage/current/power all sized correctly → ok
        assert pair.status == "ok", [c.detail for c in pair.checks]

    def test_contactor_undersized_current_fails(self) -> None:
        r = check(_contactor(ie_ac3_400v="2;A"), _motor(rated_current="5;A"))
        pair = next(x for x in r.results if "load_output" in x.from_port)
        current = next(c for c in pair.checks if c.field == "current")
        assert current.status == "fail"


class TestMissingFieldsArePartial:
    def test_missing_power_is_partial_not_fail(self) -> None:
        r = check(_drive(rated_power=None), _motor(rated_power=None))
        pair = next(x for x in r.results if "motor_output" in x.from_port)
        power = next(c for c in pair.checks if c.field == "power")
        assert power.status == "partial"


def test_unknown_product_type_raises() -> None:
    class Stub:
        product_type = "unknown"

    with pytest.raises(KeyError):
        ports_for(Stub())  # type: ignore[arg-type]


class TestFitsPartialMode:
    """`strict=False` downgrades fail→partial while preserving detail."""

    def test_undersized_current_softens_to_partial(self) -> None:
        r = check(
            _drive(rated_current="2;A"), _motor(rated_current="5;A"), strict=False
        )
        assert r.status == "partial"
        pair = next(x for x in r.results if "motor_output" in x.from_port)
        current = next(c for c in pair.checks if c.field == "current")
        assert current.status == "partial"
        # Detail must survive the softening so the UI can show what didn't line up.
        assert "supply 2.0 < demand 5.0" in current.detail

    def test_shaft_mismatch_softens(self) -> None:
        r = check(
            _motor(shaft_diameter="10;mm"),
            _gearhead(input_shaft_diameter="14;mm"),
            strict=False,
        )
        assert r.status == "partial"
        pair = next(x for x in r.results if "shaft_output" in x.from_port)
        shaft = next(c for c in pair.checks if c.field == "shaft_diameter")
        assert shaft.status == "partial"

    def test_strict_default_unchanged(self) -> None:
        # Sanity: existing strict callers see the same fails as before.
        r = check(_drive(rated_current="2;A"), _motor(rated_current="5;A"))
        assert r.status == "fail"

    def test_ok_pairs_stay_ok(self) -> None:
        r = check(_drive(), _motor(), strict=False)
        assert r.status == "ok"


class TestSerialization:
    def test_to_dict_round_trips_status_and_results(self) -> None:
        r = check(_drive(), _motor()).to_dict()
        assert r["from_type"] == "drive"
        assert r["to_type"] == "motor"
        assert r["status"] in {"ok", "partial", "fail"}
        assert isinstance(r["results"], list)
        assert all("checks" in pair for pair in r["results"])
