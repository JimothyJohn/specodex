"""Tests for ``specodex.mounting.extract``.

``_call_gemini`` is mocked throughout — these tests exercise the schema
construction, page-number remapping, and empty/error handling around the
actual network call, not the Gemini API itself.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from specodex.mounting.extract import (
    MotorMountingExtraction,
    extract_mounting_dimensions,
)


def _fake_response(payload) -> MagicMock:
    response = MagicMock()
    response.text = json.dumps(payload)
    return response


@pytest.mark.unit
class TestExtractMountingDimensions:
    def test_empty_page_images_returns_empty_without_calling_gemini(self) -> None:
        with patch("specodex.mounting.extract._call_gemini") as mock_call:
            result = extract_mounting_dimensions([], [], api_key="fake")
        assert result == []
        mock_call.assert_not_called()

    def test_mismatched_lengths_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_mounting_dimensions([b"x"], [1, 2], api_key="fake")

    def test_remaps_page_number_to_true_pdf_page(self) -> None:
        payload = [
            {
                "frame_designator": "NEMA 23",
                "mounting_flange": {
                    "bolt_circle_diameter": {"value": 63, "unit": "mm"},
                    "bolt_hole_count": 4,
                    "mounting_standard": "NEMA",
                },
                "shaft_diameter": {"value": 8, "unit": "mm"},
                "shaft_length": {"value": 20, "unit": "mm"},
                "shaft_modification": ["keyway"],
                "page_number": 2,
            }
        ]
        with patch(
            "specodex.mounting.extract._call_gemini",
            return_value=_fake_response(payload),
        ):
            results = extract_mounting_dimensions(
                [b"img1", b"img2"], [5, 9], api_key="fake"
            )
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, MotorMountingExtraction)
        assert r.frame_designator == "NEMA 23"
        assert r.page_number == 9  # 1-indexed "2" -> page_numbers[1] == 9
        assert r.mounting_flange.bolt_circle_diameter.value == 63.0
        assert r.shaft_modification == ["keyway"]

    def test_out_of_range_page_number_is_dropped_not_raised(self) -> None:
        payload = [
            {"frame_designator": "ghost", "page_number": 99},
            {"frame_designator": "NEMA 34", "page_number": 1},
        ]
        with patch(
            "specodex.mounting.extract._call_gemini",
            return_value=_fake_response(payload),
        ):
            results = extract_mounting_dimensions([b"img1"], [3], api_key="fake")
        assert len(results) == 1
        assert results[0].frame_designator == "NEMA 34"
        assert results[0].page_number == 3

    def test_empty_llm_array_returns_empty_list_not_error(self) -> None:
        with patch(
            "specodex.mounting.extract._call_gemini",
            return_value=_fake_response([]),
        ):
            results = extract_mounting_dimensions([b"img1"], [0], api_key="fake")
        assert results == []

    def test_multiple_variants_on_one_page(self) -> None:
        payload = [
            {"frame_designator": "NEMA 23", "page_number": 1},
            {"frame_designator": "NEMA 34", "page_number": 1},
        ]
        with patch(
            "specodex.mounting.extract._call_gemini",
            return_value=_fake_response(payload),
        ):
            results = extract_mounting_dimensions([b"img1"], [7], api_key="fake")
        assert len(results) == 2
        assert all(r.page_number == 7 for r in results)
        assert {r.frame_designator for r in results} == {"NEMA 23", "NEMA 34"}

    def test_malformed_row_is_skipped_not_fatal(self) -> None:
        # bolt_hole_count must be an int; a garbage string degrades that
        # ONE row's validation, not the whole call, per parse_gemini_response's
        # per-row error handling.
        payload = [
            {
                "frame_designator": "bad",
                "mounting_flange": {"bolt_hole_count": "many"},
                "page_number": 1,
            },
            {"frame_designator": "good", "page_number": 1},
        ]
        with patch(
            "specodex.mounting.extract._call_gemini",
            return_value=_fake_response(payload),
        ):
            results = extract_mounting_dimensions([b"img1"], [0], api_key="fake")
        assert len(results) == 1
        assert results[0].frame_designator == "good"
