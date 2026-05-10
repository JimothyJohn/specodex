"""Unit tests for specodex.models.llm_schema (Pydantic → Gemini)."""

from __future__ import annotations

import pytest

from specodex.models.drive import Drive
from specodex.models.llm_schema import (
    EXCLUDED_FIELDS,
    to_gemini_schema,
)
from specodex.models.motor import Motor


@pytest.mark.unit
class TestTopLevelShape:
    def test_returns_array_by_default(self) -> None:
        schema = to_gemini_schema(Drive)
        assert schema["type"] == "ARRAY"
        assert "items" in schema
        assert schema["items"]["type"] == "OBJECT"

    def test_as_array_false_returns_plain_object(self) -> None:
        schema = to_gemini_schema(Drive, as_array=False)
        assert schema["type"] == "OBJECT"
        assert "items" not in schema
        assert "properties" in schema


@pytest.mark.unit
class TestExclusion:
    def test_excluded_fields_absent_by_default(self) -> None:
        props = to_gemini_schema(Drive)["items"]["properties"]
        for excluded in EXCLUDED_FIELDS:
            assert excluded not in props, (
                f"excluded field {excluded!r} leaked into schema"
            )

    def test_include_excluded_keeps_them(self) -> None:
        # Nested submodels call to_gemini_schema with include_excluded=True
        # so internal bookkeeping fields show up in Dimensions etc.
        props = to_gemini_schema(Drive, as_array=False, include_excluded=True)[
            "properties"
        ]
        # product_name / manufacturer are required on Drive
        assert "product_name" in props
        assert "manufacturer" in props


@pytest.mark.unit
class TestValueUnitMapping:
    def test_scalar_value_unit_becomes_object(self) -> None:
        props = to_gemini_schema(Drive)["items"]["properties"]
        rc = props["rated_current"]
        assert rc["type"] == "OBJECT"
        assert rc["properties"]["value"]["type"] == "NUMBER"
        assert rc["properties"]["unit"]["type"] == "STRING"

    def test_list_value_unit_becomes_array_of_object(self) -> None:
        props = to_gemini_schema(Drive)["items"]["properties"]
        sf = props["switching_frequency"]
        assert sf["type"] == "ARRAY"
        assert sf["items"]["type"] == "OBJECT"
        assert "value" in sf["items"]["properties"]
        assert "unit" in sf["items"]["properties"]


@pytest.mark.unit
class TestMinMaxUnitMapping:
    def test_scalar_min_max_becomes_object_with_min_max_unit(self) -> None:
        props = to_gemini_schema(Drive)["items"]["properties"]
        iv = props["input_voltage"]
        assert iv["type"] == "OBJECT"
        assert iv["properties"]["min"]["type"] == "NUMBER"
        assert iv["properties"]["max"]["type"] == "NUMBER"
        assert iv["properties"]["unit"]["type"] == "STRING"

    def test_list_min_max_becomes_array(self) -> None:
        props = to_gemini_schema(Drive)["items"]["properties"]
        iv_freq = props["input_voltage_frequency"]
        assert iv_freq["type"] == "ARRAY"
        assert iv_freq["items"]["type"] == "OBJECT"
        assert {"min", "max", "unit"} <= set(iv_freq["items"]["properties"].keys())


@pytest.mark.unit
class TestLiteralMapping:
    def test_scalar_literal_becomes_enum_string(self) -> None:
        # Drive.type = Literal["servo", "variable frequency"]
        props = to_gemini_schema(Drive)["items"]["properties"]
        t = props["type"]
        assert t["type"] == "STRING"
        assert set(t["enum"]) == {"servo", "variable frequency"}

    def test_list_literal_becomes_array_of_enum_string(self) -> None:
        # Drive.fieldbus = Optional[List[CommunicationProtocol]]
        props = to_gemini_schema(Drive)["items"]["properties"]
        fb = props["fieldbus"]
        assert fb["type"] == "ARRAY"
        assert fb["items"]["type"] == "STRING"
        assert "EtherCAT" in fb["items"]["enum"]
        assert "PROFINET" in fb["items"]["enum"]


@pytest.mark.unit
class TestListScalars:
    def test_list_of_encoder_protocol_enum(self) -> None:
        # Drive.encoder_feedback_support = Optional[List[EncoderProtocol]]
        # — was Optional[List[str]] before the DOUBLE_TAP rework.
        props = to_gemini_schema(Drive)["items"]["properties"]
        efs = props["encoder_feedback_support"]
        assert efs["type"] == "ARRAY"
        assert efs["items"]["type"] == "STRING"
        assert "endat_2_2" in efs["items"]["enum"]
        assert "biss_c" in efs["items"]["enum"]

    def test_list_of_int(self) -> None:
        # Drive.input_voltage_phases = Optional[List[int]]
        props = to_gemini_schema(Drive)["items"]["properties"]
        ivp = props["input_voltage_phases"]
        assert ivp["type"] == "ARRAY"
        assert ivp["items"]["type"] == "INTEGER"


@pytest.mark.unit
class TestScalars:
    def test_integer_fields(self) -> None:
        props = to_gemini_schema(Drive)["items"]["properties"]
        assert props["ethernet_ports"]["type"] == "INTEGER"
        assert props["digital_inputs"]["type"] == "INTEGER"
        assert props["ip_rating"]["type"] == "INTEGER"

    def test_float_field(self) -> None:
        props = to_gemini_schema(Drive)["items"]["properties"]
        assert props["max_humidity"]["type"] == "NUMBER"

    def test_plain_string_field(self) -> None:
        props = to_gemini_schema(Drive)["items"]["properties"]
        assert props["series"]["type"] == "STRING"
        assert "enum" not in props["series"]


@pytest.mark.unit
class TestNestedModel:
    def test_dimensions_recursed(self) -> None:
        # Dimensions has width/length/height/unit scalars
        props = to_gemini_schema(Drive)["items"]["properties"]
        dims = props["dimensions"]
        assert dims["type"] == "OBJECT"
        dp = dims["properties"]
        assert dp["width"]["type"] == "NUMBER"
        assert dp["length"]["type"] == "NUMBER"
        assert dp["height"]["type"] == "NUMBER"
        assert dp["unit"]["type"] == "STRING"


@pytest.mark.unit
class TestMotorShape:
    """Sanity-check a different product type to catch model-specific quirks."""

    def test_motor_has_literal_type_enum(self) -> None:
        props = to_gemini_schema(Motor)["items"]["properties"]
        assert "type" in props
        t = props["type"]
        assert t["type"] == "STRING"
        # Motor type enum includes at least 'brushless dc'
        assert "brushless dc" in t["enum"]

    def test_motor_scalar_value_units(self) -> None:
        props = to_gemini_schema(Motor)["items"]["properties"]
        assert props["rated_speed"]["type"] == "OBJECT"
        assert props["rated_torque"]["type"] == "OBJECT"
