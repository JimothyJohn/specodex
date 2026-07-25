"""Regression tests for the DynamoDB point-lookup miss bug class.

DynamoDB applies ``Limit`` before ``FilterExpression``, so a filtered
scan/query with ``Limit=1`` examines one arbitrary item and misses the
target on any populated table. Filtered scans without a pagination loop
have the sibling failure: a match past the first 1 MB page is never seen.

Each test seeds a table where the target row is NOT the first item
examined and asserts the lookup still finds it. All of these failed
before the fix (only the one row that happened to be scanned/queried
first was ever findable).
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import boto3
import moto
import pytest
from boto3.dynamodb.conditions import Attr

from specodex.db.lookups import query_first_match, scan_first_match

TABLE = "products"


@pytest.fixture()
def table() -> Iterator[Any]:
    with moto.mock_aws():
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["DYNAMODB_TABLE_NAME"] = TABLE
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName=TABLE,
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
        yield dynamodb.Table(TABLE)


def _seed_datasheets(table: Any, n: int = 10, status: str = "approved") -> list[str]:
    ids = [f"ds-{i:03d}" for i in range(n)]
    for i, ds_id in enumerate(ids):
        table.put_item(
            Item={
                "PK": f"DATASHEET#{ds_id}",
                "SK": "METADATA",
                "datasheet_id": ds_id,
                "status": status,
                "s3_key": f"good_examples/{ds_id}/file-{i}.pdf",
                "url": f"https://example.com/{ds_id}.pdf",
                "content_hash": f"hash-{ds_id}",
                "product_type": "motor",
            }
        )
    return ids


def _seed_filler(table: Any, n: int = 4) -> None:
    """Non-matching bulk rows that push matches past the first 1 MB scan page."""
    filler = "x" * 390_000
    for i in range(n):
        table.put_item(Item={"PK": f"FILLER#{i}", "SK": "METADATA", "blob": filler})


@pytest.mark.unit
class TestHelpers:
    def test_scan_first_match_rejects_limit(self, table: Any) -> None:
        with pytest.raises(ValueError, match="Limit"):
            scan_first_match(table, FilterExpression=Attr("x").eq(1), Limit=1)

    def test_query_first_match_rejects_limit(self, table: Any) -> None:
        with pytest.raises(ValueError, match="Limit"):
            query_first_match(
                table,
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": "P"},
                Limit=1,
            )

    def test_scan_first_match_paginates_past_empty_pages(self, table: Any) -> None:
        _seed_filler(table)
        ids = _seed_datasheets(table, n=3)
        for ds_id in ids:
            item = scan_first_match(
                table, FilterExpression=Attr("datasheet_id").eq(ds_id)
            )
            assert item is not None, f"{ds_id} not found past the first scan page"
            assert item["datasheet_id"] == ds_id

    def test_scan_first_match_returns_none_when_absent(self, table: Any) -> None:
        _seed_datasheets(table, n=3)
        assert (
            scan_first_match(table, FilterExpression=Attr("datasheet_id").eq("nope"))
            is None
        )


@pytest.mark.unit
class TestProcessorLookups:
    """cli/processor.py: every queued datasheet must be findable, not just one."""

    def test_find_datasheet_record_finds_every_seeded_id(self, table: Any) -> None:
        from cli.processor import find_datasheet_record

        ids = _seed_datasheets(table, n=10)
        for ds_id in ids:
            record = find_datasheet_record(None, ds_id)
            assert record is not None, f"processor missed queued datasheet {ds_id}"
            assert record["datasheet_id"] == ds_id

    def test_update_datasheet_status_updates_every_row(self, table: Any) -> None:
        from cli.processor import update_datasheet_status

        ids = _seed_datasheets(table, n=10, status="queued")
        for ds_id in ids:
            update_datasheet_status(ds_id, "processed")
        for ds_id in ids:
            item = table.get_item(Key={"PK": f"DATASHEET#{ds_id}", "SK": "METADATA"})[
                "Item"
            ]
            assert item["status"] == "processed", f"{ds_id} status never transitioned"


@pytest.mark.unit
class TestAgentLookups:
    def test_update_datasheet_status_updates_every_row(self, table: Any) -> None:
        from cli.agent import _update_datasheet_status

        ids = _seed_datasheets(table, n=10, status="queued")
        for ds_id in ids:
            _update_datasheet_status(table, ds_id, "processed")
        for ds_id in ids:
            item = table.get_item(Key={"PK": f"DATASHEET#{ds_id}", "SK": "METADATA"})[
                "Item"
            ]
            assert item["status"] == "processed", f"{ds_id} status never transitioned"

    def test_lookup_by_s3_key_paginates_past_first_page(self, table: Any) -> None:
        from cli.agent import _lookup_datasheet_by_s3_key

        _seed_filler(table)
        ids = _seed_datasheets(table, n=3)
        for i, ds_id in enumerate(ids):
            record = _lookup_datasheet_by_s3_key(f"good_examples/{ds_id}/file-{i}.pdf")
            assert record is not None, f"s3_key lookup missed {ds_id} past page one"
            assert record["datasheet_id"] == ds_id


@pytest.mark.unit
class TestIntakeDedupLookups:
    """cli/intake.py: dedup misses re-ingest the same PDF as a fresh datasheet."""

    def test_find_by_content_hash_paginates(self, table: Any) -> None:
        from cli.intake import _find_by_content_hash

        _seed_filler(table)
        ids = _seed_datasheets(table, n=3)
        for ds_id in ids:
            record = _find_by_content_hash(None, f"hash-{ds_id}")
            assert record is not None, f"content-hash dedup missed {ds_id}"
            assert record["datasheet_id"] == ds_id

    def test_find_by_url_paginates(self, table: Any) -> None:
        from cli.intake import _find_by_url

        _seed_filler(table)
        ids = _seed_datasheets(table, n=3)
        for ds_id in ids:
            record = _find_by_url(f"https://example.com/{ds_id}.pdf")
            assert record is not None, f"url dedup missed {ds_id}"
            assert record["datasheet_id"] == ds_id


@pytest.mark.unit
class TestDynamoDBClientLookups:
    def test_product_exists_finds_every_row_in_partition(self, table: Any) -> None:
        from specodex.db.dynamo import DynamoDBClient
        from specodex.models.motor import Motor

        for i in range(10):
            table.put_item(
                Item={
                    "PK": "PRODUCT#MOTOR",
                    "SK": f"PRODUCT#{i:03d}",
                    "product_type": "motor",
                    "manufacturer": "Acme",
                    "product_name": f"Motor-{i:03d}",
                }
            )
        client = DynamoDBClient(table_name=TABLE)
        for i in range(10):
            assert client.product_exists("motor", "Acme", f"Motor-{i:03d}", Motor), (
                f"product_exists missed Motor-{i:03d} — scraper would re-ingest it"
            )

    def test_datasheet_exists_finds_every_url(self, table: Any) -> None:
        from specodex.db.dynamo import DynamoDBClient

        ids = _seed_datasheets(table, n=10)
        client = DynamoDBClient(table_name=TABLE)
        for ds_id in ids:
            assert client.datasheet_exists(f"https://example.com/{ds_id}.pdf"), (
                f"datasheet_exists missed {ds_id}"
            )
