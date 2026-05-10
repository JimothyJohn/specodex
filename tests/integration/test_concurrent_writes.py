"""Concurrent-write stress test for ``DynamoDBClient.batch_create``.

`scraper.batch_create` and `process_datasheet` write shared DynamoDB
state. Without this test, the codepath has no coverage under
concurrent writes with overlapping ``product_id``s — the exact
shape race conditions take in production when two scraper workers
ingest the same datasheet.

What this test asserts:

1. **No exceptions under concurrent writes.** 20 parallel writers
   issuing batch_creates with overlapping IDs return cleanly.
2. **Last-write-wins, not torn-write.** For each contested
   ``product_id``, the final row matches ONE writer's full
   signature (all fields from one writer, no field-by-field
   mixing). DynamoDB's ``put_item`` is atomic at the item level —
   if this test ever fails on the field-by-field check, something
   in the serialisation path has split a logical write into two
   physical writes.
3. **No rows lost or duplicated.** The total row count after the
   stress run equals the number of distinct (product_type,
   product_id) keys. No duplicate keys; no missing rows.

Per HARDENING.md Phase 3.4. Uses ``moto.mock_aws`` for in-process
DynamoDB — no real AWS, no fixtures from prod data.
"""

from __future__ import annotations

import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import UUID

import boto3
import moto
import pytest

from specodex.db.dynamo import DynamoDBClient
from specodex.models.motor import Motor


# Keep the test deterministic — pytest-randomly auto-loads, so any
# random.* call would otherwise drift across runs.
_RNG_SEED = 20260510


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


def _writer_signature_motor(product_id: UUID, writer_id: int) -> Motor:
    """Build a Motor whose every text field encodes the writer_id.

    Lets the assertion phase detect "field A from writer 7, field B
    from writer 12" — which would mean the put_item was split into
    two physical writes (a torn write).
    """
    return Motor(
        product_id=product_id,
        product_type="motor",
        product_name=f"writer-{writer_id}-product-name",
        manufacturer=f"writer-{writer_id}-mfg",
        part_number=f"writer-{writer_id}-pn",
        series=f"writer-{writer_id}-series",
        product_family=f"writer-{writer_id}-family",
    )


def _writer_run(
    db_client: DynamoDBClient,
    writer_id: int,
    contested_ids: list[UUID],
    jitter_seed: int,
) -> int:
    """Worker thread: write a Motor per contested ID, with timing jitter.

    Jitter is what triggers races — without sleeps the OS scheduler
    can serialise the writes by accident and miss the actual race.
    """
    rng = random.Random(jitter_seed)
    models = [_writer_signature_motor(pid, writer_id) for pid in contested_ids]
    # Sub-millisecond jitter to interleave writes across threads.
    time.sleep(rng.uniform(0.0, 0.005))
    return db_client.batch_create(models)


class TestConcurrentBatchCreate:
    """20 writers × overlapping IDs = the production race condition."""

    NUM_WRITERS = 20
    NUM_CONTESTED_IDS = 5

    def _contested_ids(self) -> list[UUID]:
        # Deterministic UUIDs so test failure logs are diffable.
        return [
            UUID(f"00000000-0000-0000-0000-{i:012x}")
            for i in range(1, self.NUM_CONTESTED_IDS + 1)
        ]

    def test_no_exceptions_under_concurrency(self, db_setup):
        contested = self._contested_ids()
        rng_master = random.Random(_RNG_SEED)
        seeds = [rng_master.randint(0, 1 << 31) for _ in range(self.NUM_WRITERS)]

        with ThreadPoolExecutor(max_workers=self.NUM_WRITERS) as pool:
            futures = [
                pool.submit(_writer_run, db_setup, wid, contested, seeds[wid])
                for wid in range(self.NUM_WRITERS)
            ]
            results = [f.result() for f in as_completed(futures)]

        # Each writer claims to have written all of its models. moto's
        # batch_writer should never silently drop on the contested path.
        for r in results:
            assert r == self.NUM_CONTESTED_IDS, (
                f"writer reported {r} successes, expected "
                f"{self.NUM_CONTESTED_IDS} — silent drop in batch_create"
            )

    def test_no_torn_writes_per_id(self, db_setup):
        """For each contested ID, final row = ONE writer's full signature.

        If the test ever fails with "writer 7 wrote name, writer 12
        wrote part_number" on the same row, the put_item was split
        into a multi-step transaction somewhere in the serialiser —
        that's the regression to catch.
        """
        contested = self._contested_ids()
        rng_master = random.Random(_RNG_SEED)
        seeds = [rng_master.randint(0, 1 << 31) for _ in range(self.NUM_WRITERS)]

        with ThreadPoolExecutor(max_workers=self.NUM_WRITERS) as pool:
            futures = [
                pool.submit(_writer_run, db_setup, wid, contested, seeds[wid])
                for wid in range(self.NUM_WRITERS)
            ]
            for f in as_completed(futures):
                f.result()

        # Read each contested row back. Every row's text fields must
        # all encode the SAME writer_id — no field-by-field mixing.
        for pid in contested:
            row = db_setup.read(pid, Motor)
            assert row is not None, f"row {pid} lost under concurrent writes"

            # Extract the writer_id from every text field on the row.
            # Format is "writer-<N>-<suffix>". They must all agree.
            text_fields = {
                "product_name": row.product_name,
                "manufacturer": row.manufacturer,
                "part_number": row.part_number,
                "series": row.series,
                "product_family": row.product_family,
            }
            writer_ids_seen: set[str] = set()
            for fname, fval in text_fields.items():
                assert fval is not None and fval.startswith("writer-"), (
                    f"row {pid} field {fname} = {fval!r} — not a "
                    f"writer-signature value, possible serialiser drift"
                )
                # "writer-7-product-name" → "7"
                writer_id = fval.split("-")[1]
                writer_ids_seen.add(writer_id)
            assert len(writer_ids_seen) == 1, (
                f"row {pid} has fields from multiple writers "
                f"({sorted(writer_ids_seen)}) — TORN WRITE detected. "
                f"The put_item path has split atomicity somewhere."
            )

    def test_no_rows_lost_or_duplicated(self, db_setup):
        contested = self._contested_ids()
        rng_master = random.Random(_RNG_SEED)
        seeds = [rng_master.randint(0, 1 << 31) for _ in range(self.NUM_WRITERS)]

        with ThreadPoolExecutor(max_workers=self.NUM_WRITERS) as pool:
            futures = [
                pool.submit(_writer_run, db_setup, wid, contested, seeds[wid])
                for wid in range(self.NUM_WRITERS)
            ]
            for f in as_completed(futures):
                f.result()

        # Distinct (product_type, product_id) keys should equal
        # the number of contested IDs — no row lost, no duplicate.
        all_motors = db_setup.list(Motor)
        ids_seen = [str(m.product_id) for m in all_motors]
        assert len(ids_seen) == len(set(ids_seen)), (
            f"duplicate product_id rows in DynamoDB: {ids_seen}"
        )
        assert len(ids_seen) == self.NUM_CONTESTED_IDS, (
            f"expected {self.NUM_CONTESTED_IDS} distinct rows, got "
            f"{len(ids_seen)} — last-write-wins semantics broken"
        )
