"""Integration test for ``backfill_motor_mounts`` against moto.

Verifies the walker's contract:

- ``apply=False`` is read-only — no DB writes regardless of how
  many rows would match.
- ``apply=True`` writes ``motor_mount_pattern`` for matched rows and
  leaves everything else alone.
- Idempotent — a second ``apply=True`` pass is a no-op (already_set
  goes up, matched/written stay at 0).
- Never overwrites a non-null ``motor_mount_pattern``.
"""

from __future__ import annotations

import os

import boto3
import moto
import pytest

from specodex.admin.motor_mount_backfill import backfill_motor_mounts
from specodex.db.dynamo import DynamoDBClient
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


def _seed_motors(client: DynamoDBClient) -> list[Motor]:
    """Fixed-set of motors covering every backfill outcome.

    - 2 derivable (NEMA 17, NEMA 23)
    - 1 already-set (motor_mount_pattern present)
    - 1 no-frame-size
    - 1 unmatched-frame ("60mm")
    """
    motors = [
        Motor(
            product_type="motor",
            product_name="Stepper-A",
            manufacturer="Acme",
            part_number="A1",
            frame_size="NEMA 17",
        ),
        Motor(
            product_type="motor",
            product_name="Stepper-B",
            manufacturer="Acme",
            part_number="A2",
            frame_size="NEMA 23",
        ),
        Motor(
            product_type="motor",
            product_name="Already-Set",
            manufacturer="Acme",
            part_number="A3",
            frame_size="NEMA 34",
            motor_mount_pattern="NEMA 34",
        ),
        Motor(
            product_type="motor",
            product_name="No-Frame",
            manufacturer="Acme",
            part_number="A4",
        ),
        Motor(
            product_type="motor",
            product_name="Unknown-Frame",
            manufacturer="Acme",
            part_number="A5",
            frame_size="60mm",
        ),
    ]
    for m in motors:
        assert client.create(m)
    return motors


class TestBackfillMotorMounts:
    def test_dry_run_writes_nothing(self, db_setup):
        _seed_motors(db_setup)
        result = backfill_motor_mounts(db_setup, apply=False)

        assert result.applied is False
        assert result.considered == 5
        assert result.already_set == 1
        assert result.no_frame_size == 1
        assert result.unmatched_frame == 1
        assert result.matched == 2  # NEMA 17 + NEMA 23
        assert result.written == 0  # dry run

        # Confirm the DB wasn't touched.
        for m in db_setup.list(Motor):
            if m.product_name == "Already-Set":
                assert m.motor_mount_pattern == "NEMA 34"
            else:
                # Everything else either had no motor_mount_pattern
                # or we haven't applied yet.
                assert m.motor_mount_pattern is None or (
                    m.product_name == "Already-Set"
                )

    def test_apply_writes_matched_rows(self, db_setup):
        _seed_motors(db_setup)
        result = backfill_motor_mounts(db_setup, apply=True)

        assert result.applied is True
        assert result.matched == 2
        assert result.written == 2

        # Confirm the DB now has the derived patterns.
        by_name = {m.product_name: m for m in db_setup.list(Motor)}
        assert by_name["Stepper-A"].motor_mount_pattern == "NEMA 17"
        assert by_name["Stepper-B"].motor_mount_pattern == "NEMA 23"
        # Idempotent — already-set row untouched.
        assert by_name["Already-Set"].motor_mount_pattern == "NEMA 34"
        # No frame_size to derive from — still null.
        assert by_name["No-Frame"].motor_mount_pattern is None
        # Unmatched frame — still null until per-vendor lookup arrives.
        assert by_name["Unknown-Frame"].motor_mount_pattern is None

    def test_apply_is_idempotent(self, db_setup):
        """Re-running after a successful apply is a no-op (every row
        is either already_set, no_frame_size, or unmatched_frame)."""
        _seed_motors(db_setup)
        first = backfill_motor_mounts(db_setup, apply=True)
        assert first.written == 2

        second = backfill_motor_mounts(db_setup, apply=True)
        assert second.matched == 0
        assert second.written == 0
        assert second.already_set == 3  # was 1, now 1 + 2 freshly-written
        assert second.no_frame_size == 1
        assert second.unmatched_frame == 1

    def test_samples_captured_for_spot_check(self, db_setup):
        _seed_motors(db_setup)
        result = backfill_motor_mounts(db_setup, apply=False)
        # Two matched rows → two samples, capped at 10.
        assert len(result.samples) == 2
        for s in result.samples:
            assert "frame_size" in s and "motor_mount_pattern" in s

    def test_empty_table_returns_zero_counts(self, db_setup):
        result = backfill_motor_mounts(db_setup, apply=False)
        assert result.considered == 0
        assert result.matched == 0
        assert result.written == 0
