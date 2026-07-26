"""Tests for the triage module — identifies low-quality products and copies datasheets."""

from unittest.mock import MagicMock, patch

import pytest

from specodex.models.motor import Motor

from cli.triage import (
    _copy_to_triage,
    _find_pdf_by_url,
    _s3_key_from_url,
    find_triage_candidates,
    triage_datasheets,
)

MFG = "TestMfg"


@pytest.mark.unit
class TestS3KeyFromUrl:
    def test_s3_url(self):
        result = _s3_key_from_url("s3://my-bucket/queue/abc/file.pdf")
        assert result == ("my-bucket", "queue/abc/file.pdf")

    def test_s3_url_nested(self):
        result = _s3_key_from_url("s3://bucket/done/deep/path/f.pdf")
        assert result == ("bucket", "done/deep/path/f.pdf")

    def test_http_url_returns_none(self):
        assert _s3_key_from_url("https://example.com/datasheet.pdf") is None

    def test_empty_string_returns_none(self):
        assert _s3_key_from_url("") is None


@pytest.mark.unit
class TestCopyToTriage:
    def test_copies_to_triage_prefix(self):
        s3 = MagicMock()
        dest_key = _copy_to_triage(
            s3, "src-bucket", "done/abc/motor.pdf", "dest-bucket"
        )
        assert dest_key == "triage/motor.pdf"
        s3.copy_object.assert_called_once_with(
            Bucket="dest-bucket",
            CopySource={"Bucket": "src-bucket", "Key": "done/abc/motor.pdf"},
            Key="triage/motor.pdf",
        )

    def test_preserves_filename_only(self):
        s3 = MagicMock()
        dest_key = _copy_to_triage(s3, "b", "raw_pdfs/deep/nested/file.pdf", "b")
        assert dest_key == "triage/file.pdf"


@pytest.mark.unit
class TestFindPdfByUrl:
    def test_matches_done_prefix(self):
        s3 = MagicMock()
        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.side_effect = [
            [{"Contents": [{"Key": "done/motor_datasheet.pdf"}]}],
            [],
        ]
        result = _find_pdf_by_url(
            s3, "bucket", "https://example.com/motor_datasheet.pdf"
        )
        assert result == "done/motor_datasheet.pdf"

    def test_matches_raw_pdfs_prefix(self):
        s3 = MagicMock()
        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.side_effect = [
            [{"Contents": []}],  # done/ has nothing
            [{"Contents": [{"Key": "raw_pdfs/motor_datasheet.pdf"}]}],
        ]
        result = _find_pdf_by_url(
            s3, "bucket", "https://example.com/motor_datasheet.pdf"
        )
        assert result == "raw_pdfs/motor_datasheet.pdf"

    def test_no_match_returns_none(self):
        s3 = MagicMock()
        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{"Contents": []}]
        result = _find_pdf_by_url(s3, "bucket", "https://example.com/missing.pdf")
        assert result is None

    def test_non_pdf_url_returns_none(self):
        s3 = MagicMock()
        result = _find_pdf_by_url(s3, "bucket", "https://example.com/page.html")
        assert result is None

    def test_case_insensitive_match(self):
        s3 = MagicMock()
        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.side_effect = [
            [{"Contents": [{"Key": "done/Motor_Datasheet.PDF"}]}],
            [],
        ]
        result = _find_pdf_by_url(
            s3, "bucket", "https://example.com/motor_datasheet.pdf"
        )
        assert result == "done/Motor_Datasheet.PDF"


def _make_motor(name: str, score_fields: dict | None = None, **kwargs) -> Motor:
    """Create a Motor with optional spec fields filled."""
    defaults = {"product_name": name, "product_type": "motor", "manufacturer": MFG}
    defaults.update(kwargs)
    if score_fields:
        defaults.update(score_fields)
    return Motor(**defaults)


MOTOR_ONLY_SCHEMAS = {"motor": Motor}


@pytest.mark.unit
class TestFindTriageCandidates:
    @patch("cli.triage.SCHEMA_CHOICES", MOTOR_ONLY_SCHEMAS)
    @patch("cli.triage._get_dynamo")
    def test_finds_low_quality_products(self, mock_dynamo):
        empty_motor = _make_motor("Empty")
        good_motor = _make_motor(
            "Good",
            score_fields={
                "rated_voltage": "200-240;V",
                "rated_speed": "3000;rpm",
                "rated_torque": "2.5;Nm",
                "rated_power": "750;W",
                "rated_current": "3;A",
                "peak_current": "6;A",
                "part_number": "MTR-001",
                "poles": 8,
                "type": "brushless dc",
                "series": "X",
                "ip_rating": 65,
                "peak_torque": "5;Nm",
                "max_speed": "4000;rpm",
                "voltage_constant": "0.1;V/krpm",
                "torque_constant": "0.5;Nm/A",
                "resistance": "1.2;ohm",
                "inductance": "5;mH",
                "rotor_inertia": "0.5;kg*cm2",
                "weight": "2.5;kg",
                "release_year": 2024,
            },
        )

        db = MagicMock()
        db.list.return_value = [empty_motor, good_motor]
        mock_dynamo.return_value = db

        candidates = find_triage_candidates(threshold=0.5)
        names = [c[0].product_name for c in candidates]
        assert "Empty" in names
        assert "Good" not in names

    @patch("cli.triage.SCHEMA_CHOICES", MOTOR_ONLY_SCHEMAS)
    @patch("cli.triage._get_dynamo")
    def test_threshold_controls_sensitivity(self, mock_dynamo):
        motor = _make_motor(
            "Partial",
            score_fields={
                "rated_voltage": "200-240;V",
                "rated_speed": "3000;rpm",
                "rated_torque": "2.5;Nm",
            },
        )
        db = MagicMock()
        db.list.return_value = [motor]
        mock_dynamo.return_value = db

        # Low threshold — should not flag. threshold=0.05 rather than 0.1:
        # `total` (the score denominator) grows whenever Motor gains a new
        # spec field, so a threshold pinned just above this fixture's score
        # is a magic-number trap, not a real regression check.
        candidates = find_triage_candidates(threshold=0.05)
        assert len(candidates) == 0

        # High threshold — should flag
        candidates = find_triage_candidates(threshold=0.9)
        assert len(candidates) > 0

    @patch("cli.triage.SCHEMA_CHOICES", MOTOR_ONLY_SCHEMAS)
    @patch("cli.triage._get_dynamo")
    def test_empty_table_returns_empty(self, mock_dynamo):
        db = MagicMock()
        db.list.return_value = []
        mock_dynamo.return_value = db

        candidates = find_triage_candidates(threshold=0.5)
        assert candidates == []


@pytest.mark.unit
@patch("cli.triage.SCHEMA_CHOICES", MOTOR_ONLY_SCHEMAS)
class TestTriageDatasheets:
    @patch("cli.triage._get_s3")
    @patch("cli.triage._get_dynamo")
    @patch("cli.triage._resolve_bucket", return_value="test-bucket")
    def test_copies_s3_datasheet_to_triage(self, mock_bucket, mock_dynamo, mock_s3):
        motor = _make_motor(
            "LowQ",
            datasheet_url="s3://test-bucket/done/abc/motor.pdf",
        )
        db = MagicMock()
        db.list.return_value = [motor]
        mock_dynamo.return_value = db

        s3 = MagicMock()
        mock_s3.return_value = s3

        results = triage_datasheets(threshold=0.5, bucket="test-bucket")
        assert len(results) == 1
        assert results[0]["triaged"] is True
        assert results[0]["dest_key"] == "triage/motor.pdf"
        s3.copy_object.assert_called_once()

    @patch("cli.triage._get_s3")
    @patch("cli.triage._get_dynamo")
    @patch("cli.triage._resolve_bucket", return_value="test-bucket")
    def test_dry_run_does_not_copy(self, mock_bucket, mock_dynamo, mock_s3):
        motor = _make_motor(
            "LowQ",
            datasheet_url="s3://test-bucket/done/motor.pdf",
        )
        db = MagicMock()
        db.list.return_value = [motor]
        mock_dynamo.return_value = db

        s3 = MagicMock()
        mock_s3.return_value = s3

        results = triage_datasheets(threshold=0.5, bucket="test-bucket", dry_run=True)
        assert results[0]["triaged"] is False
        assert results[0]["reason"] == "dry_run"
        s3.copy_object.assert_not_called()

    @patch("cli.triage._get_s3")
    @patch("cli.triage._get_dynamo")
    @patch("cli.triage._resolve_bucket", return_value="test-bucket")
    def test_no_datasheet_url_logged(self, mock_bucket, mock_dynamo, mock_s3):
        motor = _make_motor("NoURL")
        db = MagicMock()
        db.list.return_value = [motor]
        mock_dynamo.return_value = db

        s3 = MagicMock()
        mock_s3.return_value = s3

        results = triage_datasheets(threshold=0.5, bucket="test-bucket")
        assert results[0]["triaged"] is False
        assert results[0]["reason"] == "no_datasheet_url"

    @patch("cli.triage._find_pdf_by_url")
    @patch("cli.triage._get_s3")
    @patch("cli.triage._get_dynamo")
    @patch("cli.triage._resolve_bucket", return_value="test-bucket")
    def test_external_url_with_s3_match(
        self, mock_bucket, mock_dynamo, mock_s3, mock_find
    ):
        motor = _make_motor(
            "ExtURL",
            datasheet_url="https://example.com/motor.pdf",
        )
        db = MagicMock()
        db.list.return_value = [motor]
        mock_dynamo.return_value = db

        s3 = MagicMock()
        mock_s3.return_value = s3
        mock_find.return_value = "done/motor.pdf"

        results = triage_datasheets(threshold=0.5, bucket="test-bucket")
        assert results[0]["triaged"] is True
        assert results[0]["dest_key"] == "triage/motor.pdf"

    @patch("cli.triage._find_pdf_by_url")
    @patch("cli.triage._get_s3")
    @patch("cli.triage._get_dynamo")
    @patch("cli.triage._resolve_bucket", return_value="test-bucket")
    def test_external_url_no_match(self, mock_bucket, mock_dynamo, mock_s3, mock_find):
        motor = _make_motor(
            "ExtNoMatch",
            datasheet_url="https://example.com/missing.pdf",
        )
        db = MagicMock()
        db.list.return_value = [motor]
        mock_dynamo.return_value = db

        s3 = MagicMock()
        mock_s3.return_value = s3
        mock_find.return_value = None

        results = triage_datasheets(threshold=0.5, bucket="test-bucket")
        assert results[0]["triaged"] is False
        assert results[0]["reason"] == "external_url_no_s3_match"

    @patch("cli.triage._get_s3")
    @patch("cli.triage._get_dynamo")
    @patch("cli.triage._resolve_bucket", return_value="test-bucket")
    def test_copy_failure_handled(self, mock_bucket, mock_dynamo, mock_s3):
        motor = _make_motor(
            "FailCopy",
            datasheet_url="s3://test-bucket/done/fail.pdf",
        )
        db = MagicMock()
        db.list.return_value = [motor]
        mock_dynamo.return_value = db

        s3 = MagicMock()
        s3.copy_object.side_effect = Exception("Access Denied")
        mock_s3.return_value = s3

        results = triage_datasheets(threshold=0.5, bucket="test-bucket")
        assert results[0]["triaged"] is False
        assert "copy_failed" in results[0]["reason"]
