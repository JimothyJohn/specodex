"""Tests for ingest_log key/shape helpers and DynamoDB CRUD."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from specodex.db.dynamo import DynamoDBClient
from specodex.ingest_log import (
    MIN_RETRY_THRESHOLD,
    SCHEMA_VERSION,
    STATUS_EXTRACT_FAIL,
    STATUS_QUALITY_FAIL,
    STATUS_SUCCESS,
    build_record,
    pk_for_url,
    should_skip,
    url_hash,
)


def _make_client(mock_boto3: MagicMock) -> tuple[DynamoDBClient, MagicMock]:
    mock_table = MagicMock()
    mock_resource = MagicMock()
    mock_resource.Table.return_value = mock_table
    mock_boto3.resource.return_value = mock_resource
    return DynamoDBClient(table_name="products"), mock_table


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKeyHelpers:
    def test_url_hash_is_stable(self) -> None:
        assert url_hash("https://x.com/a.pdf") == url_hash("https://x.com/a.pdf")

    def test_url_hash_varies(self) -> None:
        assert url_hash("https://x.com/a.pdf") != url_hash("https://x.com/b.pdf")

    def test_pk_format(self) -> None:
        pk = pk_for_url("https://x.com/a.pdf")
        assert pk.startswith("INGEST#")
        assert len(pk) == len("INGEST#") + 16


# ---------------------------------------------------------------------------
# build_record
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildRecord:
    def test_minimal_record(self) -> None:
        r = build_record(
            url="https://x.com/a.pdf",
            manufacturer="Acme",
            product_type="motor",
            status=STATUS_EXTRACT_FAIL,
        )
        assert r["PK"] == pk_for_url("https://x.com/a.pdf")
        assert r["SK"].startswith("INGEST#")
        assert r["status"] == STATUS_EXTRACT_FAIL
        assert r["schema_version"] == SCHEMA_VERSION
        assert r["products_extracted"] == 0
        assert r["fields_missing"] == []

    def test_rejects_unknown_status(self) -> None:
        with pytest.raises(ValueError):
            build_record(
                url="https://x.com/a.pdf",
                manufacturer="Acme",
                product_type="motor",
                status="bogus",
            )

    def test_missing_fields_deduped_and_sorted(self) -> None:
        r = build_record(
            url="https://x.com/a.pdf",
            manufacturer="Acme",
            product_type="motor",
            status=STATUS_QUALITY_FAIL,
            fields_missing=["stroke", "rated_power", "stroke"],
        )
        assert r["fields_missing"] == ["rated_power", "stroke"]

    def test_hints_included_when_provided(self) -> None:
        r = build_record(
            url="https://x.com/a.pdf",
            manufacturer="Acme",
            product_type="motor",
            status=STATUS_SUCCESS,
            product_name_hint="M1",
            product_family_hint="M-series",
        )
        assert r["product_name_hint"] == "M1"
        assert r["product_family_hint"] == "M-series"


# ---------------------------------------------------------------------------
# should_skip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShouldSkip:
    def test_none_means_run(self) -> None:
        assert should_skip(None) is False

    def test_success_skips(self) -> None:
        assert should_skip({"status": STATUS_SUCCESS}) is True

    def test_extract_fail_retries(self) -> None:
        assert should_skip({"status": STATUS_EXTRACT_FAIL}) is False

    def test_quality_fail_above_threshold_skips(self) -> None:
        # filled / total = 0.5, threshold is 0.25 → skip (not worth re-run)
        rec = {
            "status": STATUS_QUALITY_FAIL,
            "fields_filled_avg": 15,
            "fields_total": 30,
        }
        assert should_skip(rec) is True

    def test_quality_fail_below_threshold_retries(self) -> None:
        # filled / total = 0.1, threshold is 0.25 → retry
        rec = {
            "status": STATUS_QUALITY_FAIL,
            "fields_filled_avg": 3,
            "fields_total": 30,
        }
        assert should_skip(rec) is False

    def test_threshold_exactly(self) -> None:
        # == threshold should skip (>=)
        filled = MIN_RETRY_THRESHOLD * 100
        rec = {
            "status": STATUS_QUALITY_FAIL,
            "fields_filled_avg": filled,
            "fields_total": 100,
        }
        assert should_skip(rec) is True


@pytest.mark.unit
class TestShouldSkipMalformedRecord:
    """Regression cases for the malformed-record path on quality_fail.
    Caught by ``tests/unit/test_ingest_log_property.py`` (hypothesis
    surfaced non-numeric strings landing in ``fields_filled_avg`` /
    ``fields_total`` — the prior ``int()`` / ``float()`` coercion raised
    ValueError before the function could decide). A leaked exception
    here crashes ``scraper.process_datasheet`` on the first read of the
    bad row, so the safe-retry fallback is also the safe-recovery path.
    """

    def test_non_numeric_fields_filled_avg_retries(self) -> None:
        rec = {
            "status": STATUS_QUALITY_FAIL,
            "fields_filled_avg": ":",  # the exact shape hypothesis caught
            "fields_total": None,
        }
        assert should_skip(rec) is False

    def test_non_numeric_fields_total_retries(self) -> None:
        rec = {
            "status": STATUS_QUALITY_FAIL,
            "fields_filled_avg": 10.0,
            "fields_total": "not-a-number",
        }
        assert should_skip(rec) is False

    def test_both_numeric_slots_malformed_retries(self) -> None:
        rec = {
            "status": STATUS_QUALITY_FAIL,
            "fields_filled_avg": "x",
            "fields_total": "y",
        }
        assert should_skip(rec) is False


# ---------------------------------------------------------------------------
# DynamoDBClient ingest CRUD
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDynamoIngestCrud:
    @patch("specodex.db.dynamo.boto3")
    def test_write_ingest_puts_item(self, mock_boto3: MagicMock) -> None:
        client, table = _make_client(mock_boto3)
        r = build_record(
            url="https://x.com/a.pdf",
            manufacturer="Acme",
            product_type="motor",
            status=STATUS_SUCCESS,
            fields_filled_avg=7.5,  # float — must be decimal-coerced
        )
        assert client.write_ingest(r) is True
        table.put_item.assert_called_once()
        item = table.put_item.call_args.kwargs["Item"]
        # Float → Decimal conversion
        from decimal import Decimal

        assert isinstance(item["fields_filled_avg"], Decimal)

    @patch("specodex.db.dynamo.boto3")
    def test_write_ingest_swallows_errors(self, mock_boto3: MagicMock) -> None:
        client, table = _make_client(mock_boto3)
        table.put_item.side_effect = RuntimeError("boom")
        r = build_record(
            url="https://x.com/a.pdf",
            manufacturer="Acme",
            product_type="motor",
            status=STATUS_SUCCESS,
        )
        # Best-effort: must not raise
        assert client.write_ingest(r) is False

    @patch("specodex.db.dynamo.boto3")
    def test_read_ingest_queries_latest(self, mock_boto3: MagicMock) -> None:
        client, table = _make_client(mock_boto3)
        table.query.return_value = {"Items": [{"SK": "INGEST#2026-04-24T00:00:00Z"}]}
        got = client.read_ingest("https://x.com/a.pdf")
        assert got == {"SK": "INGEST#2026-04-24T00:00:00Z"}
        kwargs = table.query.call_args.kwargs
        assert kwargs["ScanIndexForward"] is False
        assert kwargs["Limit"] == 1
        assert kwargs["ExpressionAttributeValues"][":pk"] == pk_for_url(
            "https://x.com/a.pdf"
        )

    @patch("specodex.db.dynamo.boto3")
    def test_read_ingest_empty(self, mock_boto3: MagicMock) -> None:
        client, table = _make_client(mock_boto3)
        table.query.return_value = {"Items": []}
        assert client.read_ingest("https://x.com/a.pdf") is None

    @patch("specodex.db.dynamo.boto3")
    def test_list_ingest_filters(self, mock_boto3: MagicMock) -> None:
        client, table = _make_client(mock_boto3)
        table.scan.return_value = {"Items": [{"manufacturer": "Acme"}]}
        items = client.list_ingest(
            manufacturer="Acme", status=STATUS_QUALITY_FAIL, since="2026-04-01"
        )
        assert items == [{"manufacturer": "Acme"}]
        kwargs = table.scan.call_args.kwargs
        expr = kwargs["FilterExpression"]
        assert "manufacturer = :mfg" in expr
        assert "#st = :status" in expr
        assert "SK >= :since_sk" in expr
        assert kwargs["ExpressionAttributeValues"][":mfg"] == "Acme"
        assert kwargs["ExpressionAttributeValues"][":since_sk"] == "INGEST#2026-04-01"
