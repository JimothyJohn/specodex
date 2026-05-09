"""
Integration tests for DynamoDB CRUD operations using moto.

All tests use moto to mock DynamoDB in-process.
No real AWS credentials are needed.
"""

from __future__ import annotations

import os
from uuid import UUID

import boto3
import moto
import pytest

from specodex.db.dynamo import DynamoDBClient
from specodex.models.datasheet import Datasheet
from specodex.models.drive import Drive
from specodex.models.motor import Motor


@pytest.fixture
def db_setup():
    with moto.mock_aws():
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="products",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        client = DynamoDBClient(table_name="products")
        yield client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_motor(
    product_id: UUID = UUID("00000000-0000-0000-0000-000000000001"),
    product_name: str = "M3AA 132",
    manufacturer: str = "ABB",
    part_number: str = "3GAA132001-ASE",
    rated_speed: str = "3000;rpm",
    rated_torque: str = "10;Nm",
) -> Motor:
    return Motor(
        product_id=product_id,
        product_type="motor",
        product_name=product_name,
        manufacturer=manufacturer,
        part_number=part_number,
        rated_speed=rated_speed,
        rated_torque=rated_torque,
    )


def _make_drive(
    product_id: UUID = UUID("00000000-0000-0000-0000-000000000099"),
    product_name: str = "ACS580-01",
    manufacturer: str = "ABB",
    part_number: str = "ACS580-01-012A-4",
    fieldbus: list[str] | None = None,
) -> Drive:
    return Drive(
        product_id=product_id,
        product_type="drive",
        product_name=product_name,
        manufacturer=manufacturer,
        part_number=part_number,
        fieldbus=fieldbus or ["EtherCAT"],
        rated_current="3.0;A",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMotorCRUD:
    def test_motor_crud_cycle(self, db_setup: DynamoDBClient) -> None:
        """Create -> read -> update -> read -> delete -> read returns None."""
        client = db_setup
        motor = _make_motor()

        # Create
        assert client.create(motor) is True

        # Read back
        fetched = client.read(motor.product_id, Motor)
        assert fetched is not None
        assert fetched.product_name == "M3AA 132"
        assert fetched.manufacturer == "ABB"
        assert fetched.part_number == "3GAA132001-ASE"

        # Update rated_speed
        from specodex.models.common import ValueUnit

        motor.rated_speed = ValueUnit(value=6000, unit="rpm")
        assert client.update(motor) is True

        updated = client.read(motor.product_id, Motor)
        assert updated is not None
        assert updated.rated_speed == ValueUnit(value=6000, unit="rpm")

        # Delete
        deleted = client.delete(motor.product_id, Motor)
        assert deleted is True

        gone = client.read(motor.product_id, Motor)
        assert gone is None


@pytest.mark.integration
class TestDriveCRUD:
    def test_drive_crud_cycle(self, db_setup: DynamoDBClient) -> None:
        """Create -> read -> verify list field -> update -> delete -> gone."""
        client = db_setup
        drive = _make_drive(fieldbus=["EtherCAT"])

        assert client.create(drive) is True

        fetched = client.read(drive.product_id, Drive)
        assert fetched is not None
        assert fetched.product_name == "ACS580-01"
        assert fetched.fieldbus == ["EtherCAT"]

        # Update fieldbus list
        drive.fieldbus = ["EtherCAT", "PROFINET"]
        assert client.update(drive) is True

        updated = client.read(drive.product_id, Drive)
        assert updated is not None
        assert set(updated.fieldbus) == {"EtherCAT", "PROFINET"}

        # Delete
        deleted = client.delete(drive.product_id, Drive)
        assert deleted is True
        assert client.read(drive.product_id, Drive) is None


@pytest.mark.integration
class TestDatasheetCRUD:
    def test_datasheet_crud_cycle(self, db_setup: DynamoDBClient) -> None:
        """Create datasheet -> exists check -> get_all -> get_by_name."""
        client = db_setup
        ds = Datasheet(
            datasheet_id=UUID("00000000-0000-0000-0000-0000000000aa"),
            url="https://example.com/m3aa.pdf",
            product_type="motor",
            product_name="M3AA 132",
            manufacturer="ABB",
        )

        assert client.create(ds) is True

        # datasheet_exists by URL
        assert client.datasheet_exists("https://example.com/m3aa.pdf") is True
        assert client.datasheet_exists("https://example.com/nope.pdf") is False

        # get_all_datasheets
        all_ds = client.get_all_datasheets()
        assert len(all_ds) == 1
        assert all_ds[0].url == "https://example.com/m3aa.pdf"

        # get_datasheets_by_product_name
        by_name = client.get_datasheets_by_product_name("M3AA 132")
        assert len(by_name) == 1
        assert by_name[0].product_name == "M3AA 132"

        # Non-existent name
        assert client.get_datasheets_by_product_name("Nonexistent") == []


@pytest.mark.integration
class TestBatchAndList:
    def test_batch_create_and_list(self, db_setup: DynamoDBClient) -> None:
        """Create 30 motors via batch_create -> list returns all 30."""
        client = db_setup

        motors = [
            _make_motor(
                product_id=UUID(f"00000000-0000-0000-0000-{i:012d}"),
                part_number=f"MOTOR-{i:04d}",
                product_name=f"TestMotor-{i}",
            )
            for i in range(1, 31)
        ]

        created = client.batch_create(motors)
        assert created == 30

        listed = client.list(Motor)
        assert len(listed) == 30


@pytest.mark.integration
class TestProductExists:
    def test_product_exists_check(self, db_setup: DynamoDBClient) -> None:
        """product_exists returns True/False correctly."""
        client = db_setup
        motor = _make_motor(manufacturer="ABB", product_name="M3AA")
        client.create(motor)

        assert client.product_exists("motor", "ABB", "M3AA", Motor) is True
        assert client.product_exists("motor", "ABB", "Different", Motor) is False


@pytest.mark.integration
class TestDeleteByProductType:
    def test_delete_by_product_type(self, db_setup: DynamoDBClient) -> None:
        """Deleting motors leaves drives intact."""
        client = db_setup

        motors = [
            _make_motor(
                product_id=UUID(f"00000000-0000-0000-0000-{i:012d}"),
                part_number=f"MOT-{i}",
                product_name=f"Motor-{i}",
            )
            for i in range(1, 4)
        ]
        drives = [
            _make_drive(
                product_id=UUID(f"00000000-0000-0000-0001-{i:012d}"),
                part_number=f"DRV-{i}",
                product_name=f"Drive-{i}",
            )
            for i in range(1, 3)
        ]

        client.batch_create(motors)
        client.batch_create(drives)

        assert len(client.list(Motor)) == 3
        assert len(client.list(Drive)) == 2

        deleted = client.delete_by_product_type("motor", confirm=True)
        assert deleted == 3

        assert len(client.list(Motor)) == 0
        assert len(client.list(Drive)) == 2


@pytest.mark.integration
class TestDeleteDuplicates:
    def test_delete_duplicates(self, db_setup: DynamoDBClient) -> None:
        """Two motors sharing a part_number -> one deleted, unique kept."""
        client = db_setup

        m1 = _make_motor(
            product_id=UUID("00000000-0000-0000-0000-000000000001"),
            part_number="DUPE-001",
            product_name="Motor-A",
        )
        m2 = _make_motor(
            product_id=UUID("00000000-0000-0000-0000-000000000002"),
            part_number="DUPE-001",
            product_name="Motor-B",
        )
        m3 = _make_motor(
            product_id=UUID("00000000-0000-0000-0000-000000000003"),
            part_number="UNIQUE-001",
            product_name="Motor-C",
        )

        client.batch_create([m1, m2, m3])
        assert len(client.list(Motor)) == 3

        stats = client.delete_duplicates(confirm=True)
        assert stats["duplicates_deleted"] == 1

        remaining = client.list(Motor)
        assert len(remaining) == 2
