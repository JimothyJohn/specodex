"""Tests for specodex.db.dynamo.DynamoDBClient."""

from decimal import Decimal
from unittest.mock import MagicMock, patch
import uuid
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from specodex.db.dynamo import DynamoDBClient
from specodex.models.datasheet import Datasheet
from specodex.models.motor import Motor


def _make_client(mock_boto3: MagicMock) -> tuple[DynamoDBClient, MagicMock]:
    """Create a DynamoDBClient with fully mocked boto3, return (client, mock_table)."""
    mock_table = MagicMock()
    mock_resource = MagicMock()
    mock_resource.Table.return_value = mock_table
    mock_boto3.resource.return_value = mock_resource
    client = DynamoDBClient(table_name="products")
    return client, mock_table


def _client_error(
    code: str = "ValidationException", msg: str = "test error"
) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": msg}},
        "TestOp",
    )


# ---------------------------------------------------------------------------
# TestConvertFloatsToDecimal
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestConvertFloatsToDecimal:
    @patch("specodex.db.dynamo.boto3")
    def test_float_converted(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        assert client._convert_floats_to_decimal(3.14) == Decimal("3.14")

    @patch("specodex.db.dynamo.boto3")
    def test_nested_dict(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        result = client._convert_floats_to_decimal({"a": {"b": 1.5}})
        assert result == {"a": {"b": Decimal("1.5")}}

    @patch("specodex.db.dynamo.boto3")
    def test_list_items(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        result = client._convert_floats_to_decimal([1.0, 2.0])
        assert result == [Decimal("1.0"), Decimal("2.0")]

    @patch("specodex.db.dynamo.boto3")
    def test_non_float_unchanged(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        assert client._convert_floats_to_decimal("hello") == "hello"
        assert client._convert_floats_to_decimal(42) == 42

    @patch("specodex.db.dynamo.boto3")
    def test_string_unchanged(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        assert client._convert_floats_to_decimal("3.14") == "3.14"


# ---------------------------------------------------------------------------
# TestSerializeItem
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestSerializeItem:
    @patch("specodex.db.dynamo.boto3")
    def test_motor_serialization(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        motor = Motor(
            product_name="TestMotor",
            product_type="motor",
            manufacturer="Acme",
        )
        data = client._serialize_item(motor)
        assert data["PK"] == "PRODUCT#MOTOR"
        assert data["SK"] == f"PRODUCT#{motor.product_id}"
        assert isinstance(data["product_id"], str)

    @patch("specodex.db.dynamo.boto3")
    def test_datasheet_serialization(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        ds = Datasheet(
            url="https://example.com/ds.pdf",
            product_type="motor",
            product_name="TestMotor",
            manufacturer="Acme",
        )
        data = client._serialize_item(ds)
        assert data["PK"] == "DATASHEET#MOTOR"
        assert data["SK"] == f"DATASHEET#{ds.datasheet_id}"

    @patch("specodex.db.dynamo.boto3")
    def test_uuid_to_string(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        motor = Motor(
            product_name="TestMotor",
            product_type="motor",
            manufacturer="Acme",
        )
        data = client._serialize_item(motor)
        assert isinstance(data["product_id"], str)
        assert data["product_id"] == str(motor.product_id)

    @patch("specodex.db.dynamo.boto3")
    def test_value_unit_serialised_as_dict(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        motor = Motor(
            product_name="TestMotor",
            product_type="motor",
            manufacturer="Acme",
            rated_speed={"value": 3000, "unit": "rpm"},
        )
        data = client._serialize_item(motor)
        assert data["rated_speed"] == {"value": Decimal("3000"), "unit": "rpm"}

    @patch("specodex.db.dynamo.boto3")
    def test_min_max_unit_serialised_as_dict(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        motor = Motor(
            product_name="TestMotor",
            product_type="motor",
            manufacturer="Acme",
            rated_voltage={"min": 100, "max": 240, "unit": "V"},
        )
        data = client._serialize_item(motor)
        assert data["rated_voltage"] == {
            "min": Decimal("100"),
            "max": Decimal("240"),
            "unit": "V",
        }


# ---------------------------------------------------------------------------
# TestDeserializeItem
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestDeserializeItem:
    @patch("specodex.db.dynamo.boto3")
    def test_valid_motor(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        uid = str(uuid4())
        item = {
            "PK": "PRODUCT#MOTOR",
            "SK": f"PRODUCT#{uid}",
            "product_id": uid,
            "product_type": "motor",
            "product_name": "TestMotor",
            "manufacturer": "Acme",
        }
        result = client._deserialize_item(item, Motor)
        assert isinstance(result, Motor)
        assert result.product_name == "TestMotor"

    @patch("specodex.db.dynamo.boto3")
    def test_invalid_data_returns_none(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        result = client._deserialize_item({"garbage": True}, Motor)
        assert result is None


# ---------------------------------------------------------------------------
# TestCRUD
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestCRUD:
    @patch("specodex.db.dynamo.boto3")
    def test_create_success(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        motor = Motor(
            product_name="TestMotor",
            product_type="motor",
            manufacturer="Acme",
        )
        assert client.create(motor) is True
        mock_table.put_item.assert_called_once()

    @patch("specodex.db.dynamo.boto3")
    def test_create_client_error(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        mock_table.put_item.side_effect = _client_error()
        motor = Motor(
            product_name="TestMotor",
            product_type="motor",
            manufacturer="Acme",
        )
        assert client.create(motor) is False

    @patch("specodex.db.dynamo.boto3")
    def test_read_found(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        uid = str(uuid4())
        mock_table.get_item.return_value = {
            "Item": {
                "PK": "PRODUCT#MOTOR",
                "SK": f"PRODUCT#{uid}",
                "product_id": uid,
                "product_type": "motor",
                "product_name": "TestMotor",
                "manufacturer": "Acme",
            }
        }
        result = client.read(uid, Motor)
        assert isinstance(result, Motor)
        assert result.product_name == "TestMotor"

    @patch("specodex.db.dynamo.boto3")
    def test_read_not_found(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        mock_table.get_item.return_value = {}
        result = client.read(str(uuid4()), Motor)
        assert result is None

    @patch("specodex.db.dynamo.boto3")
    def test_update_success(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        motor = Motor(
            product_name="TestMotor",
            product_type="motor",
            manufacturer="Acme",
        )
        assert client.update(motor) is True
        mock_table.update_item.assert_called_once()
        call_kwargs = mock_table.update_item.call_args[1]
        assert "UpdateExpression" in call_kwargs
        assert call_kwargs["UpdateExpression"].startswith("SET ")

    @patch("specodex.db.dynamo.boto3")
    def test_delete_success(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        uid = str(uuid4())
        deleted = client.delete(uid, Motor)
        assert deleted is True
        mock_table.delete_item.assert_called_once()


# ---------------------------------------------------------------------------
# TestList
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestList:
    @patch("specodex.db.dynamo.boto3")
    def test_list_by_type(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        uid = str(uuid4())
        mock_table.query.return_value = {
            "Items": [
                {
                    "PK": "PRODUCT#MOTOR",
                    "SK": f"PRODUCT#{uid}",
                    "product_id": uid,
                    "product_type": "motor",
                    "product_name": "Motor1",
                    "manufacturer": "Acme",
                }
            ]
        }
        results = client.list(Motor)
        assert len(results) == 1
        assert isinstance(results[0], Motor)

    @patch("specodex.db.dynamo.boto3")
    def test_list_with_limit(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        mock_table.query.return_value = {"Items": []}
        client.list(Motor, limit=5)
        call_kwargs = mock_table.query.call_args[1]
        assert call_kwargs["Limit"] == 5

    @patch("specodex.db.dynamo.boto3")
    def test_list_pagination(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        uid1 = str(uuid4())
        uid2 = str(uuid4())
        # First page returns one item + LastEvaluatedKey; second page returns another item
        mock_table.query.side_effect = [
            {
                "Items": [
                    {
                        "PK": "PRODUCT#MOTOR",
                        "SK": f"PRODUCT#{uid1}",
                        "product_id": uid1,
                        "product_type": "motor",
                        "product_name": "Motor1",
                        "manufacturer": "Acme",
                    }
                ],
                "LastEvaluatedKey": {"PK": "PRODUCT#MOTOR", "SK": f"PRODUCT#{uid1}"},
            },
            {
                "Items": [
                    {
                        "PK": "PRODUCT#MOTOR",
                        "SK": f"PRODUCT#{uid2}",
                        "product_id": uid2,
                        "product_type": "motor",
                        "product_name": "Motor2",
                        "manufacturer": "Acme",
                    }
                ]
            },
        ]
        results = client.list(Motor)
        assert len(results) == 2
        assert mock_table.query.call_count == 2


# ---------------------------------------------------------------------------
# TestBatchCreate
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestBatchCreate:
    @patch("specodex.db.dynamo.boto3")
    def test_batch_empty_list(self, mock_boto3: MagicMock) -> None:
        client, _ = _make_client(mock_boto3)
        assert client.batch_create([]) == 0

    @patch("specodex.db.dynamo.boto3")
    def test_batch_success(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        mock_writer = MagicMock()
        mock_table.batch_writer.return_value.__enter__ = MagicMock(
            return_value=mock_writer
        )
        mock_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
        motors = [
            Motor(product_name=f"Motor{i}", product_type="motor", manufacturer="Acme")
            for i in range(3)
        ]
        count = client.batch_create(motors)
        assert count == 3

    @patch("specodex.db.dynamo.boto3")
    def test_batch_flush_failure_not_counted(self, mock_boto3: MagicMock) -> None:
        """Regression: items were counted as they were buffered into the
        batch_writer, but the write happens on context exit. A flush
        failure (boto3's unprocessed-items retries exhausted) reported
        every buffered item as written — process_datasheet then logged
        success and marked the ingest-log STATUS_SUCCESS for rows that
        never landed."""
        client, mock_table = _make_client(mock_boto3)
        mock_writer = MagicMock()
        mock_table.batch_writer.return_value.__enter__ = MagicMock(
            return_value=mock_writer
        )
        mock_table.batch_writer.return_value.__exit__ = MagicMock(
            side_effect=RuntimeError("flush failed after retries")
        )
        motors = [
            Motor(product_name=f"Motor{i}", product_type="motor", manufacturer="Acme")
            for i in range(3)
        ]
        assert client.batch_create(motors) == 0


# ---------------------------------------------------------------------------
# TestDatasheetOps
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestDatasheetOps:
    @patch("specodex.db.dynamo.boto3")
    def test_datasheet_exists_true(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        mock_table.scan.return_value = {
            "Items": [{"url": "https://example.com/ds.pdf"}]
        }
        assert client.datasheet_exists("https://example.com/ds.pdf") is True

    @patch("specodex.db.dynamo.boto3")
    def test_datasheet_exists_false(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        mock_table.scan.return_value = {"Items": []}
        assert client.datasheet_exists("https://example.com/ds.pdf") is False

    @patch("specodex.db.dynamo.boto3")
    def test_product_exists(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        uid = str(uuid4())
        mock_table.query.return_value = {
            "Items": [
                {
                    "PK": "PRODUCT#MOTOR",
                    "SK": f"PRODUCT#{uid}",
                    "product_id": uid,
                    "product_type": "motor",
                    "product_name": "TestMotor",
                    "manufacturer": "Acme",
                }
            ]
        }
        assert client.product_exists("motor", "Acme", "TestMotor", Motor) is True


# ---------------------------------------------------------------------------
# TestBatchCreateHardening — 2026-07-25 CGI ingest incident
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestBatchCreateHardening:
    """batch_create fed two part numbers whose normalized product IDs
    collide ('5.5:1' vs '55:1' — compute_product_id strips punctuation)
    put duplicate keys in one BatchWriteItem chunk. DynamoDB rejected
    the whole chunk, the ClientError escaped the chunk loop, and every
    REMAINING chunk was silently abandoned — 'WROTE 536' with 193 rows
    actually readable."""

    def _writer(self, mock_table, exit_effects=None):
        mock_writer = MagicMock()
        mock_table.batch_writer.return_value.__enter__ = MagicMock(
            return_value=mock_writer
        )
        if exit_effects is None:
            mock_table.batch_writer.return_value.__exit__ = MagicMock(
                return_value=False
            )
        else:
            mock_table.batch_writer.return_value.__exit__ = MagicMock(
                side_effect=exit_effects
            )
        return mock_writer

    @patch("specodex.db.dynamo.boto3")
    def test_duplicate_ids_deduped_last_wins(self, mock_boto3: MagicMock) -> None:
        client, mock_table = _make_client(mock_boto3)
        writer = self._writer(mock_table)
        dup_id = uuid.uuid4()
        first = Motor(
            product_name="M",
            product_type="motor",
            manufacturer="Acme",
            product_id=dup_id,
            part_number="017PLX 5.5:1",
        )
        second = Motor(
            product_name="M",
            product_type="motor",
            manufacturer="Acme",
            product_id=dup_id,
            part_number="017PLX 55:1",
        )
        other = Motor(product_name="M2", product_type="motor", manufacturer="Acme")
        count = client.batch_create([first, second, other])
        assert count == 2  # duplicate collapsed, truthful count
        assert writer.put_item.call_count == 2
        written_pns = [
            c.kwargs["Item"].get("part_number") for c in writer.put_item.call_args_list
        ]
        assert "017PLX 55:1" in written_pns  # last wins
        assert "017PLX 5.5:1" not in written_pns

    @patch("specodex.db.dynamo.boto3")
    def test_duplicate_id_different_pn_warns(
        self, mock_boto3: MagicMock, caplog
    ) -> None:
        client, mock_table = _make_client(mock_boto3)
        self._writer(mock_table)
        dup_id = uuid.uuid4()
        a = Motor(
            product_name="M",
            product_type="motor",
            manufacturer="Acme",
            product_id=dup_id,
            part_number="A 5.5:1",
        )
        b = Motor(
            product_name="M",
            product_type="motor",
            manufacturer="Acme",
            product_id=dup_id,
            part_number="A 55:1",
        )
        import logging

        with caplog.at_level(logging.WARNING, logger="specodex.db.dynamo"):
            client.batch_create([a, b])
        assert any("collide" in r.message for r in caplog.records)

    @patch("specodex.db.dynamo.boto3")
    def test_failed_chunk_does_not_abandon_remaining(
        self, mock_boto3: MagicMock
    ) -> None:
        client, mock_table = _make_client(mock_boto3)
        boom = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "duplicates"}},
            "BatchWriteItem",
        )
        self._writer(mock_table, exit_effects=[boom, False])
        motors = [
            Motor(product_name=f"M{i}", product_type="motor", manufacturer="Acme")
            for i in range(30)  # 2 chunks of 25 + 5
        ]
        count = client.batch_create(motors)
        assert count == 5  # chunk 1 failed, chunk 2 still written


@pytest.mark.unit
class TestDeleteArgOrder:
    @patch("specodex.db.dynamo.boto3")
    def test_reversed_arguments_raise_clearly(self, mock_boto3: MagicMock) -> None:
        """delete(model_class, id) — the reversed call — used to surface
        as \"'UUID' object has no attribute 'model_fields'\" deep inside
        serialization. Fail fast with the signature in the message."""
        client, _ = _make_client(mock_boto3)
        with pytest.raises(TypeError, match="delete\\(product_id, model_class\\)"):
            client.delete(Motor, uuid.uuid4())  # type: ignore[arg-type]
