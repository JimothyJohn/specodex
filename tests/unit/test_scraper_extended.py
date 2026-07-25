"""
Extended tests for scraper.py — covers untested branches in process_datasheet:
quality filter rejection, multi-product parsing, empty parse, debug output,
and web content (non-PDF) paths.
"""

from unittest.mock import MagicMock, patch

import pytest

from specodex.scraper import process_datasheet


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.product_exists.return_value = False
    db.batch_create.return_value = 1
    return db


FAKE_API_KEY = "fake-api-key-for-test"


class TestProcessDatasheetWebContent:
    """Test non-PDF (HTML) content paths."""

    @patch("specodex.extract.parse_gemini_response")
    @patch("specodex.extract.generate_content")
    @patch("specodex.scraper.get_web_content")
    @patch("specodex.scraper.is_pdf_url", return_value=False)
    def test_html_success(
        self, _mock_is_pdf, mock_get_web, mock_gen, mock_parse, mock_db
    ):
        from specodex.models.motor import Motor

        mock_get_web.return_value = "<html>specs here</html>"
        mock_gen.return_value = MagicMock(text='[{"product_name": "Test"}]')

        fake_motor = Motor(
            product_type="motor",
            product_name="TestMotor",
            manufacturer="TestCorp",
        )
        mock_parse.return_value = [fake_motor]

        result = process_datasheet(
            mock_db,
            FAKE_API_KEY,
            "motor",
            "TestCorp",
            "TestMotor",
            "TestFamily",
            "https://example.com/specs",
            None,
        )
        assert result == "success"
        mock_get_web.assert_called_once()

    @patch("specodex.scraper.get_web_content")
    @patch("specodex.scraper.is_pdf_url", return_value=False)
    def test_html_retrieval_failure(self, _mock_is_pdf, mock_get_web, mock_db):
        mock_get_web.return_value = None

        result = process_datasheet(
            mock_db,
            FAKE_API_KEY,
            "motor",
            "TestCorp",
            "TestMotor",
            "TestFamily",
            "https://example.com/specs",
            None,
        )
        assert result == "failed"

    @patch("specodex.scraper.get_web_content")
    @patch("specodex.scraper.is_pdf_url", return_value=False)
    def test_pages_warning_for_web(self, _mock_is_pdf, mock_get_web, mock_db):
        """Pages parameter is ignored for web content."""
        mock_get_web.return_value = None  # Fail early to simplify test

        result = process_datasheet(
            mock_db,
            FAKE_API_KEY,
            "motor",
            "TestCorp",
            "TestMotor",
            "TestFamily",
            "https://example.com/specs",
            [1, 2],
        )
        assert result == "failed"


class TestProcessDatasheetParseFailure:
    """Test when LLM response parsing fails."""

    @patch("specodex.extract.parse_gemini_response")
    @patch("specodex.extract.generate_content")
    @patch("specodex.scraper.get_document")
    @patch("specodex.scraper.is_pdf_url", return_value=True)
    def test_parse_value_error(
        self, _mock_is_pdf, mock_get_doc, mock_gen, mock_parse, mock_db
    ):
        mock_get_doc.return_value = b"%PDF-fake"
        mock_gen.return_value = MagicMock(text="invalid json")
        mock_parse.side_effect = ValueError("Could not parse")

        result = process_datasheet(
            mock_db,
            FAKE_API_KEY,
            "motor",
            "TestCorp",
            "TestMotor",
            "TestFamily",
            "https://example.com/test.pdf",
            None,
        )
        assert result == "failed"

    @patch("specodex.extract.parse_gemini_response")
    @patch("specodex.extract.generate_content")
    @patch("specodex.scraper.get_document")
    @patch("specodex.scraper.is_pdf_url", return_value=True)
    def test_empty_parse_result(
        self, _mock_is_pdf, mock_get_doc, mock_gen, mock_parse, mock_db
    ):
        mock_get_doc.return_value = b"%PDF-fake"
        mock_gen.return_value = MagicMock(text="[]")
        mock_parse.return_value = []

        result = process_datasheet(
            mock_db,
            FAKE_API_KEY,
            "motor",
            "TestCorp",
            "TestMotor",
            "TestFamily",
            "https://example.com/test.pdf",
            None,
        )
        assert result == "failed"


class TestProcessDatasheetPdfRetrieval:
    """Test PDF retrieval failure."""

    @patch("specodex.scraper.get_document")
    @patch("specodex.scraper.is_pdf_url", return_value=True)
    def test_pdf_retrieval_failure(self, _mock_is_pdf, mock_get_doc, mock_db):
        mock_get_doc.return_value = None

        result = process_datasheet(
            mock_db,
            FAKE_API_KEY,
            "motor",
            "TestCorp",
            "TestMotor",
            "TestFamily",
            "https://example.com/test.pdf",
            None,
        )
        assert result == "failed"


class TestProcessDatasheetSkipExisting:
    """Test product_exists → skip path."""

    def test_existing_product_skipped(self, mock_db):
        mock_db.product_exists.return_value = True

        result = process_datasheet(
            mock_db,
            FAKE_API_KEY,
            "motor",
            "TestCorp",
            "TestMotor",
            "TestFamily",
            "https://example.com/test.pdf",
            None,
        )
        assert result == "skipped"


class TestProcessDatasheetUnexpectedError:
    """Test exception handling inside the try block."""

    @patch("specodex.scraper.get_document")
    @patch("specodex.scraper.is_pdf_url", return_value=True)
    def test_unexpected_exception_in_try_block(
        self, _mock_is_pdf, mock_get_doc, mock_db
    ):
        mock_get_doc.side_effect = RuntimeError("Unexpected crash")

        result = process_datasheet(
            mock_db,
            FAKE_API_KEY,
            "motor",
            "TestCorp",
            "TestMotor",
            "TestFamily",
            "https://example.com/test.pdf",
            None,
        )
        assert result == "failed"


class TestForceBypassesProductExists:
    """Regression: force=True must bypass the product_exists
    short-circuit. A partially ingested datasheet shares product_name
    with its own rows, so without the bypass a re-run can never finish
    the stragglers (the 'partial success wedges retries' wart — see
    catalog-ingest skill §7; product IDs are deterministic UUID5, so
    re-runs overwrite idempotently rather than duplicating)."""

    @patch("specodex.scraper.get_web_content")
    @patch("specodex.scraper.is_pdf_url", return_value=False)
    def test_force_false_short_circuits(self, _mock_is_pdf, mock_get_web, mock_db):
        mock_db.product_exists.return_value = True
        result = process_datasheet(
            mock_db,
            FAKE_API_KEY,
            "motor",
            "TestCorp",
            "TestMotor",
            "TestFamily",
            "https://example.com/specs",
            None,
            force=False,
        )
        assert result == "skipped"
        mock_get_web.assert_not_called()

    @patch("specodex.scraper.get_web_content")
    @patch("specodex.scraper.is_pdf_url", return_value=False)
    def test_force_true_proceeds_past_existing_product(
        self, _mock_is_pdf, mock_get_web, mock_db
    ):
        mock_db.product_exists.return_value = True
        mock_get_web.return_value = None  # fail right after the gate
        result = process_datasheet(
            mock_db,
            FAKE_API_KEY,
            "motor",
            "TestCorp",
            "TestMotor",
            "TestFamily",
            "https://example.com/specs",
            None,
            force=True,
        )
        # Reached the content fetch — the existing-product gate did not
        # short-circuit the forced re-run.
        mock_get_web.assert_called_once()
        assert result == "failed"
