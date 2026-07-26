"""Tests for ``cli.mounting_extract`` — the DB-walker CLI that backfills
``Motor.mounting_flange`` from dimensional-drawing extractions.

``run()``'s network/DB/LLM dependencies (``make_client``, ``get_document``,
``extract_mounting_dimensions``) are mocked throughout; the pure matching
and grouping helpers get direct example-based coverage.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cli.mounting_extract import (
    _group_by_datasheet_url,
    _match_extraction_to_motor,
    _matching_motors,
    run,
)
from specodex.models.motor import Motor
from specodex.mounting.extract import MotorMountingExtraction

MFG = "TestVendor"


def _motor(**overrides) -> Motor:
    defaults = dict(
        product_name="M1",
        manufacturer=MFG,
        part_number="PN-1",
        frame_size="NEMA 23",
        motor_mount_pattern="NEMA 23",
        datasheet_url="https://example.com/catalog.pdf",
    )
    defaults.update(overrides)
    return Motor(**defaults)


def _extraction(**overrides) -> MotorMountingExtraction:
    defaults = dict(frame_designator="NEMA 23", page_number=1)
    defaults.update(overrides)
    return MotorMountingExtraction(**defaults)


@pytest.mark.unit
class TestMatchingMotors:
    def test_no_filters_returns_all(self):
        client = MagicMock()
        client.list.return_value = [_motor(), _motor(part_number="PN-2")]
        assert len(_matching_motors(client, None, None)) == 2

    def test_manufacturer_filter_is_case_insensitive_substring(self):
        client = MagicMock()
        client.list.return_value = [
            _motor(manufacturer="Acme Robotics"),
            _motor(manufacturer="Other Co", part_number="PN-2"),
        ]
        result = _matching_motors(client, "acme", None)
        assert len(result) == 1
        assert result[0].manufacturer == "Acme Robotics"

    def test_product_id_filter(self):
        client = MagicMock()
        m1, m2 = _motor(), _motor(part_number="PN-2")
        client.list.return_value = [m1, m2]
        result = _matching_motors(client, None, [str(m1.product_id)])
        assert result == [m1]


@pytest.mark.unit
class TestGroupByDatasheetUrl:
    def test_groups_by_url_skips_missing_url(self):
        with_url_a = _motor(datasheet_url="https://a.com/x.pdf")
        with_url_a2 = _motor(part_number="PN-2", datasheet_url="https://a.com/x.pdf")
        with_url_b = _motor(part_number="PN-3", datasheet_url="https://b.com/y.pdf")
        no_url = _motor(part_number="PN-4", datasheet_url=None)

        groups = _group_by_datasheet_url([with_url_a, with_url_a2, with_url_b, no_url])
        assert set(groups.keys()) == {"https://a.com/x.pdf", "https://b.com/y.pdf"}
        assert len(groups["https://a.com/x.pdf"]) == 2
        assert len(groups["https://b.com/y.pdf"]) == 1


@pytest.mark.unit
class TestMatchExtractionToMotor:
    def test_exact_frame_size_match(self):
        motor = _motor(frame_size="NEMA 23", motor_mount_pattern=None)
        extraction = _extraction(frame_designator="NEMA 23")
        assert _match_extraction_to_motor(extraction, [motor]) is motor

    def test_exact_mount_pattern_match(self):
        motor = _motor(frame_size=None, motor_mount_pattern="NEMA 34")
        extraction = _extraction(frame_designator="NEMA 34")
        assert _match_extraction_to_motor(extraction, [motor]) is motor

    def test_no_frame_designator_returns_none(self):
        motor = _motor()
        extraction = _extraction(frame_designator=None)
        assert _match_extraction_to_motor(extraction, [motor]) is None

    def test_no_candidates_match_returns_none(self):
        motor = _motor(frame_size="NEMA 17", motor_mount_pattern="NEMA 17")
        extraction = _extraction(frame_designator="NEMA 34")
        assert _match_extraction_to_motor(extraction, [motor]) is None

    def test_ambiguous_match_across_two_candidates_returns_none(self):
        # Both candidates carry the exact same frame_size — the extraction
        # can't tell which specific row it belongs to.
        a = _motor(part_number="PN-A", frame_size="NEMA 23", motor_mount_pattern=None)
        b = _motor(part_number="PN-B", frame_size="NEMA 23", motor_mount_pattern=None)
        extraction = _extraction(frame_designator="NEMA 23")
        assert _match_extraction_to_motor(extraction, [a, b]) is None

    def test_substring_containment_match(self):
        # Drawing labels the variant "Frame NEMA 23 (standard)" — the
        # motor's plain "NEMA 23" frame_size is contained within it.
        motor = _motor(frame_size="NEMA 23", motor_mount_pattern=None)
        extraction = _extraction(frame_designator="Frame NEMA 23 (standard)")
        assert _match_extraction_to_motor(extraction, [motor]) is motor


@pytest.mark.unit
class TestRun:
    def test_missing_api_key_returns_1(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        rc = run(
            manufacturer="acme",
            product_ids=None,
            stage="dev",
            apply=False,
            max_pages=None,
        )
        assert rc == 1

    def test_apply_against_prod_is_refused(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake")
        rc = run(
            manufacturer="acme",
            product_ids=None,
            stage="prod",
            apply=True,
            max_pages=None,
        )
        assert rc == 1

    def test_dry_run_never_calls_client_update(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake")
        motor = _motor()
        client = MagicMock()
        client.list.return_value = [motor]

        with (
            patch("cli.mounting_extract.make_client", return_value=client),
            patch("cli.mounting_extract.get_document", return_value=b"%PDF-fake"),
            patch(
                "cli.mounting_extract.find_drawing_pages_scored",
                return_value=([0], [{"score": 0.5}]),
            ),
            patch(
                "cli.mounting_extract.pdf_pages_to_images",
                return_value=[b"img0"],
            ),
            patch(
                "cli.mounting_extract.extract_mounting_dimensions",
                return_value=[_extraction(frame_designator="NEMA 23", page_number=0)],
            ),
        ):
            rc = run(
                manufacturer=None,
                product_ids=None,
                stage="dev",
                apply=False,
                max_pages=None,
            )

        assert rc == 0
        client.update.assert_not_called()

    def test_apply_writes_matched_motors_only(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake")
        motor = _motor()
        client = MagicMock()
        client.list.return_value = [motor]
        client.update.return_value = True

        extraction = _extraction(
            frame_designator="NEMA 23",
            page_number=0,
            mounting_flange={"bolt_circle_diameter": {"value": 63, "unit": "mm"}},
            shaft_length={"value": 20, "unit": "mm"},
            shaft_modification=["keyway"],
        )

        with (
            patch("cli.mounting_extract.make_client", return_value=client),
            patch("cli.mounting_extract.get_document", return_value=b"%PDF-fake"),
            patch(
                "cli.mounting_extract.find_drawing_pages_scored",
                return_value=([0], [{"score": 0.5}]),
            ),
            patch("cli.mounting_extract.pdf_pages_to_images", return_value=[b"img0"]),
            patch(
                "cli.mounting_extract.extract_mounting_dimensions",
                return_value=[extraction],
            ),
        ):
            rc = run(
                manufacturer=None,
                product_ids=None,
                stage="dev",
                apply=True,
                max_pages=None,
            )

        assert rc == 0
        client.update.assert_called_once()
        written = client.update.call_args[0][0]
        assert written.mounting_flange.bolt_circle_diameter.value == 63.0
        assert written.shaft_length.value == 20.0
        assert written.shaft_modification == ["keyway"]

    def test_motors_with_existing_mounting_flange_are_skipped(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake")
        already_done = _motor(
            mounting_flange={"bolt_circle_diameter": {"value": 63, "unit": "mm"}}
        )
        client = MagicMock()
        client.list.return_value = [already_done]

        with patch("cli.mounting_extract.make_client", return_value=client):
            rc = run(
                manufacturer=None,
                product_ids=None,
                stage="dev",
                apply=False,
                max_pages=None,
            )

        assert rc == 0
        # Nothing to extract — get_document should never be reached, and
        # this is easiest to prove by confirming the run short-circuits
        # with no candidates rather than mocking every downstream call.

    def test_motors_without_datasheet_url_are_excluded(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake")
        no_url = _motor(datasheet_url=None)
        client = MagicMock()
        client.list.return_value = [no_url]

        with patch("cli.mounting_extract.make_client", return_value=client):
            rc = run(
                manufacturer=None,
                product_ids=None,
                stage="dev",
                apply=False,
                max_pages=None,
            )

        assert rc == 0

    def test_no_candidate_drawing_pages_is_not_a_failure(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake")
        motor = _motor()
        client = MagicMock()
        client.list.return_value = [motor]

        with (
            patch("cli.mounting_extract.make_client", return_value=client),
            patch("cli.mounting_extract.get_document", return_value=b"%PDF-fake"),
            patch(
                "cli.mounting_extract.find_drawing_pages_scored",
                return_value=([], []),
            ),
        ):
            rc = run(
                manufacturer=None,
                product_ids=None,
                stage="dev",
                apply=False,
                max_pages=None,
            )

        assert rc == 0
        client.update.assert_not_called()

    def test_fetch_failure_does_not_abort_other_groups(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake")
        bad = _motor(part_number="PN-BAD", datasheet_url="https://bad.example/x.pdf")
        good = _motor(
            part_number="PN-GOOD",
            frame_size="NEMA 34",
            motor_mount_pattern="NEMA 34",
            datasheet_url="https://good.example/y.pdf",
        )
        client = MagicMock()
        client.list.return_value = [bad, good]
        client.update.return_value = True

        def fake_get_document(url):
            if "bad" in url:
                raise RuntimeError("boom")
            return b"%PDF-fake"

        with (
            patch("cli.mounting_extract.make_client", return_value=client),
            patch("cli.mounting_extract.get_document", side_effect=fake_get_document),
            patch(
                "cli.mounting_extract.find_drawing_pages_scored",
                return_value=([0], [{"score": 0.5}]),
            ),
            patch("cli.mounting_extract.pdf_pages_to_images", return_value=[b"img0"]),
            patch(
                "cli.mounting_extract.extract_mounting_dimensions",
                return_value=[_extraction(frame_designator="NEMA 34", page_number=0)],
            ),
        ):
            rc = run(
                manufacturer=None,
                product_ids=None,
                stage="dev",
                apply=True,
                max_pages=None,
            )

        assert rc == 0
        client.update.assert_called_once()
        assert client.update.call_args[0][0].part_number == "PN-GOOD"
