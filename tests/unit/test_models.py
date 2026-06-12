"""Comprehensive unit tests for all Pydantic models in specodex."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.datasheet import Datasheet
from specodex.models.drive import Drive
from specodex.models.gearhead import Gearhead
from specodex.models.manufacturer import Manufacturer
from specodex.models.motor import Motor
from specodex.models.product import Dimensions
from specodex.models.robot_arm import (
    Controller,
    ControllerIO,
    ForceTorqueSensor,
    JointSpecs,
    RobotArm,
    TeachPendant,
    ToolIO,
)

DETERMINISTIC_UUID = UUID("12345678-1234-1234-1234-123456789012")
MFG = "TestMfg"


@pytest.mark.unit
class TestValueUnit:
    def test_valid_value_unit_string(self):
        motor = Motor(product_name="Test", manufacturer=MFG, rated_speed="3000;rpm")
        assert motor.rated_speed == ValueUnit(value=3000, unit="rpm")

    def test_dict_input_conversion(self):
        motor = Motor(
            product_name="Test",
            manufacturer=MFG,
            rated_speed={"value": "3000", "unit": "rpm"},
        )
        assert motor.rated_speed == ValueUnit(value=3000, unit="rpm")

    def test_space_separated_input(self):
        motor = Motor(product_name="Test", manufacturer=MFG, rated_speed="3000 rpm")
        assert motor.rated_speed == ValueUnit(value=3000, unit="rpm")

    def test_strip_special_chars(self):
        motor = Motor(product_name="Test", manufacturer=MFG, rated_speed="+3000;rpm")
        assert motor.rated_speed == ValueUnit(value=3000, unit="rpm")

    def test_strip_special_chars_dict(self):
        motor = Motor(
            product_name="Test",
            manufacturer=MFG,
            rated_speed={"value": "~3000", "unit": "rpm"},
        )
        assert motor.rated_speed == ValueUnit(value=3000, unit="rpm")

    def test_strip_special_chars_space_separated(self):
        motor = Motor(product_name="Test", manufacturer=MFG, rated_speed=">3000 rpm")
        assert motor.rated_speed == ValueUnit(value=3000, unit="rpm")

    def test_missing_value_part_drops_to_none(self):
        # The typed alias coercer drops unparseable inputs to None at the
        # field level instead of raising — keeps a single bad value from
        # killing the whole extraction.
        m = Motor(product_name="Test", manufacturer=MFG, rated_speed=";rpm")
        assert m.rated_speed is None

    def test_missing_unit_part_drops_to_none(self):
        m = Motor(product_name="Test", manufacturer=MFG, rated_speed="3000;")
        assert m.rated_speed is None

    def test_none_passthrough(self):
        motor = Motor(product_name="Test", manufacturer=MFG)
        assert motor.rated_speed is None


@pytest.mark.unit
class TestMinMaxUnit:
    def test_valid_range_string(self):
        motor = Motor(product_name="Test", manufacturer=MFG, rated_voltage="200-240;V")
        assert motor.rated_voltage == MinMaxUnit(min=200, max=240, unit="V")

    def test_dict_input_min_max(self):
        motor = Motor(
            product_name="Test",
            manufacturer=MFG,
            rated_voltage={"min": "200", "max": "240", "unit": "V"},
        )
        assert motor.rated_voltage == MinMaxUnit(min=200, max=240, unit="V")

    def test_dict_input_min_only(self):
        motor = Motor(
            product_name="Test",
            manufacturer=MFG,
            rated_voltage={"min": "200", "unit": "V"},
        )
        assert motor.rated_voltage == MinMaxUnit(min=200, max=None, unit="V")

    def test_dict_input_max_only(self):
        motor = Motor(
            product_name="Test",
            manufacturer=MFG,
            rated_voltage={"max": "240", "unit": "V"},
        )
        assert motor.rated_voltage == MinMaxUnit(min=None, max=240, unit="V")

    def test_to_separator_replaced(self):
        motor = Motor(
            product_name="Test", manufacturer=MFG, rated_voltage="200 to 240;V"
        )
        assert motor.rated_voltage == MinMaxUnit(min=200, max=240, unit="V")

    def test_empty_range_rejected(self):
        # Empty value-part on the range string returns None at the field level
        # (typed alias drops it as unparseable) rather than raising.
        m = Motor(product_name="Test", manufacturer=MFG, rated_voltage=";V")
        assert m.rated_voltage is None

    def test_empty_unit_rejected(self):
        m = Motor(product_name="Test", manufacturer=MFG, rated_voltage="200-240;")
        assert m.rated_voltage is None


@pytest.mark.unit
class TestDimensions:
    def test_dimensions_defaults(self):
        dims = Dimensions()
        assert dims.unit == "mm"
        assert dims.width is None
        assert dims.length is None
        assert dims.height is None

    def test_dimensions_custom(self):
        dims = Dimensions(width=100.0, height=50.0, unit="in")
        assert dims.width == 100.0
        assert dims.height == 50.0
        assert dims.unit == "in"
        assert dims.length is None


@pytest.mark.unit
class TestProductBase:
    def test_auto_uuid_generation(self):
        motor = Motor(product_name="Test", manufacturer=MFG)
        assert motor.product_id is not None
        assert isinstance(motor.product_id, UUID)

    def test_computed_pk(self):
        motor = Motor(product_name="Test", manufacturer=MFG)
        assert motor.PK == "PRODUCT#MOTOR"

    def test_computed_sk(self):
        motor = Motor(
            product_name="Test", manufacturer=MFG, product_id=DETERMINISTIC_UUID
        )
        assert motor.SK == f"PRODUCT#{DETERMINISTIC_UUID}"

    def test_required_fields(self):
        # product_name and manufacturer are required
        with pytest.raises(ValidationError):
            Motor()

    def test_optional_fields_default_none(self):
        motor = Motor(product_name="Test", manufacturer=MFG)
        assert motor.manufacturer == MFG
        assert motor.part_number is None
        assert motor.product_family is None
        assert motor.release_year is None
        assert motor.dimensions is None
        assert motor.weight is None
        assert motor.msrp is None
        assert motor.warranty is None
        assert motor.datasheet_url is None
        assert motor.pages is None


@pytest.mark.unit
class TestMotor:
    def test_motor_creation_minimal(self):
        motor = Motor(product_type="motor", product_name="Test", manufacturer=MFG)
        assert motor.product_type == "motor"
        assert motor.product_name == "Test"

    def test_motor_creation_full(self):
        motor = Motor(
            product_id=DETERMINISTIC_UUID,
            product_name="ECMA-C30804",
            product_type="motor",
            manufacturer="Delta Electronics",
            part_number="ECMA-C30804E7",
            series="ECMA",
            type="ac servo",
            rated_voltage="200-240;V",
            rated_speed="3000;rpm",
            max_speed="5000;rpm",
            rated_torque="2.5;Nm",
            peak_torque="7.5;Nm",
            rated_power="400;W",
            rated_current="2.6;A",
            peak_current="7.8;A",
            poles=8,
            ip_rating=65,
            encoder_feedback_support="Incremental 2500 ppr",
        )
        assert motor.product_id == DETERMINISTIC_UUID
        assert motor.manufacturer == "Delta Electronics"
        assert motor.rated_speed == ValueUnit(value=3000, unit="rpm")
        assert motor.rated_voltage == MinMaxUnit(min=200, max=240, unit="V")
        assert motor.poles == 8
        assert motor.PK == "PRODUCT#MOTOR"

    def test_motor_product_type_literal(self):
        motor = Motor(product_name="Test", manufacturer=MFG, product_type="motor")
        assert motor.product_type == "motor"

    def test_motor_invalid_type_literal(self):
        with pytest.raises(ValidationError):
            Motor(product_name="Test", manufacturer=MFG, product_type="drive")

    def test_motor_value_unit_fields(self):
        motor = Motor(
            product_name="Test",
            manufacturer=MFG,
            rated_speed="3000;rpm",
            rated_torque="2.5;Nm",
            rated_power="400;W",
        )
        assert motor.rated_speed == ValueUnit(value=3000, unit="rpm")
        assert motor.rated_torque == ValueUnit(value=2.5, unit="Nm")
        assert motor.rated_power == ValueUnit(value=400, unit="W")


@pytest.mark.unit
class TestDrive:
    def test_drive_creation(self):
        drive = Drive(
            product_name="ASD-B3",
            manufacturer=MFG,
            fieldbus=["EtherCAT", "PROFINET"],
        )
        assert drive.product_type == "drive"
        assert drive.fieldbus == ["EtherCAT", "PROFINET"]

    def test_drive_list_fields(self):
        drive = Drive(
            product_name="ASD-B3",
            manufacturer=MFG,
            input_voltage_frequency=["50-60;Hz", "50;Hz"],
            switching_frequency=["8;kHz", "16;kHz"],
        )
        assert drive.input_voltage_frequency == [
            MinMaxUnit(min=50, max=60, unit="Hz"),
            MinMaxUnit(min=50, max=None, unit="Hz"),
        ]
        assert drive.switching_frequency == [
            ValueUnit(value=8, unit="kHz"),
            ValueUnit(value=16, unit="kHz"),
        ]

    def test_drive_type_literal(self):
        drive = Drive(product_name="Test", manufacturer=MFG, product_type="drive")
        assert drive.product_type == "drive"

    def test_drive_invalid_type_literal(self):
        with pytest.raises(ValidationError):
            Drive(product_name="Test", manufacturer=MFG, product_type="motor")

    def test_drive_fieldbus_modbus_rtu_and_canopen(self):
        # Regression (2026-06-12): a live Bardac P2 row carried
        # ["ModbusRTU", "CANopen"] and was unreadable because the
        # CommunicationProtocol literal lacked both protocols. They're
        # real and common on VFD-class drives — added to the literal
        # (canonical spellings; the DB row was rewritten to match).
        drive = Drive(
            product_name="P2 AC drive",
            manufacturer=MFG,
            fieldbus=["Modbus RTU", "CANopen"],
        )
        assert drive.fieldbus == ["Modbus RTU", "CANopen"]

    def test_drive_fieldbus_non_canonical_spelling_still_rejected(self):
        # "ModbusRTU" (no space) stays invalid on purpose — the fix is
        # canonical enum values plus a data rewrite, not a spelling
        # free-for-all. Gemini is constrained by response_schema, so
        # only canonical spellings can enter going forward.
        with pytest.raises(ValidationError):
            Drive(product_name="Test", manufacturer=MFG, fieldbus=["ModbusRTU"])


@pytest.mark.unit
class TestGearhead:
    def test_gearhead_creation(self):
        gh = Gearhead(
            product_name="PHL-060",
            manufacturer=MFG,
            product_type="gearhead",
            gear_ratio=10.0,
            stages=1,
        )
        assert gh.product_type == "gearhead"
        assert gh.gear_ratio == 10.0
        assert gh.stages == 1

    def test_efficiency_valid(self):
        gh = Gearhead(
            product_name="PHL-060",
            manufacturer=MFG,
            product_type="gearhead",
            efficiency=0.97,
        )
        assert gh.efficiency == 0.97

    def test_efficiency_upper_bound(self):
        with pytest.raises(ValidationError):
            Gearhead(
                product_name="PHL-060",
                manufacturer=MFG,
                product_type="gearhead",
                efficiency=1.5,
            )

    def test_efficiency_lower_bound(self):
        with pytest.raises(ValidationError):
            Gearhead(
                product_name="PHL-060",
                manufacturer=MFG,
                product_type="gearhead",
                efficiency=-0.1,
            )

    def test_efficiency_boundary_values(self):
        gh_zero = Gearhead(
            product_name="Test",
            manufacturer=MFG,
            product_type="gearhead",
            efficiency=0.0,
        )
        assert gh_zero.efficiency == 0.0

        gh_one = Gearhead(
            product_name="Test",
            manufacturer=MFG,
            product_type="gearhead",
            efficiency=1.0,
        )
        assert gh_one.efficiency == 1.0


@pytest.mark.unit
class TestDatasheet:
    def test_datasheet_creation(self):
        ds = Datasheet(
            datasheet_id=DETERMINISTIC_UUID,
            url="https://example.com/datasheet.pdf",
            product_type="motor",
            product_name="ECMA-C30804",
            manufacturer=MFG,
        )
        assert ds.url == "https://example.com/datasheet.pdf"
        assert ds.product_type == "motor"
        assert ds.product_name == "ECMA-C30804"

    def test_datasheet_computed_pk_sk(self):
        ds = Datasheet(
            datasheet_id=DETERMINISTIC_UUID,
            url="https://example.com/datasheet.pdf",
            product_type="motor",
            product_name="Test",
            manufacturer=MFG,
        )
        assert ds.PK == "DATASHEET#MOTOR"
        assert ds.SK == f"DATASHEET#{DETERMINISTIC_UUID}"

    def test_datasheet_required_url(self):
        with pytest.raises(ValidationError):
            Datasheet(product_type="motor", product_name="Test", manufacturer=MFG)

    def test_datasheet_auto_uuid(self):
        ds = Datasheet(
            url="https://example.com/datasheet.pdf",
            product_type="motor",
            product_name="Test",
            manufacturer=MFG,
        )
        assert isinstance(ds.datasheet_id, UUID)

    def test_datasheet_optional_fields_default(self):
        ds = Datasheet(
            url="https://example.com/datasheet.pdf",
            product_type="motor",
            product_name="Test",
            manufacturer=MFG,
        )
        assert ds.pages is None
        assert ds.product_family is None
        assert ds.manufacturer == MFG
        assert ds.category is None
        assert ds.release_year is None
        assert ds.warranty is None


@pytest.mark.unit
class TestManufacturer:
    def test_manufacturer_creation(self):
        mfg = Manufacturer(
            id=DETERMINISTIC_UUID,
            name="Delta Electronics",
            website="https://www.delta.com",
        )
        assert mfg.name == "Delta Electronics"
        assert mfg.website == "https://www.delta.com"

    def test_manufacturer_computed_pk_sk(self):
        mfg = Manufacturer(id=DETERMINISTIC_UUID, name="Delta Electronics")
        assert mfg.PK == "MANUFACTURER"
        assert mfg.SK == f"MANUFACTURER#{DETERMINISTIC_UUID}"

    def test_manufacturer_offered_types(self):
        mfg = Manufacturer(
            name="Delta Electronics",
            offered_product_types=["motor", "drive", "gearhead"],
        )
        assert mfg.offered_product_types == ["motor", "drive", "gearhead"]

    def test_manufacturer_auto_uuid(self):
        mfg = Manufacturer(name="Test Manufacturer")
        assert isinstance(mfg.id, UUID)

    def test_manufacturer_optional_defaults(self):
        mfg = Manufacturer(name="Test Manufacturer")
        assert mfg.website is None
        assert mfg.offered_product_types == []


@pytest.mark.unit
class TestRobotArm:
    def test_robot_arm_creation(self):
        arm = RobotArm(
            product_name="UR5e",
            payload="5;kg",
            reach="850;mm",
            joints=[
                JointSpecs(
                    joint_name="Base",
                    working_range="360;deg",
                    max_speed="180;deg/s",
                ),
            ],
            force_torque_sensor=ForceTorqueSensor(
                force_range="50;N",
                torque_range="10;Nm",
            ),
        )
        assert arm.product_type == "robot_arm"
        assert arm.payload == ValueUnit(value=5, unit="kg")
        assert arm.reach == ValueUnit(value=850, unit="mm")
        assert len(arm.joints) == 1
        assert arm.joints[0].joint_name == "Base"

    def test_joint_specs(self):
        joint = JointSpecs(
            joint_name="Wrist 1",
            working_range="360;deg",
            max_speed="180;deg/s",
        )
        assert joint.joint_name == "Wrist 1"
        assert joint.working_range == ValueUnit(value=360, unit="deg")
        assert joint.max_speed == ValueUnit(value=180, unit="deg/s")

    def test_joint_specs_value_unit_dict(self):
        joint = JointSpecs(
            joint_name="Base",
            working_range={"value": "360", "unit": "deg"},
        )
        assert joint.working_range == ValueUnit(value=360, unit="deg")

    def test_force_torque_sensor(self):
        fts = ForceTorqueSensor(
            force_range="50;N",
            force_precision="3.5;N",
            torque_range="10;Nm",
            torque_precision="0.1;Nm",
        )
        # Force/Torque are typed aliases; ForceTorqueSensor still uses Force/
        # Torque (unchanged). They construct ValueUnit instances under the
        # hood now.
        assert fts.force_range == ValueUnit(value=50, unit="N")
        assert fts.force_precision == ValueUnit(value=3.5, unit="N")
        assert fts.torque_range == ValueUnit(value=10, unit="Nm")
        assert fts.torque_precision == ValueUnit(value=0.1, unit="Nm")

    def test_force_torque_sensor_defaults(self):
        fts = ForceTorqueSensor()
        assert fts.force_range is None
        assert fts.torque_range is None

    def test_robot_arm_defaults(self):
        arm = RobotArm(product_name="UR5e")
        assert arm.manufacturer == "Universal Robots"
        assert arm.product_family == "e-Series"
        assert arm.degrees_of_freedom == 6
        assert arm.ip_rating == 54
        assert arm.operating_temp == MinMaxUnit(min=0, max=50, unit="°C")

    def test_robot_arm_nested_controller(self):
        arm = RobotArm(
            product_name="UR5e",
            controller=Controller(
                io_ports=ControllerIO(),
                power_source="100-240;VAC",
            ),
        )
        assert arm.controller.ip_rating == 44
        assert arm.controller.io_ports.digital_in == 16
        assert arm.controller.io_ports.digital_out == 16

    def test_teach_pendant_defaults(self):
        tp = TeachPendant()
        assert tp.display_size == ValueUnit(value=12, unit="in")
        assert tp.weight == ValueUnit(value=1.6, unit="kg")
        assert tp.cable_length == ValueUnit(value=4.5, unit="m")
        assert tp.ip_rating == 54

    def test_tool_io_creation(self):
        tio = ToolIO(digital_in=2, digital_out=2, analog_in=2)
        assert tio.digital_in == 2
        assert tio.digital_out == 2
        assert tio.analog_in == 2
