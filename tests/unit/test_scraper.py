"""Unit tests for specodex/scraper.py."""

import logging
from unittest.mock import MagicMock, Mock, patch

import pytest

from specodex.models.motor import Motor
from specodex.scraper import (
    ElapsedTimeFormatter,
    _chunk_pages,
    process_datasheet,
)


@pytest.mark.unit
class TestChunkPages:
    """Tests for the page-chunking algorithm. See todo/CHUNKS.md."""

    def test_empty(self) -> None:
        assert _chunk_pages([]) == []

    def test_single_page(self) -> None:
        assert _chunk_pages([5]) == [[5]]

    def test_consecutive_within_max(self) -> None:
        assert _chunk_pages([3, 4, 5]) == [[3, 4, 5]]

    def test_consecutive_splits_at_max(self) -> None:
        assert _chunk_pages([3, 4, 5, 6, 7], chunk_max=4) == [[3, 4, 5, 6], [7]]

    def test_bridges_one_gap(self) -> None:
        # MPP-shaped: every-other-page hits collapse into runs, gaps get filled.
        out = _chunk_pages([3, 5, 7, 9, 11, 13, 15, 23], chunk_max=4, bridge_gap=1)
        assert out == [[3, 4, 5, 6], [7, 8, 9, 10], [11, 12, 13, 14], [15], [23]]

    def test_does_not_bridge_large_gap(self) -> None:
        assert _chunk_pages([3, 9], chunk_max=4, bridge_gap=1) == [[3], [9]]

    def test_dedupes_and_sorts(self) -> None:
        assert _chunk_pages([5, 3, 4, 4]) == [[3, 4, 5]]

    def test_zero_bridge_keeps_singletons(self) -> None:
        # bridge_gap=0 means only adjacent pages merge.
        assert _chunk_pages([3, 5, 7], bridge_gap=0) == [[3], [5], [7]]
        assert _chunk_pages([3, 4, 5], bridge_gap=0) == [[3, 4, 5]]


@pytest.mark.unit
class TestElapsedTimeFormatter:
    """Tests for the ElapsedTimeFormatter logging formatter."""

    def test_format_time(self) -> None:
        """formatTime returns elapsed time in M:SS format."""
        formatter = ElapsedTimeFormatter()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        # Simulate a record created 65 seconds after the formatter was created
        record.created = formatter.start_time + 65

        result = formatter.formatTime(record)

        assert result == "1:05"


@pytest.mark.unit
class TestProcessDatasheet:
    """Tests for process_datasheet()."""

    def test_product_exists_skipped(self) -> None:
        """Product already in DB -- returns 'skipped'."""
        mock_client = MagicMock()
        mock_client.product_exists.return_value = True

        result = process_datasheet(
            client=mock_client,
            api_key="test-key",
            product_type="motor",
            manufacturer="TestMfg",
            product_name="Test",
            product_family="",
            url="https://example.com/test.pdf",
            pages=None,
        )

        assert result == "skipped"
        mock_client.product_exists.assert_called_once()

    @patch("specodex.extract.parse_gemini_response")
    @patch("specodex.extract.generate_content")
    @patch("specodex.scraper.is_pdf_url")
    @patch("specodex.scraper.get_document")
    @patch("specodex.scraper.find_spec_pages_by_text", return_value=[])
    def test_pdf_success(
        self,
        mock_find_pages: MagicMock,
        mock_get_doc: MagicMock,
        mock_is_pdf: MagicMock,
        mock_generate: MagicMock,
        mock_parse: MagicMock,
    ) -> None:
        """PDF downloaded and parsed successfully -- returns 'success'."""
        mock_is_pdf.return_value = True
        mock_get_doc.return_value = b"pdf bytes"
        mock_response = Mock()
        mock_response.text = '{"products": []}'
        mock_generate.return_value = mock_response

        motor = Motor(
            product_type="motor",
            product_name="Test",
            manufacturer="TestMfg",
            part_number="ABC-123",
        )
        mock_parse.return_value = [motor]

        mock_client = MagicMock()
        mock_client.product_exists.return_value = False
        mock_client.read.return_value = None
        mock_client.batch_create.return_value = 1

        result = process_datasheet(
            client=mock_client,
            api_key="test-key",
            product_type="motor",
            manufacturer="TestMfg",
            product_name="Test",
            product_family="",
            url="https://example.com/test.pdf",
            pages=None,
        )

        assert result == "success"
        mock_client.batch_create.assert_called_once()

    @patch("specodex.extract.parse_gemini_response")
    @patch("specodex.extract.generate_content")
    @patch("specodex.scraper.is_pdf_url")
    @patch("specodex.scraper.get_web_content")
    def test_html_success(
        self,
        mock_get_web: MagicMock,
        mock_is_pdf: MagicMock,
        mock_generate: MagicMock,
        mock_parse: MagicMock,
    ) -> None:
        """Web page scraped and parsed successfully -- returns 'success'."""
        mock_is_pdf.return_value = False
        mock_get_web.return_value = "<html><body>specs</body></html>"
        mock_response = Mock()
        mock_response.text = '{"products": []}'
        mock_generate.return_value = mock_response

        motor = Motor(
            product_type="motor",
            product_name="WebMotor",
            manufacturer="WebMfg",
            part_number="WEB-001",
        )
        mock_parse.return_value = [motor]

        mock_client = MagicMock()
        mock_client.product_exists.return_value = False
        mock_client.read.return_value = None
        mock_client.batch_create.return_value = 1

        result = process_datasheet(
            client=mock_client,
            api_key="test-key",
            product_type="motor",
            manufacturer="WebMfg",
            product_name="WebMotor",
            product_family="",
            url="https://example.com/product",
            pages=None,
        )

        assert result == "success"

    @patch("specodex.scraper.is_pdf_url")
    @patch("specodex.scraper.get_document")
    def test_download_failure(
        self,
        mock_get_doc: MagicMock,
        mock_is_pdf: MagicMock,
    ) -> None:
        """PDF download returns None -- returns 'failed'."""
        mock_is_pdf.return_value = True
        mock_get_doc.return_value = None

        mock_client = MagicMock()
        mock_client.product_exists.return_value = False

        result = process_datasheet(
            client=mock_client,
            api_key="test-key",
            product_type="motor",
            manufacturer="TestMfg",
            product_name="Test",
            product_family="",
            url="https://example.com/broken.pdf",
            pages=None,
        )

        assert result == "failed"

    @patch("specodex.extract.parse_gemini_response")
    @patch("specodex.extract.generate_content")
    @patch("specodex.scraper.is_pdf_url")
    @patch("specodex.scraper.get_document")
    @patch("specodex.scraper.find_spec_pages_by_text", return_value=[])
    def test_parse_failure(
        self,
        mock_find_pages: MagicMock,
        mock_get_doc: MagicMock,
        mock_is_pdf: MagicMock,
        mock_generate: MagicMock,
        mock_parse: MagicMock,
    ) -> None:
        """parse_gemini_response raises ValueError -- returns 'failed'."""
        mock_is_pdf.return_value = True
        mock_get_doc.return_value = b"pdf bytes"
        mock_response = Mock()
        mock_response.text = "garbage"
        mock_generate.return_value = mock_response
        mock_parse.side_effect = ValueError("Invalid JSON")

        mock_client = MagicMock()
        mock_client.product_exists.return_value = False

        result = process_datasheet(
            client=mock_client,
            api_key="test-key",
            product_type="motor",
            manufacturer="TestMfg",
            product_name="Test",
            product_family="",
            url="https://example.com/test.pdf",
            pages=None,
        )

        assert result == "failed"

    @patch("specodex.quality.filter_products", side_effect=lambda ps, **_: (ps, []))
    @patch("specodex.extract.parse_gemini_response")
    @patch("specodex.extract.generate_content")
    @patch("specodex.scraper.is_pdf_url")
    @patch("specodex.scraper.get_document")
    @patch("specodex.scraper.find_spec_pages_by_text", return_value=[])
    def test_deterministic_id(
        self,
        mock_find_pages: MagicMock,
        mock_get_doc: MagicMock,
        mock_is_pdf: MagicMock,
        mock_generate: MagicMock,
        mock_parse: MagicMock,
        mock_filter: MagicMock,
    ) -> None:
        """product_id is generated via uuid5 from manufacturer+part_number.

        Quality filter is stubbed out: this test only verifies ID assignment,
        not quality gating, and a bare-minimum motor would otherwise fail
        the 25% completeness threshold.
        """
        import re
        import uuid

        mock_is_pdf.return_value = True
        mock_get_doc.return_value = b"pdf bytes"
        mock_response = Mock()
        mock_response.text = "{}"
        mock_generate.return_value = mock_response

        motor = Motor(
            product_type="motor",
            product_name="Test",
            manufacturer="TestMfg",
            part_number="ABC-123",
        )
        mock_parse.return_value = [motor]

        mock_client = MagicMock()
        mock_client.product_exists.return_value = False
        mock_client.read.return_value = None
        mock_client.batch_create.return_value = 1

        process_datasheet(
            client=mock_client,
            api_key="test-key",
            product_type="motor",
            manufacturer="TestMfg",
            product_name="Test",
            product_family="",
            url="https://example.com/test.pdf",
            pages=None,
        )

        # Reproduce the expected deterministic ID
        namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
        norm_mfg = re.sub(r"[^a-z0-9]", "", "TestMfg".lower().strip())
        norm_pn = re.sub(r"[^a-z0-9]", "", "ABC-123".lower().strip())
        expected_id = uuid.uuid5(namespace, f"{norm_mfg}:{norm_pn}")

        # The model passed to batch_create should have the deterministic ID
        saved_models = mock_client.batch_create.call_args[0][0]
        assert saved_models[0].product_id == expected_id

    @patch("specodex.extract.parse_gemini_response")
    @patch("specodex.extract.generate_content")
    @patch("specodex.scraper.is_pdf_url")
    @patch("specodex.scraper.get_document")
    @patch("specodex.scraper.find_spec_pages_by_text", return_value=[])
    def test_no_manufacturer_skips(
        self,
        mock_find_pages: MagicMock,
        mock_get_doc: MagicMock,
        mock_is_pdf: MagicMock,
        mock_generate: MagicMock,
        mock_parse: MagicMock,
    ) -> None:
        """Model without manufacturer AND part_number is excluded from valid_models."""
        mock_is_pdf.return_value = True
        mock_get_doc.return_value = b"pdf bytes"
        mock_response = Mock()
        mock_response.text = "{}"
        mock_generate.return_value = mock_response

        # Motor with empty manufacturer and no part_number -- cannot generate robust ID
        motor = Motor(
            product_type="motor",
            product_name="Orphan",
            manufacturer="",
            part_number=None,
        )
        mock_parse.return_value = [motor]

        mock_client = MagicMock()
        mock_client.product_exists.return_value = False
        mock_client.read.return_value = None
        mock_client.batch_create.return_value = 0

        result = process_datasheet(
            client=mock_client,
            api_key="test-key",
            product_type="motor",
            # Pass empty manufacturer at the call-site level too
            manufacturer="",
            product_name="Orphan",
            product_family="",
            url="https://example.com/test.pdf",
            pages=None,
        )

        # No valid models means batch_create gets an empty list (or is called with 0 items)
        # The function returns "failed" because success_count == 0
        assert result == "failed"


@pytest.mark.unit
class TestExtractBundledPdfTempFiles:
    """Regression: _extract_bundled_pdf created two delete=False temp
    files and only unlinked them on the happy path. Its caller
    (_run_chunk) swallows exceptions, so a bad chunk silently leaked
    two temp PDFs per failure — a long batch run accumulated them."""

    def test_no_temp_leak_when_extraction_raises(self, tmp_path, monkeypatch) -> None:
        import tempfile

        from specodex import scraper

        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        monkeypatch.setattr(
            scraper,
            "extract_pdf_pages",
            MagicMock(side_effect=RuntimeError("corrupt pdf")),
        )
        with pytest.raises(RuntimeError):
            scraper._extract_bundled_pdf(b"%PDF-fake", [0])
        leftovers = list(tmp_path.iterdir())
        assert leftovers == [], f"temp files leaked: {leftovers}"

    def test_no_temp_leak_on_success(self, tmp_path, monkeypatch) -> None:
        import tempfile

        from specodex import scraper

        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

        def _fake_extract(tmp_in, tmp_out, pages):
            tmp_out.write_bytes(b"%PDF-out")

        monkeypatch.setattr(scraper, "extract_pdf_pages", _fake_extract)
        assert scraper._extract_bundled_pdf(b"%PDF-fake", [0]) == b"%PDF-out"
        assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
class TestPromptPrefixForwarding:
    """process_datasheet forwards prompt_prefix to every LLM call.

    Series-overview brochures (Nidec ABLE VR, 2026-07-24) need a
    steering block ("one entry per series") or Gemini enumerates the
    ratio × frame-size cross-product and truncates mid-JSON.
    """

    @staticmethod
    def _one_page_pdf() -> bytes:
        import fitz

        doc = fitz.open()
        doc.new_page()
        return doc.tobytes()

    @staticmethod
    def _mock_client() -> MagicMock:
        client = MagicMock()
        client.product_exists.return_value = False
        client.read.return_value = None
        client.read_ingest.return_value = None
        client.batch_create.return_value = 1
        return client

    @patch("specodex.extract.parse_gemini_response")
    @patch("specodex.extract.generate_content")
    @patch("specodex.scraper.is_pdf_url", return_value=True)
    @patch("specodex.scraper.get_document")
    def test_per_page_path_forwards_prefix(
        self,
        mock_get_doc: MagicMock,
        mock_is_pdf: MagicMock,
        mock_generate: MagicMock,
        mock_parse: MagicMock,
    ) -> None:
        mock_get_doc.return_value = self._one_page_pdf()
        mock_generate.return_value = Mock(text="[]")
        mock_parse.return_value = [
            Motor(
                product_type="motor",
                product_name="Test",
                manufacturer="TestMfg",
                part_number="ABC-123",
            )
        ]

        process_datasheet(
            client=self._mock_client(),
            api_key="test-key",
            product_type="motor",
            manufacturer="TestMfg",
            product_name="Test",
            product_family="",
            url="https://example.com/test.pdf",
            pages=[0],
            save_failed_to=None,
            prompt_prefix="STEER-BLOCK",
        )

        assert mock_generate.call_count >= 1
        for call in mock_generate.call_args_list:
            assert call.kwargs["prompt_prefix"] == "STEER-BLOCK"

    @patch("specodex.extract.parse_gemini_response")
    @patch("specodex.extract.generate_content")
    @patch("specodex.scraper.is_pdf_url", return_value=True)
    @patch("specodex.scraper.get_document")
    @patch("specodex.scraper.find_spec_pages_by_text", return_value=[])
    def test_full_pdf_path_forwards_prefix(
        self,
        mock_find_pages: MagicMock,
        mock_get_doc: MagicMock,
        mock_is_pdf: MagicMock,
        mock_generate: MagicMock,
        mock_parse: MagicMock,
    ) -> None:
        mock_get_doc.return_value = b"pdf bytes"
        mock_generate.return_value = Mock(text="[]")
        mock_parse.return_value = [
            Motor(
                product_type="motor",
                product_name="Test",
                manufacturer="TestMfg",
                part_number="ABC-123",
            )
        ]

        process_datasheet(
            client=self._mock_client(),
            api_key="test-key",
            product_type="motor",
            manufacturer="TestMfg",
            product_name="Test",
            product_family="",
            url="https://example.com/test.pdf",
            pages=None,
            save_failed_to=None,
            prompt_prefix="STEER-BLOCK",
        )

        mock_generate.assert_called_once()
        assert mock_generate.call_args.kwargs["prompt_prefix"] == "STEER-BLOCK"


@pytest.mark.unit
class TestMinQualityOverride:
    """process_datasheet honors a per-datasheet quality-gate override."""

    @staticmethod
    def _sparse_motor() -> Motor:
        # part_number + type only — sparse enough to fail the default
        # 25% gate but pass a deliberate 20% override is not guaranteed
        # across schema growth, so compute the score dynamically in the
        # test instead of hardcoding expectations here.
        return Motor(
            product_type="motor",
            product_name="Test",
            manufacturer="TestMfg",
            part_number="ABC-123",
        )

    def _run(self, min_quality, mock_parse_return) -> MagicMock:
        client = MagicMock()
        client.product_exists.return_value = False
        client.read.return_value = None
        client.read_ingest.return_value = None
        client.batch_create.side_effect = lambda models: len(models)

        with (
            patch("specodex.scraper.get_document", return_value=b"pdf bytes"),
            patch("specodex.scraper.is_pdf_url", return_value=True),
            patch("specodex.scraper.find_spec_pages_by_text", return_value=[]),
            patch("specodex.extract.generate_content", return_value=Mock(text="[]")),
            patch(
                "specodex.extract.parse_gemini_response", return_value=mock_parse_return
            ),
        ):
            process_datasheet(
                client=client,
                api_key="test-key",
                product_type="motor",
                manufacturer="TestMfg",
                product_name="Test",
                product_family="",
                url="https://example.com/test.pdf",
                pages=None,
                save_failed_to=None,
                min_quality=min_quality,
            )
        return client

    def test_sparse_product_fails_default_gate(self) -> None:
        from specodex.quality import score_product

        sparse = self._sparse_motor()
        score, *_ = score_product(sparse)
        assert score < 0.25, "fixture drifted: no longer below the default gate"

        client = self._run(min_quality=None, mock_parse_return=[sparse])
        (written,) = client.batch_create.call_args.args
        assert written == []

    def test_override_admits_sparse_product(self) -> None:
        from specodex.quality import score_product

        sparse = self._sparse_motor()
        score, *_ = score_product(sparse)

        client = self._run(min_quality=score, mock_parse_return=[sparse])
        (written,) = client.batch_create.call_args.args
        assert len(written) == 1
        assert written[0].part_number == "ABC-123"
