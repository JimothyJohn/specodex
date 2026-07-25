"""Tests for specodex.placeholders.is_placeholder."""

from __future__ import annotations

import pytest

from specodex.placeholders import PLACEHOLDER_STRINGS, is_placeholder


class TestIsPlaceholder:
    def test_none_is_placeholder(self) -> None:
        assert is_placeholder(None) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "N/A",
            "n/a",
            "NA",
            "TBD",
            "tbd",
            "-",
            "--",
            "None",
            "none",
            "NULL",
            "?",
            "unknown",
            "not available",
            "Not Applicable",
            "not specified",
            # Regression: real Gemini output, Nidec ABLE VR run 2026-07-24.
            "unset",
            "Unset",
        ],
    )
    def test_known_placeholder_strings(self, value: str) -> None:
        assert is_placeholder(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "  N/A  ",
            "\tTBD\n",
            "  -  ",
        ],
    )
    def test_whitespace_is_stripped(self, value: str) -> None:
        assert is_placeholder(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "Maxon",
            "100",
            "0",
            "EC-45",
            "N/A motors",  # embedded, not exact
            "na-12345",  # part number substring collision
            "tba-01",
        ],
    )
    def test_real_values_are_not_placeholder(self, value: str) -> None:
        assert is_placeholder(value) is False

    def test_non_string_non_none_values_are_not_placeholder(self) -> None:
        assert is_placeholder(0) is False
        assert is_placeholder(0.0) is False
        assert is_placeholder(False) is False
        assert is_placeholder([]) is False
        assert is_placeholder({}) is False
        assert is_placeholder({"value": 100}) is False

    def test_all_canonical_placeholders_accepted(self) -> None:
        """Smoke: every entry in PLACEHOLDER_STRINGS must match."""
        for tok in PLACEHOLDER_STRINGS:
            assert is_placeholder(tok) is True, f"{tok!r} failed"
