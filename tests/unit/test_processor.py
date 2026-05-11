"""Example-based tests for ``cli.processor`` helpers.

The property-test companion (``test_processor_property.py``) pins
the contract over adversarial input; this file pins specific
shapes from real-world S3 keys so a regression on any of these
is caught even if the Hypothesis strategy drifts.
"""

from __future__ import annotations

import pytest

from cli.processor import parse_datasheet_id_from_key


class TestParseDatasheetIdFromKey:
    """Happy-path and malformed-input shapes for the S3 key parser."""

    @pytest.mark.parametrize(
        "key,expected",
        [
            # Real upload-queue shapes.
            ("queue/abc-123/datasheet.pdf", "abc-123"),
            ("good_examples/ds-uuid/spec.pdf", "ds-uuid"),
            # Trailing slash on a folder-like key still resolves the id.
            ("queue/abc/", "abc"),
            ("good_examples/abc/sub/file.pdf", "abc"),
            # Three-segment minimum is met with empty filename.
            ("queue/abc/", "abc"),
        ],
    )
    def test_well_formed_keys_return_id(self, key: str, expected: str) -> None:
        assert parse_datasheet_id_from_key(key) == expected

    @pytest.mark.parametrize(
        "key",
        [
            # Too few segments — no id slot.
            "",
            "queue",
            "queue/abc",
            "good_examples/abc",
            # Empty id slot — caller would otherwise scan DynamoDB
            # with an empty datasheet_id and silently skip.
            "queue//file.pdf",
            "good_examples//file.pdf",
        ],
    )
    def test_malformed_keys_return_none(self, key: str) -> None:
        assert parse_datasheet_id_from_key(key) is None

    @pytest.mark.parametrize(
        "value",
        [None, 0, 1, True, False, 1.5, b"queue/abc/file.pdf", ["queue", "abc"], {}],
    )
    def test_non_string_input_returns_none(self, value) -> None:
        """Non-string inputs (None, bytes, list, dict, int, bool,
        float) must never raise — return None and let the caller
        skip.
        """
        assert parse_datasheet_id_from_key(value) is None  # type: ignore[arg-type]
