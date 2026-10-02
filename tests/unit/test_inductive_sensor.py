"""Unit tests for the InductiveSensor model."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from specodex.config import SCHEMA_CHOICES
from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.inductive_sensor import InductiveSensor
from specodex.models.llm_schema import to_gemini_schema
from specodex.quality import DEFAULT_MIN_QUALITY, score_product

DETERMINISTIC_UUID = UUID("12345678-1234-1234-1234-123456789012")
MFG = "TestMfg"


@pytest.mark.unit
class TestInductiveSensorCreation:
    def test_minimal_creation(self):
        s = InductiveSensor(product_name="IMB18", manufacturer="SICK")
        assert s.product_type == "inductive_sensor"
        assert s.PK == "PRODUCT#INDUCTIVE_SENSOR"

    def test_full_creation(self):
        s = InductiveSensor(
            product_id=DETERMINISTIC_UUID,
            product_name="IMB18-08BPOVC0K",
            manufacturer="SICK",
            part_number="IMB18-08BPOVC0K",
            type="cylindrical_threaded",
            series="IMB",
            housing_size="M18",
            housing_length="69;mm",
            housing_material="Stainless steel V4A",
            sensing_face_material="LCP",
            sensing_distance="8;mm",
            assured_sensing_distance="6.48;mm",
            mounting="flush",
            switching_frequency="1000;Hz",
            hysteresis="15;%",
            output_type="pnp",
            output_function="nc",
            wiring="dc_3_wire",
            io_link=False,
            supply_voltage="10-30;V",
            max_load_current="200;mA",
            no_load_current="10;mA",
            voltage_drop="2;V",
            short_circuit_protection=True,
            reverse_polarity_protection=True,
            connection="connector_m12",
            connector_pins=4,
            operating_temp="-40-100;°C",
            ip_rating="IP68",
            protection_ratings=["IP68", "IP69K"],
            tightening_torque="40;Nm",
        )
        assert s.SK == f"PRODUCT#{DETERMINISTIC_UUID}"
        assert s.sensing_distance == ValueUnit(value=8, unit="mm")
        assert s.assured_sensing_distance == ValueUnit(value=6.48, unit="mm")
        assert s.supply_voltage == MinMaxUnit(min=10, max=30, unit="V")
        assert s.operating_temp == MinMaxUnit(min=-40, max=100, unit="°C")
        assert s.ip_rating == 68
        assert s.protection_ratings == ["IP68", "IP69K"]

    def test_wrong_product_type_rejected(self):
        with pytest.raises(ValidationError):
            InductiveSensor(product_name="x", manufacturer=MFG, product_type="motor")

    def test_registered_for_extraction(self):
        assert SCHEMA_CHOICES["inductive_sensor"] is InductiveSensor


@pytest.mark.unit
class TestInductiveSensorUnits:
    def test_milliamp_currents_normalise_to_amps(self):
        """Every vendor prints load / leakage current in mA."""
        s = InductiveSensor(
            product_name="x",
            manufacturer=MFG,
            max_load_current={"value": 200, "unit": "mA"},
            min_load_current={"value": 3, "unit": "mA"},
            leakage_current={"value": 0.8, "unit": "mA"},
        )
        assert s.max_load_current == ValueUnit(value=0.2, unit="A")
        assert s.min_load_current == ValueUnit(value=0.003, unit="A")
        assert s.leakage_current == ValueUnit(value=0.0008, unit="A")

    def test_wrong_family_unit_drops_field_not_row(self):
        s = InductiveSensor(
            product_name="x",
            manufacturer=MFG,
            sensing_distance={"value": 8, "unit": "V"},
            switching_frequency={"value": 5, "unit": "mm"},
            supply_voltage={"min": 10, "max": 30, "unit": "mA"},
        )
        assert s.sensing_distance is None
        assert s.switching_frequency is None
        assert s.supply_voltage is None

    @pytest.mark.parametrize("raw", ["IP69K", "67/68", {"unit": "IP"}, True])
    def test_non_numeric_ip_rating_is_none(self, raw):
        """Lettered ratings live in protection_ratings, never kill the row."""
        s = InductiveSensor(product_name="x", manufacturer=MFG, ip_rating=raw)
        assert s.ip_rating is None


@pytest.mark.unit
class TestInductiveSensorEnums:
    """Vendor synonyms must be mapped by the extractor, not smuggled in."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("mounting", "shielded"),
            ("mounting", "embeddable"),
            ("output_type", "PNP"),
            ("output_function", "NO"),
            ("wiring", "3-wire"),
            ("connection", "M12"),
            ("type", "barrel"),
        ],
    )
    def test_off_vocabulary_value_rejected(self, field, value):
        with pytest.raises(ValidationError):
            InductiveSensor(product_name="x", manufacturer=MFG, **{field: value})

    def test_gemini_schema_carries_closed_enums(self):
        props = to_gemini_schema(InductiveSensor, as_array=False)["properties"]
        assert set(props["mounting"]["enum"]) == {"flush", "non_flush", "quasi_flush"}
        assert "pnp" in props["output_type"]["enum"]
        assert "product_type" not in props


@pytest.mark.unit
class TestInductiveSensorQuality:
    def test_selection_guide_row_passes_default_gate(self):
        """A thin selection-table row (ifm / Balluff style: size, Sn,
        mounting, output, connection, part number) must clear the gate —
        otherwise the brochures that list the most parts ingest nothing."""
        s = InductiveSensor(
            product_name="BES Global",
            manufacturer="Balluff",
            part_number="BES0038",
            type="cylindrical_threaded",
            housing_size="M8",
            sensing_distance="1.5;mm",
            assured_sensing_distance="1.2;mm",
            mounting="flush",
            output_type="pnp",
            output_function="no",
            wiring="dc_3_wire",
            connection="cable",
            cable_length="3;m",
        )
        score, *_ = score_product(s)
        assert score >= DEFAULT_MIN_QUALITY

    def test_bare_part_number_fails_default_gate(self):
        s = InductiveSensor(product_name="x", manufacturer=MFG, part_number="BES0038")
        score, *_ = score_product(s)
        assert score < DEFAULT_MIN_QUALITY
