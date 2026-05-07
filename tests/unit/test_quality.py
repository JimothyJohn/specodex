"""Tests for product data quality scoring and filtering."""

import pytest

from specodex.quality import (
    DEFAULT_MIN_QUALITY,
    filter_products,
    score_product,
    spec_fields_for_model,
)
from specodex.models.motor import Motor
from specodex.models.drive import Drive
from specodex.models.gearhead import Gearhead

MFG = "TestMfg"


@pytest.mark.unit
class TestSpecFields:
    """Verify that meta fields are excluded from spec field lists."""

    def test_motor_excludes_meta(self):
        fields = spec_fields_for_model(Motor)
        for meta in ("product_id", "product_type", "manufacturer", "PK", "SK"):
            assert meta not in fields

    def test_motor_includes_specs(self):
        fields = spec_fields_for_model(Motor)
        assert "rated_voltage" in fields
        assert "rated_torque" in fields
        assert "rated_power" in fields

    def test_drive_includes_specs(self):
        fields = spec_fields_for_model(Drive)
        assert "input_voltage" in fields
        assert "rated_current" in fields

    def test_gearhead_includes_specs(self):
        fields = spec_fields_for_model(Gearhead)
        assert "gear_ratio" in fields
        assert "efficiency" in fields


@pytest.mark.unit
class TestScoreProduct:
    """Test score_product against motors with varying completeness."""

    def test_empty_motor_scores_low(self):
        motor = Motor(product_name="Empty", product_type="motor", manufacturer=MFG)
        score, filled, total, missing = score_product(motor)
        assert score < DEFAULT_MIN_QUALITY
        assert filled == 0
        assert total > 0
        assert len(missing) == total

    def test_fully_populated_motor_scores_high(self):
        motor = Motor(
            product_name="Full",
            product_type="motor",
            manufacturer=MFG,
            type="brushless dc",
            series="X",
            rated_voltage="200-240;V",
            rated_speed="3000;rpm",
            max_speed="4000;rpm",
            rated_torque="2.5;Nm",
            peak_torque="5;Nm",
            rated_power="750;W",
            encoder_feedback_support="Incremental",
            poles=8,
            rated_current="3;A",
            peak_current="6;A",
            voltage_constant="0.1;V/krpm",
            torque_constant="0.5;Nm/A",
            resistance="1.2;Ω",
            inductance="5;mH",
            ip_rating=65,
            rotor_inertia="0.5;kg·cm²",
            shaft_diameter="14;mm",
            frame_size="60",
            part_number="MTR-001",
            release_year=2024,
            weight="2.5;kg",
        )
        score, filled, total, missing = score_product(motor)
        assert score > 0.8
        assert len(missing) <= 5

    def test_partially_filled_motor(self):
        motor = Motor(
            product_name="Half",
            product_type="motor",
            manufacturer=MFG,
            rated_voltage="200-240;V",
            rated_speed="3000;rpm",
            rated_torque="2.5;Nm",
            rated_power="750;W",
            rated_current="3;A",
            part_number="MTR-002",
        )
        score, filled, total, missing = score_product(motor)
        assert 0.2 < score < 0.8
        assert "rated_voltage" not in missing
        assert "peak_torque" in missing

    def test_score_counts_non_none_correctly(self):
        motor = Motor(
            product_name="Test",
            product_type="motor",
            manufacturer=MFG,
            poles=4,
            ip_rating=67,
        )
        score, filled, total, missing = score_product(motor)
        # poles and ip_rating are filled, plus part_number/release_year etc are in spec fields
        assert filled == 2
        assert "poles" not in missing
        assert "ip_rating" not in missing


@pytest.mark.unit
class TestFilterProducts:
    """Test the filter_products partitioning logic."""

    def test_empty_list_returns_empty(self):
        passed, rejected = filter_products([])
        assert passed == []
        assert rejected == []

    def test_good_products_pass(self):
        motor = Motor(
            product_name="Good",
            product_type="motor",
            manufacturer=MFG,
            rated_voltage="200-240;V",
            rated_speed="3000;rpm",
            rated_torque="2.5;Nm",
            rated_power="750;W",
            rated_current="3;A",
            peak_current="6;A",
            part_number="MTR-003",
        )
        passed, rejected = filter_products([motor])
        assert len(passed) == 1
        assert len(rejected) == 0

    def test_empty_products_rejected(self):
        motor = Motor(product_name="Bad", product_type="motor", manufacturer=MFG)
        passed, rejected = filter_products([motor])
        assert len(passed) == 0
        assert len(rejected) == 1

    def test_mixed_list_partitioned(self):
        good = Motor(
            product_name="Good",
            product_type="motor",
            manufacturer=MFG,
            rated_voltage="200-240;V",
            rated_speed="3000;rpm",
            rated_torque="2.5;Nm",
            rated_power="750;W",
            rated_current="3;A",
            peak_current="6;A",
            part_number="MTR-004",
        )
        bad = Motor(product_name="Bad", product_type="motor", manufacturer=MFG)
        passed, rejected = filter_products([good, bad])
        assert len(passed) == 1
        assert len(rejected) == 1
        assert passed[0].product_name == "Good"
        assert rejected[0].product_name == "Bad"

    def test_custom_threshold(self):
        motor = Motor(
            product_name="Borderline",
            product_type="motor",
            manufacturer=MFG,
            rated_voltage="200-240;V",
            rated_speed="3000;rpm",
            part_number="MTR-005",
        )
        # With a very high threshold, it should be rejected
        passed, rejected = filter_products([motor], min_quality=0.9)
        assert len(passed) == 0
        assert len(rejected) == 1

        # With a very low threshold, it should pass
        passed, rejected = filter_products([motor], min_quality=0.05)
        assert len(passed) == 1
        assert len(rejected) == 0

    def test_zero_threshold_passes_everything(self):
        motor = Motor(product_name="Empty", product_type="motor", manufacturer=MFG)
        passed, rejected = filter_products([motor], min_quality=0.0)
        assert len(passed) == 1
        assert len(rejected) == 0

    def test_works_with_drive_model(self):
        drive = Drive(
            product_name="TestDrive",
            product_type="drive",
            manufacturer=MFG,
            input_voltage="200-240;V",
            rated_current="5;A",
            peak_current="10;A",
            rated_power="1000;W",
            part_number="DRV-001",
            digital_inputs=4,
            digital_outputs=2,
        )
        passed, rejected = filter_products([drive])
        assert len(passed) == 1

    def test_works_with_gearhead_model(self):
        gh = Gearhead(
            product_name="TestGH",
            product_type="gearhead",
            manufacturer=MFG,
            gear_ratio=10.0,
            stages=2,
            efficiency=0.97,
            max_continuous_torque="50;Nm",
            backlash="3;arcmin",
            part_number="GH-001",
        )
        passed, rejected = filter_products([gh])
        assert len(passed) == 1
