"""Unit tests for the ElectricCylinder model."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.electric_cylinder import ElectricCylinder

DETERMINISTIC_UUID = UUID("12345678-1234-1234-1234-123456789012")
MFG = "TestMfg"


@pytest.mark.unit
class TestElectricCylinderCreation:
    def test_minimal_creation(self):
        ec = ElectricCylinder(product_name="22L ML", manufacturer="Faulhaber")
        assert ec.product_type == "electric_cylinder"
        assert ec.product_name == "22L ML"
        assert ec.PK == "PRODUCT#ELECTRIC_CYLINDER"

    def test_full_creation(self):
        ec = ElectricCylinder(
            product_id=DETERMINISTIC_UUID,
            product_name="22L ML",
            manufacturer="Faulhaber",
            series="L-Series",
            type="linear actuator",
            stroke="20;mm",
            max_push_force="80;N",
            max_pull_force="50;N",
            continuous_force="30;N",
            max_linear_speed="65;mm/s",
            positioning_repeatability="0.05;mm",
            rated_voltage="12-24;V",
            rated_current="0.8;A",
            peak_current="2.5;A",
            rated_power="15;W",
            motor_type="brushless dc",
            lead_screw_pitch="0.5;mm/rev",
            backlash="0.1;mm",
            max_radial_load="30;N",
            max_axial_load="100;N",
            encoder_feedback_support="Incremental 512 ppr",
            fieldbus="CANopen",
            ip_rating="IP54",
            operating_temp="-10-60;°C",
            service_life="5000;hours",
            noise_level="45;dBA",
        )
        assert ec.product_id == DETERMINISTIC_UUID
        assert ec.stroke == ValueUnit(value=20, unit="mm")
        assert ec.max_push_force == ValueUnit(value=80, unit="N")
        assert ec.max_pull_force == ValueUnit(value=50, unit="N")
        assert ec.continuous_force == ValueUnit(value=30, unit="N")
        assert ec.rated_voltage == MinMaxUnit(min=12, max=24, unit="V")
        assert ec.motor_type == "brushless dc"
        assert ec.SK == f"PRODUCT#{DETERMINISTIC_UUID}"

    def test_product_type_locked(self):
        ec = ElectricCylinder(product_name="Test", manufacturer=MFG)
        assert ec.product_type == "electric_cylinder"

    def test_wrong_product_type_rejected(self):
        with pytest.raises(ValidationError):
            ElectricCylinder(
                product_name="Test", manufacturer=MFG, product_type="motor"
            )


@pytest.mark.unit
class TestElectricCylinderForceSpecs:
    """Force specs use N (Newtons), not Nm — core distinction from motors."""

    def test_force_in_newtons(self):
        ec = ElectricCylinder(
            product_name="Test",
            manufacturer=MFG,
            max_push_force="120;N",
            max_pull_force="80;N",
            continuous_force="45;N",
        )
        assert ec.max_push_force == ValueUnit(value=120, unit="N")
        assert ec.max_pull_force == ValueUnit(value=80, unit="N")
        assert ec.continuous_force == ValueUnit(value=45, unit="N")

    def test_force_unit_conversion_kn(self):
        """kN should normalize to N via unit conversion."""
        ec = ElectricCylinder(
            product_name="Test", manufacturer=MFG, max_push_force="0.5;kN"
        )
        assert ec.max_push_force == ValueUnit(value=500, unit="N")

    def test_force_unit_conversion_lbf(self):
        ec = ElectricCylinder(
            product_name="Test", manufacturer=MFG, max_push_force="10;lbf"
        )
        assert ec.max_push_force == ValueUnit(value=44.4822, unit="N")

    def test_stroke_field(self):
        ec = ElectricCylinder(product_name="Test", manufacturer=MFG, stroke="50;mm")
        assert ec.stroke == ValueUnit(value=50, unit="mm")


@pytest.mark.unit
class TestElectricCylinderMotorSpecs:
    """Integrated motor specs behave like Motor model fields."""

    def test_voltage_range(self):
        ec = ElectricCylinder(
            product_name="Test", manufacturer=MFG, rated_voltage="12-48;V"
        )
        assert ec.rated_voltage == MinMaxUnit(min=12, max=48, unit="V")

    def test_voltage_dict_input(self):
        ec = ElectricCylinder(
            product_name="Test",
            manufacturer=MFG,
            rated_voltage={"min": "12", "max": "48", "unit": "V"},
        )
        assert ec.rated_voltage == MinMaxUnit(min=12, max=48, unit="V")

    def test_current_fields(self):
        ec = ElectricCylinder(
            product_name="Test",
            manufacturer=MFG,
            rated_current="1.2;A",
            peak_current="3.5;A",
        )
        assert ec.rated_current == ValueUnit(value=1.2, unit="A")
        assert ec.peak_current == ValueUnit(value=3.5, unit="A")

    def test_power_conversion(self):
        ec = ElectricCylinder(
            product_name="Test", manufacturer=MFG, rated_power="0.5;kW"
        )
        assert ec.rated_power == ValueUnit(value=500, unit="W")


@pytest.mark.unit
class TestElectricCylinderMechanical:
    def test_lead_screw_pitch(self):
        ec = ElectricCylinder(
            product_name="Test", manufacturer=MFG, lead_screw_pitch="1.0;mm/rev"
        )
        assert ec.lead_screw_pitch == ValueUnit(value=1.0, unit="mm/rev")

    def test_backlash(self):
        ec = ElectricCylinder(product_name="Test", manufacturer=MFG, backlash="0.08;mm")
        assert ec.backlash == ValueUnit(value=0.08, unit="mm")


@pytest.mark.unit
class TestElectricCylinderDefaults:
    def test_all_optional_fields_none(self):
        ec = ElectricCylinder(product_name="Test", manufacturer=MFG)
        assert ec.type is None
        assert ec.series is None
        assert ec.stroke is None
        assert ec.max_push_force is None
        assert ec.max_pull_force is None
        assert ec.continuous_force is None
        assert ec.max_linear_speed is None
        assert ec.positioning_repeatability is None
        assert ec.rated_voltage is None
        assert ec.rated_current is None
        assert ec.peak_current is None
        assert ec.rated_power is None
        assert ec.motor_type is None
        assert ec.lead_screw_pitch is None
        assert ec.backlash is None
        assert ec.max_radial_load is None
        assert ec.max_axial_load is None
        assert ec.encoder_feedback_support is None
        assert ec.fieldbus is None
        assert ec.ip_rating is None
        assert ec.operating_temp is None
        assert ec.service_life is None
        assert ec.noise_level is None


@pytest.mark.unit
class TestElectricCylinderTypeLiteral:
    def test_valid_types(self):
        for t in [
            "linear actuator",
            "linear servo",
            "micro linear actuator",
            "tubular linear motor",
        ]:
            ec = ElectricCylinder(product_name="Test", manufacturer=MFG, type=t)
            assert ec.type == t

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            ElectricCylinder(product_name="Test", manufacturer=MFG, type="brushless dc")
