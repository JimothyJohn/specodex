"""
Performance tests: verify that core operations complete within acceptable
time bounds. These are lightweight smoke tests, not load tests.
"""

import time
from unittest.mock import MagicMock, patch


from specodex.models.motor import Motor
from specodex.models.drive import Drive
from specodex.models.robot_arm import RobotArm
from specodex.models.common import MinMaxUnit, ValueUnit


class TestModelSerializationPerformance:
    """Model creation and serialization should be fast."""

    def test_motor_creation_under_10ms(self):
        start = time.perf_counter()
        for _ in range(100):
            Motor(product_type="motor", product_name="Test", manufacturer="Corp")
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 500, f"100 motors took {elapsed:.1f}ms (expected <500ms)"

    def test_drive_creation_under_10ms(self):
        start = time.perf_counter()
        for _ in range(100):
            Drive(product_type="drive", product_name="Test", manufacturer="Corp")
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 500, f"100 drives took {elapsed:.1f}ms (expected <500ms)"

    def test_robot_arm_creation_under_10ms(self):
        start = time.perf_counter()
        for _ in range(100):
            RobotArm(product_name="Test", manufacturer="Corp")
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 500, f"100 robot arms took {elapsed:.1f}ms (expected <500ms)"

    def test_model_dump_under_5ms(self):
        motor = Motor(
            product_type="motor",
            product_name="Test",
            manufacturer="Corp",
            rated_power="100;W",
            rated_voltage="24-48;V",
            rated_speed="3000;rpm",
        )
        start = time.perf_counter()
        for _ in range(100):
            motor.model_dump()
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 500, f"100 model_dump calls took {elapsed:.1f}ms"


class TestValidatorPerformance:
    """Pydantic validators should not be a bottleneck."""

    def test_value_unit_input_handling_fast(self):
        inputs = [
            {"value": 100, "unit": "W"},
            "100 W",
            "100;W",
            {"min": 10, "max": 50, "unit": "V"},
        ]
        start = time.perf_counter()
        for _ in range(1000):
            for inp in inputs:
                try:
                    ValueUnit.model_validate(inp)
                except Exception:
                    # Some inputs are intentionally invalid; the perf test
                    # measures throughput including the failure path.
                    pass
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 600, f"4000 validator calls took {elapsed:.1f}ms"

    def test_min_max_unit_input_handling_fast(self):
        inputs = [
            {"min": 0, "max": 100, "unit": "C"},
            {"min": -20, "unit": "C"},
            {"value": 24, "unit": "V"},
            "10-50;C",
        ]
        start = time.perf_counter()
        for _ in range(1000):
            for inp in inputs:
                try:
                    MinMaxUnit.model_validate(inp)
                except Exception:
                    # Some inputs are intentionally invalid; the perf test
                    # measures throughput including the failure path.
                    pass
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 600, f"4000 validator calls took {elapsed:.1f}ms"


class TestDBOperationPerformance:
    """DynamoDB client operations (with mocked table) should be fast."""

    @patch("specodex.db.dynamo.boto3")
    def test_serialization_performance(self, mock_boto):
        from specodex.db.dynamo import DynamoDBClient

        mock_table = MagicMock()
        mock_boto.resource.return_value.Table.return_value = mock_table
        client = DynamoDBClient("test-table")

        motor = Motor(
            product_type="motor",
            product_name="Perf Test",
            manufacturer="Corp",
            rated_power="100;W",
        )

        start = time.perf_counter()
        for _ in range(100):
            client._serialize_item(motor)
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 500, f"100 serializations took {elapsed:.1f}ms"

    @patch("specodex.db.dynamo.boto3")
    def test_batch_serialization_performance(self, mock_boto):
        """Batch of 25 items (DynamoDB max) should serialize quickly."""
        from specodex.db.dynamo import DynamoDBClient

        mock_table = MagicMock()
        mock_boto.resource.return_value.Table.return_value = mock_table
        client = DynamoDBClient("test-table")

        motors = [
            Motor(product_type="motor", product_name=f"Motor {i}", manufacturer="Corp")
            for i in range(25)
        ]

        start = time.perf_counter()
        for motor in motors:
            client._serialize_item(motor)
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 500, f"25 serializations took {elapsed:.1f}ms"
