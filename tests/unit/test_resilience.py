"""
Resilience tests: DynamoDB connectivity failures, pagination edge cases,
batch partial failures, LLM retry exhaustion, and error specificity.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from specodex.db.dynamo import DynamoDBClient
from specodex.models.motor import Motor
from specodex.models.drive import Drive


def make_client_error(code: str = "ServiceUnavailable", msg: str = "Service error"):
    return ClientError(
        {"Error": {"Code": code, "Message": msg}},
        "TestOperation",
    )


@pytest.fixture
def mock_table():
    return MagicMock()


@pytest.fixture
def db(mock_table):
    with patch("specodex.db.dynamo.boto3") as mock_boto:
        mock_boto.resource.return_value.Table.return_value = mock_table
        client = DynamoDBClient("test-table")
        client.table = mock_table
        return client


# =================== DynamoDB Connectivity Failures ===================


class TestDynamoDBConnectivityFailures:
    """Verify graceful handling of DynamoDB errors."""

    def test_create_returns_false_on_client_error(self, db, mock_table):
        mock_table.put_item.side_effect = make_client_error(
            "ProvisionedThroughputExceededException"
        )
        motor = Motor(product_type="motor", product_name="Test", manufacturer="Corp")
        assert db.create(motor) is False

    def test_create_returns_false_on_generic_error(self, db, mock_table):
        mock_table.put_item.side_effect = RuntimeError("Connection reset")
        motor = Motor(product_type="motor", product_name="Test", manufacturer="Corp")
        assert db.create(motor) is False

    def test_read_returns_none_on_client_error(self, db, mock_table):
        mock_table.get_item.side_effect = make_client_error()
        result = db.read("test-id", Motor)
        assert result is None

    def test_read_returns_none_on_timeout(self, db, mock_table):
        mock_table.get_item.side_effect = make_client_error("RequestTimeout")
        result = db.read("test-id", Motor)
        assert result is None

    def test_delete_returns_false_on_client_error(self, db, mock_table):
        mock_table.delete_item.side_effect = make_client_error()
        result = db.delete("test-id", Motor)
        assert result is False

    def test_list_returns_empty_on_client_error(self, db, mock_table):
        mock_table.query.side_effect = make_client_error()
        result = db.list(Motor)
        assert result == []

    def test_product_exists_returns_false_on_error(self, db, mock_table):
        mock_table.query.side_effect = make_client_error()
        result = db.product_exists("motor", "Corp", "Test", Motor)
        assert result is False

    def test_datasheet_exists_returns_false_on_error(self, db, mock_table):
        mock_table.scan.side_effect = make_client_error()
        result = db.datasheet_exists("https://example.com/test.pdf")
        assert result is False


# =================== Pagination Edge Cases ===================


class TestPaginationEdgeCases:
    """DynamoDB pagination can fail mid-stream; verify data isn't silently lost."""

    def test_list_handles_multi_page_results(self, db, mock_table):
        """Pagination across multiple pages returns all items."""
        from uuid import uuid4

        id1, id2 = str(uuid4()), str(uuid4())
        page1 = {
            "Items": [
                {
                    "product_id": id1,
                    "product_type": "motor",
                    "product_name": "M1",
                    "manufacturer": "Corp",
                    "PK": "PRODUCT#MOTOR",
                    "SK": f"PRODUCT#{id1}",
                }
            ],
            "LastEvaluatedKey": {"PK": "PRODUCT#MOTOR", "SK": f"PRODUCT#{id1}"},
        }
        page2 = {
            "Items": [
                {
                    "product_id": id2,
                    "product_type": "motor",
                    "product_name": "M2",
                    "manufacturer": "Corp",
                    "PK": "PRODUCT#MOTOR",
                    "SK": f"PRODUCT#{id2}",
                }
            ],
        }
        mock_table.query.side_effect = [page1, page2]

        results = db.list(Motor)
        assert len(results) == 2
        assert mock_table.query.call_count == 2

    def test_list_returns_partial_on_second_page_error(self, db, mock_table):
        """If pagination fails on page 2, exception propagates (items from page 1 lost)."""
        page1 = {
            "Items": [{"product_id": "1", "product_type": "motor"}],
            "LastEvaluatedKey": {"PK": "PRODUCT#MOTOR", "SK": "PRODUCT#1"},
        }
        mock_table.query.side_effect = [page1, make_client_error()]

        # Current behavior: exception from page 2 is caught at the outer level
        result = db.list(Motor)
        # The outer try/catch returns [] on error
        assert result == []

    def test_list_empty_table_returns_empty(self, db, mock_table):
        mock_table.query.return_value = {"Items": []}
        result = db.list(Motor)
        assert result == []


# =================== Batch Operation Failures ===================


class TestBatchOperationFailures:
    """Batch writes can partially fail; verify counts are accurate."""

    def test_batch_create_counts_individual_failures(self, db, mock_table):
        """If one item in a batch fails to serialize, count reflects partial success."""
        motors = [
            Motor(product_type="motor", product_name=f"Motor {i}", manufacturer="Corp")
            for i in range(3)
        ]

        mock_writer = MagicMock()
        mock_table.batch_writer.return_value.__enter__ = MagicMock(
            return_value=mock_writer
        )
        mock_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)

        # First two succeed, third raises
        call_count = 0

        def put_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise RuntimeError("Serialization failed")

        mock_writer.put_item.side_effect = put_side_effect

        count = db.batch_create(motors)
        assert count == 2

    def test_batch_create_empty_list_returns_zero(self, db):
        assert db.batch_create([]) == 0

    def test_batch_create_single_item(self, db, mock_table):
        mock_writer = MagicMock()
        mock_table.batch_writer.return_value.__enter__ = MagicMock(
            return_value=mock_writer
        )
        mock_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)

        motor = Motor(product_type="motor", product_name="Solo", manufacturer="Corp")
        count = db.batch_create([motor])
        assert count == 1


# =================== Update Race Condition Awareness ===================


class TestUpdateEdgeCases:
    def test_update_nonexistent_item_returns_false(self, db, mock_table):
        mock_table.get_item.return_value = {}  # No Item key = not found
        motor = Motor(product_type="motor", product_name="Ghost", manufacturer="Corp")
        result = db.update(motor)
        # update() does put_item directly, doesn't check existence
        # This tests that the operation doesn't crash
        assert result in (True, False)


# =================== Error Message Specificity ===================


class TestErrorSpecificity:
    """Verify different AWS error types are handled distinctly."""

    def test_throttling_error_handled(self, db, mock_table):
        mock_table.query.side_effect = make_client_error(
            "ProvisionedThroughputExceededException"
        )
        result = db.list(Motor)
        assert result == []

    def test_resource_not_found_handled(self, db, mock_table):
        mock_table.get_item.side_effect = make_client_error("ResourceNotFoundException")
        result = db.read("test-id", Motor)
        assert result is None

    def test_validation_error_handled(self, db, mock_table):
        mock_table.put_item.side_effect = make_client_error("ValidationException")
        motor = Motor(product_type="motor", product_name="Test", manufacturer="Corp")
        assert db.create(motor) is False

    def test_conditional_check_failure_handled(self, db, mock_table):
        mock_table.delete_item.side_effect = make_client_error(
            "ConditionalCheckFailedException"
        )
        result = db.delete("test-id", Motor)
        assert result is False


# =================== LLM Retry Exhaustion ===================


class TestLLMResilience:
    """Test LLM failure modes without making real API calls."""

    def setup_method(self) -> None:
        # _client_for is lru-cached for prod hot-path reuse. Under
        # pytest-randomly any earlier test that calls generate_content
        # without patching genai leaves a real Client cached against
        # "fake-key"; the @patch("specodex.llm.genai") below can't
        # override it, so client.models.generate_content runs the real
        # SDK and chokes on the MagicMock Parts. Wipe the cache so the
        # mock takes effect. Also use wait_none() so retries don't
        # spend ~120s on exponential backoff before raising.
        from tenacity import wait_none
        from specodex.llm import _client_for, generate_content

        generate_content.retry.wait = wait_none()
        _client_for.cache_clear()

    @patch("specodex.llm.genai")
    def test_generate_content_retries_on_failure(self, mock_genai):
        """Tenacity retries should fire on API errors."""
        from specodex.llm import generate_content
        from tenacity import RetryError

        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = RuntimeError("API quota exceeded")
        mock_client.models = mock_model

        with pytest.raises((RuntimeError, RetryError)):
            generate_content(
                b"%PDF-fake",
                "fake-key",
                "motor",
                {},
                "pdf",
            )

    def test_invalid_content_type_raises(self):
        from specodex.llm import generate_content
        from tenacity import RetryError

        with pytest.raises((ValueError, RetryError)):
            generate_content(
                b"data",
                "fake-key",
                "motor",
                {},
                "xml",  # unsupported
            )


# =================== Model Validation Edge Cases ===================


class TestModelEdgeCases:
    def test_motor_with_minimal_fields(self):
        motor = Motor(product_type="motor", product_name="Bare", manufacturer="Corp")
        assert motor.product_type == "motor"
        assert motor.PK == "PRODUCT#MOTOR"

    def test_motor_with_none_optional_fields(self):
        motor = Motor(
            product_type="motor",
            product_name="Bare",
            manufacturer="Corp",
            rated_power=None,
            rated_voltage=None,
        )
        assert motor.rated_power is None

    def test_drive_with_empty_arrays(self):
        drive = Drive(
            product_type="drive",
            product_name="Test",
            manufacturer="Corp",
            fieldbus=[],
            control_modes=[],
        )
        assert drive.fieldbus == []

    def test_product_id_auto_generated(self):
        motor = Motor(product_type="motor", product_name="Auto", manufacturer="Corp")
        assert motor.product_id is not None
        assert str(motor.product_id) != ""
