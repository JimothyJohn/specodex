"""Tests for unit normalization module."""

import pytest

from specodex.models.common import ValueUnit
from specodex.units import normalize_unit_value


@pytest.mark.unit
class TestNormalizeUnitValue:
    """Test the low-level numeric normalize_unit_value function."""

    # --- Torque ---
    def test_mNm_to_Nm(self):
        val, unit = normalize_unit_value(500, "mNm")
        assert val == 0.5
        assert unit == "Nm"

    def test_oz_in_to_Nm(self):
        val, unit = normalize_unit_value(100, "oz-in")
        assert unit == "Nm"
        assert val == pytest.approx(0.706155, rel=1e-3)

    def test_lb_ft_to_Nm(self):
        val, unit = normalize_unit_value(1, "lb-ft")
        assert unit == "Nm"
        assert val == pytest.approx(1.35582, rel=1e-3)

    def test_kgf_cm_to_Nm(self):
        val, unit = normalize_unit_value(10, "kgf·cm")
        assert unit == "Nm"
        assert val == pytest.approx(0.980665, rel=1e-3)

    def test_kNm_to_Nm(self):
        val, unit = normalize_unit_value(2, "kNm")
        assert val == 2000
        assert unit == "Nm"

    # --- Power ---
    def test_mW_to_W(self):
        val, unit = normalize_unit_value(500, "mW")
        assert val == 0.5
        assert unit == "W"

    def test_kW_to_W(self):
        val, unit = normalize_unit_value(1.5, "kW")
        assert val == 1500
        assert unit == "W"

    def test_hp_to_W(self):
        val, unit = normalize_unit_value(1, "hp")
        assert unit == "W"
        assert val == pytest.approx(745.7, rel=1e-3)

    # --- Current ---
    def test_mA_to_A(self):
        val, unit = normalize_unit_value(500, "mA")
        assert val == 0.5
        assert unit == "A"

    def test_uA_to_A(self):
        val, unit = normalize_unit_value(100, "uA")
        assert unit == "A"
        assert val == pytest.approx(0.0001, rel=1e-3)

    # --- Force ---
    def test_kN_to_N(self):
        val, unit = normalize_unit_value(5, "kN")
        assert val == 5000
        assert unit == "N"

    def test_lbf_to_N(self):
        val, unit = normalize_unit_value(10, "lbf")
        assert unit == "N"
        assert val == pytest.approx(44.482, rel=1e-3)

    def test_kgf_to_N(self):
        val, unit = normalize_unit_value(1, "kgf")
        assert unit == "N"
        assert val == pytest.approx(9.80665, rel=1e-4)

    # --- Speed ---
    def test_rad_s_to_rpm(self):
        val, unit = normalize_unit_value(314.159, "rad/s")
        assert unit == "rpm"
        assert val == pytest.approx(3000, rel=1e-2)

    def test_rps_to_rpm(self):
        val, unit = normalize_unit_value(50, "rps")
        assert val == 3000
        assert unit == "rpm"

    # --- Inertia ---
    def test_gcm2_to_kgcm2(self):
        val, unit = normalize_unit_value(500, "g·cm²")
        assert val == 0.5
        assert unit == "kg·cm²"

    def test_kgm2_to_kgcm2(self):
        val, unit = normalize_unit_value(0.001, "kg·m²")
        assert val == 10
        assert unit == "kg·cm²"

    def test_oz_in2_to_kgcm2(self):
        val, unit = normalize_unit_value(10, "oz-in²")
        assert unit == "kg·cm²"
        assert val == pytest.approx(0.720078, rel=1e-3)

    # --- Inductance ---
    def test_H_to_mH(self):
        val, unit = normalize_unit_value(0.5, "H")
        assert val == 500
        assert unit == "mH"

    def test_uH_to_mH(self):
        val, unit = normalize_unit_value(100, "uH")
        assert val == 0.1
        assert unit == "mH"

    # --- Resistance ---
    def test_ohm_text_to_symbol(self):
        val, unit = normalize_unit_value(10, "ohm")
        assert val == 10
        assert unit == "Ω"

    def test_ohms_text_to_symbol(self):
        val, unit = normalize_unit_value(4.7, "Ohms")
        assert val == 4.7
        assert unit == "Ω"

    def test_mOhm_to_Ohm(self):
        val, unit = normalize_unit_value(500, "mΩ")
        assert val == 0.5
        assert unit == "Ω"

    def test_kOhm_to_Ohm(self):
        val, unit = normalize_unit_value(10, "kΩ")
        assert val == 10000
        assert unit == "Ω"

    # --- Temperature ---
    def test_fahrenheit_to_celsius(self):
        val, unit = normalize_unit_value(212, "°F")
        assert val == 100
        assert unit == "°C"

    def test_fahrenheit_negative(self):
        val, unit = normalize_unit_value(-40, "°F")
        assert val == -40
        assert unit == "°C"

    # --- Passthrough ---
    def test_canonical_unit_passes_through(self):
        val, unit = normalize_unit_value(3000, "rpm")
        assert val == 3000
        assert unit == "rpm"

    def test_unknown_unit_passes_through(self):
        val, unit = normalize_unit_value(100, "widgets")
        assert val == 100
        assert unit == "widgets"

    def test_length_units_not_converted(self):
        """Length units are intentionally excluded from conversion."""
        val, unit = normalize_unit_value(4.5, "m")
        assert val == 4.5
        assert unit == "m"

    def test_inches_not_converted(self):
        val, unit = normalize_unit_value(12, "in")
        assert val == 12
        assert unit == "in"

    def test_kHz_not_converted(self):
        """Frequency units are intentionally excluded from conversion."""
        val, unit = normalize_unit_value(8, "kHz")
        assert val == 8
        assert unit == "kHz"


@pytest.mark.unit
class TestPydanticIntegration:
    """Test that unit conversion works through the Pydantic model layer."""

    def test_motor_torque_mNm_normalized(self):
        from specodex.models.motor import Motor

        motor = Motor(
            product_name="Test",
            manufacturer="Test",
            rated_torque={"value": 500, "unit": "mNm"},
        )
        assert motor.rated_torque == ValueUnit(value=0.5, unit="Nm")

    def test_motor_torque_Nm_unchanged(self):
        from specodex.models.motor import Motor

        motor = Motor(
            product_name="Test",
            manufacturer="Test",
            rated_torque={"value": 2.5, "unit": "Nm"},
        )
        assert motor.rated_torque == ValueUnit(value=2.5, unit="Nm")

    def test_motor_power_kW_normalized(self):
        from specodex.models.motor import Motor

        motor = Motor(
            product_name="Test",
            manufacturer="Test",
            rated_power={"value": 1.5, "unit": "kW"},
        )
        assert motor.rated_power == ValueUnit(value=1500, unit="W")

    def test_motor_current_mA_normalized(self):
        from specodex.models.motor import Motor

        motor = Motor(
            product_name="Test",
            manufacturer="Test",
            rated_current={"value": 500, "unit": "mA"},
        )
        assert motor.rated_current == ValueUnit(value=0.5, unit="A")

    def test_motor_resistance_ohm_normalized(self):
        from specodex.models.motor import Motor

        motor = Motor(
            product_name="Test",
            manufacturer="Test",
            resistance={"value": 4.7, "unit": "ohm"},
        )
        assert motor.resistance == ValueUnit(value=4.7, unit="Ω")

    def test_motor_inductance_uH_normalized(self):
        from specodex.models.motor import Motor

        motor = Motor(
            product_name="Test",
            manufacturer="Test",
            inductance={"value": 100, "unit": "uH"},
        )
        assert motor.inductance == ValueUnit(value=0.1, unit="mH")

    def test_motor_inertia_gcm2_normalized(self):
        from specodex.models.motor import Motor

        motor = Motor(
            product_name="Test",
            manufacturer="Test",
            rotor_inertia={"value": 500, "unit": "g·cm²"},
        )
        assert motor.rotor_inertia == ValueUnit(value=0.5, unit="kg·cm²")

    def test_motor_inertia_scientific_notation_preserved(self):
        """The semicolon-canary case from the original bug report: 5.5e-5 must
        round-trip cleanly with no string fallback."""
        from specodex.models.motor import Motor

        motor = Motor(
            product_name="Test",
            manufacturer="Test",
            rotor_inertia={"value": 5.5e-5, "unit": "kg·cm²"},
        )
        assert motor.rotor_inertia.value == 5.5e-5
        assert motor.rotor_inertia.unit == "kg·cm²"
        # And the dump preserves it as a number, not a stringified scientific
        # notation literal.
        dumped = motor.model_dump(exclude_none=True)
        assert dumped["rotor_inertia"] == {"value": 5.5e-5, "unit": "kg·cm²"}

    def test_drive_current_mA_normalized(self):
        from specodex.models.drive import Drive

        drive = Drive(
            product_name="Test",
            manufacturer="Test",
            rated_current={"value": 500, "unit": "mA"},
        )
        assert drive.rated_current == ValueUnit(value=0.5, unit="A")

    def test_drive_power_kW_normalized(self):
        from specodex.models.drive import Drive

        drive = Drive(
            product_name="Test",
            manufacturer="Test",
            rated_power={"value": 2, "unit": "kW"},
        )
        assert drive.rated_power == ValueUnit(value=2000, unit="W")

    def test_gearhead_torque_oz_in_normalized(self):
        from specodex.models.gearhead import Gearhead

        gh = Gearhead(
            product_name="Test",
            manufacturer="Test",
            product_type="gearhead",
            max_continuous_torque={"value": 100, "unit": "oz-in"},
        )
        assert gh.max_continuous_torque.unit == "Nm"
        assert gh.max_continuous_torque.value == pytest.approx(0.706155, rel=1e-3)

    def test_gearhead_force_kN_normalized(self):
        from specodex.models.gearhead import Gearhead

        gh = Gearhead(
            product_name="Test",
            manufacturer="Test",
            product_type="gearhead",
            max_radial_load={"value": 5, "unit": "kN"},
        )
        assert gh.max_radial_load == ValueUnit(value=5000, unit="N")

    def test_legacy_compact_string_input(self):
        """Backwards compat: existing data with compact strings still loads."""
        from specodex.models.motor import Motor

        motor = Motor(product_name="Test", manufacturer="Test", rated_torque="500;mNm")
        assert motor.rated_torque == ValueUnit(value=0.5, unit="Nm")

    def test_space_separated_input_with_conversion(self):
        from specodex.models.motor import Motor

        motor = Motor(product_name="Test", manufacturer="Test", rated_torque="500 mNm")
        assert motor.rated_torque == ValueUnit(value=0.5, unit="Nm")

    def test_none_still_works(self):
        from specodex.models.motor import Motor

        motor = Motor(product_name="Test", manufacturer="Test")
        assert motor.rated_torque is None
