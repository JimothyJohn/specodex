"""Edge-case coverage for ``specodex.page_finder``.

Page-finder bugs cost real money — a false negative routes a full PDF to
Gemini instead of a 3-page slice; a false positive bills a useless LLM
call. Baseline coverage was 0%; these tests cover the pure-Python
heuristics first (``find_spec_pages_by_text``, ``_score_page``,
``find_spec_pages_scored``) and then the orchestration path with Gemini
mocked.

PDFs are built in-memory with PyMuPDF so the suite has no fixture
dependencies. Synthetic PDFs don't carry table grids — we exercise
the table-signal branches by passing fake "table" objects directly to
``_score_page``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from specodex.page_finder import (
    SPEC_KEYWORD_THRESHOLD,
    SPEC_KEYWORDS,
    _MAX_PAGES_LARGE_DOC,
    _MAX_PAGES_SMALL_DOC,
    _score_page,
    classify_pages,
    find_spec_pages,
    find_spec_pages_by_text,
    find_spec_pages_scored,
    pdf_pages_to_images,
)


# ---------------------------------------------------------------------------
# In-memory PDF builders
# ---------------------------------------------------------------------------


def _make_pdf(pages: list[str]) -> bytes:
    """Build a PDF where each entry in ``pages`` is one page's text body."""
    import fitz

    doc = fitz.open()
    for body in pages:
        page = doc.new_page()
        if body:
            # ``insert_text`` lays each line; an empty body = a blank page.
            page.insert_text((50, 72), body)
    data = doc.tobytes()
    doc.close()
    return data


def _spec_text(extra_groups: int = 4) -> str:
    """Synthesize text matching ``extra_groups`` distinct SPEC_KEYWORDS groups."""
    chosen = SPEC_KEYWORDS[:extra_groups]
    return "\n".join(group[0] for group in chosen)


# ---------------------------------------------------------------------------
# find_spec_pages_by_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindSpecPagesByText:
    def test_empty_pdf_returns_empty(self) -> None:
        # Single blank page. No text, no keywords — must not crash.
        pdf = _make_pdf([""])
        assert find_spec_pages_by_text(pdf) == []

    def test_text_without_keywords_returns_empty(self) -> None:
        pdf = _make_pdf(["the quick brown fox jumps over the lazy dog"])
        assert find_spec_pages_by_text(pdf) == []

    def test_below_group_threshold_skipped(self) -> None:
        # Match exactly threshold-1 distinct groups → not selected.
        text = _spec_text(SPEC_KEYWORD_THRESHOLD - 1)
        pdf = _make_pdf([text])
        assert find_spec_pages_by_text(pdf) == []

    def test_at_threshold_selected(self) -> None:
        # Exactly threshold groups → selected.
        text = _spec_text(SPEC_KEYWORD_THRESHOLD)
        pdf = _make_pdf([text])
        assert find_spec_pages_by_text(pdf) == [0]

    def test_returns_zero_indexed(self) -> None:
        # Page 2 (1-indexed) → index 1 in the result.
        text = _spec_text(SPEC_KEYWORD_THRESHOLD)
        pdf = _make_pdf(["", text, ""])
        assert find_spec_pages_by_text(pdf) == [1]

    def test_all_pages_match_returns_all(self) -> None:
        text = _spec_text(SPEC_KEYWORD_THRESHOLD)
        pdf = _make_pdf([text, text, text])
        assert find_spec_pages_by_text(pdf) == [0, 1, 2]

    def test_keyword_match_is_case_insensitive(self) -> None:
        # Body uppercased — ``find_spec_pages_by_text`` lowercases internally.
        upper = "\n".join(g[0].upper() for g in SPEC_KEYWORDS[:SPEC_KEYWORD_THRESHOLD])
        pdf = _make_pdf([upper])
        assert find_spec_pages_by_text(pdf) == [0]

    @patch("specodex.page_finder.logger")
    def test_pymupdf_missing_returns_empty_and_warns(
        self, mock_logger: MagicMock
    ) -> None:
        # Simulate the ImportError fallback by patching the import site.
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "fitz":
                raise ImportError("no fitz")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            assert find_spec_pages_by_text(b"") == []
        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# _score_page (direct unit — table signal is hard to synthesize otherwise)
# ---------------------------------------------------------------------------


def _fake_table(rows: int, cols: int) -> object:
    t = MagicMock()
    t.row_count = rows
    t.col_count = cols
    return t


@pytest.mark.unit
class TestScorePage:
    def test_blank_page_scores_zero(self) -> None:
        info = _score_page("", [])
        assert info["score"] == 0.0
        assert info["groups_matched"] == 0

    def test_short_page_density_capped(self) -> None:
        # A single keyword on a 1-line page would otherwise score density=1.0;
        # the short-page penalty divides by _MIN_LINES_FOR_DENSITY=15.
        info_short = _score_page(SPEC_KEYWORDS[0][0], [])
        info_long = _score_page("\n".join([SPEC_KEYWORDS[0][0]] + ["x"] * 30), [])
        # Long page has more lines → lower raw density but no short-page penalty.
        # Short page should not pretend to be dense.
        assert info_short["keyword_density"] <= info_long["keyword_density"] + 0.1

    def test_table_signal_caps_at_one(self) -> None:
        # 100 rows × 100 cols = 10_000 cells — far above the 200-cell saturation.
        # The contribution should still be bounded by 0.40 (the table weight).
        info = _score_page("", [_fake_table(100, 100)])
        assert info["table_cells"] == 10_000
        assert info["score"] <= 0.40 + 1e-9

    def test_full_breadth_groups_match(self) -> None:
        # Match every SPEC_KEYWORDS group plus a big table — should be near 1.0.
        text = "\n".join(g[0] for g in SPEC_KEYWORDS) + "\n" + "filler\n" * 30
        info = _score_page(text, [_fake_table(20, 10)])
        assert info["groups_matched"] == len(SPEC_KEYWORDS)
        assert info["score"] > 0.85

    def test_score_components_exposed(self) -> None:
        info = _score_page("rated voltage 24V", [])
        for key in (
            "groups_matched",
            "keyword_hits",
            "n_lines",
            "keyword_density",
            "n_tables",
            "table_cells",
            "score",
        ):
            assert key in info


# ---------------------------------------------------------------------------
# find_spec_pages_scored
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindSpecPagesScored:
    def test_empty_pdf_returns_empty(self) -> None:
        pdf = _make_pdf([""])
        pages, details = find_spec_pages_scored(pdf)
        assert pages == []
        assert len(details) == 1
        assert details[0].get("empty") is True

    def test_returns_pages_in_document_order(self) -> None:
        # Three matching pages, no cap — must come back sorted ascending.
        text = _spec_text(len(SPEC_KEYWORDS))
        pdf = _make_pdf([text, text, text])
        pages, _ = find_spec_pages_scored(pdf)
        assert pages == sorted(pages)
        assert pages == [0, 1, 2]

    def test_below_min_score_dropped(self) -> None:
        # No keywords anywhere → every page below _MIN_SCORE → empty result.
        pdf = _make_pdf(["nothing here", "nothing there"])
        pages, _ = find_spec_pages_scored(pdf, min_score=0.15)
        assert pages == []

    def test_cap_engages_on_large_doc(self) -> None:
        # 25 pages > 20 → adaptive cap = _MAX_PAGES_LARGE_DOC (20).
        text = _spec_text(len(SPEC_KEYWORDS))
        pdf = _make_pdf([text] * 25)
        pages, details = find_spec_pages_scored(pdf)
        assert len(pages) <= _MAX_PAGES_LARGE_DOC
        assert len(details) == 25

    def test_small_doc_cap_is_generous(self) -> None:
        text = _spec_text(len(SPEC_KEYWORDS))
        pdf = _make_pdf([text] * 10)
        pages, _ = find_spec_pages_scored(pdf)
        assert len(pages) <= _MAX_PAGES_SMALL_DOC
        # All 10 pages match → all 10 should be returned (under cap of 15).
        assert pages == list(range(10))

    def test_explicit_max_pages_overrides_adaptive(self) -> None:
        text = _spec_text(len(SPEC_KEYWORDS))
        pdf = _make_pdf([text] * 5)
        pages, _ = find_spec_pages_scored(pdf, max_pages=2)
        assert len(pages) == 2

    def test_negative_max_pages_selects_nothing(self) -> None:
        """A negative cap must not act as a Python negative slice.

        Same regression as the drawing-finder twin (see
        `tests/unit/test_drawing_page_finder.py`): `candidates[:-1]`
        dropped the lowest-scoring candidate and kept the rest, so a
        negative "cap" *expanded* the page set sent to the LLM.
        """
        text = _spec_text(len(SPEC_KEYWORDS))
        pdf = _make_pdf([text] * 5)
        assert find_spec_pages_scored(pdf)[0] == list(range(5))
        assert find_spec_pages_scored(pdf, max_pages=0)[0] == []
        assert find_spec_pages_scored(pdf, max_pages=-1)[0] == []
        assert find_spec_pages_scored(pdf, max_pages=-3)[0] == []

    def test_cap_picks_top_scores_not_first_pages(self) -> None:
        # Page 0 has minimum keywords; page 4 has every group. With cap=1,
        # the cap must select by score, not by document order — so the
        # only returned page is the late high-score one, not page 0.
        weak = _spec_text(SPEC_KEYWORD_THRESHOLD)  # threshold-many groups
        strong = _spec_text(len(SPEC_KEYWORDS))  # every group
        pdf = _make_pdf([weak, "", "", "", strong])
        pages, _ = find_spec_pages_scored(pdf, max_pages=1)
        assert pages == [4]

    @patch("specodex.page_finder.find_spec_pages_by_text")
    def test_pymupdf_missing_falls_back_to_text_finder(
        self, mock_text: MagicMock
    ) -> None:
        mock_text.return_value = [3, 7]
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "fitz":
                raise ImportError("no fitz")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            pages, details = find_spec_pages_scored(b"")
        assert pages == [3, 7]
        assert details == []

    def test_table_extraction_failure_is_swallowed(self) -> None:
        # Force find_tables to raise on every page; the function must
        # continue scoring on text-only signals, not crash.
        text = _spec_text(len(SPEC_KEYWORDS))
        pdf = _make_pdf([text])

        import fitz

        real_open = fitz.open

        def fake_open(*args: object, **kwargs: object) -> object:
            doc = real_open(*args, **kwargs)
            for i in range(len(doc)):
                doc[i].find_tables = MagicMock(side_effect=RuntimeError("boom"))
            return doc

        with patch.object(fitz, "open", side_effect=fake_open):
            pages, details = find_spec_pages_scored(pdf)
        assert pages == [0]
        assert details[0]["n_tables"] == 0


# ---------------------------------------------------------------------------
# pdf_pages_to_images
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPdfPagesToImages:
    def test_one_image_per_page(self) -> None:
        pdf = _make_pdf(["a", "b", "c"])
        images = pdf_pages_to_images(pdf)
        assert len(images) == 3
        # JPEG magic bytes (FF D8 FF) at the start of each.
        for img in images:
            assert img[:3] == b"\xff\xd8\xff"

    def test_dpi_affects_size(self) -> None:
        pdf = _make_pdf(["sample"])
        small = pdf_pages_to_images(pdf, dpi=50)[0]
        large = pdf_pages_to_images(pdf, dpi=200)[0]
        assert len(large) > len(small)


# ---------------------------------------------------------------------------
# classify_pages (Gemini mocked)
# ---------------------------------------------------------------------------


def _mock_genai_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


@pytest.mark.unit
class TestClassifyPages:
    @patch("specodex.page_finder.genai")
    def test_parses_well_formed_response(self, mock_genai: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_genai_response(
            '[{"page": 1, "has_specs": true, "description": "spec table"}]'
        )
        mock_genai.Client.return_value = mock_client

        results = classify_pages([b"img"], api_key="k")
        assert len(results) == 1
        assert results[0]["has_specs"] is True
        assert results[0]["page_number"] == 0  # 1-indexed → 0-indexed

    @patch("specodex.page_finder.genai")
    def test_batches_pages(self, mock_genai: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            _mock_genai_response('[{"page": 1, "has_specs": false}]'),
            _mock_genai_response('[{"page": 1, "has_specs": true}]'),
        ]
        mock_genai.Client.return_value = mock_client

        results = classify_pages([b"img"] * 6, api_key="k", batch_size=5)
        # 6 images → 2 batches (5 + 1) → 2 generate_content calls.
        assert mock_client.models.generate_content.call_count == 2
        assert len(results) == 2

    @patch("specodex.page_finder.genai")
    def test_api_failure_marks_pages_unknown(self, mock_genai: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("rate limit")
        mock_genai.Client.return_value = mock_client

        results = classify_pages([b"a", b"b"], api_key="k", batch_size=5)
        # Failure must surface as a row per page, not a raised exception.
        assert len(results) == 2
        assert all(r["has_specs"] is False for r in results)
        assert all("classification failed" in r["description"] for r in results)

    @patch("specodex.page_finder.genai")
    def test_non_array_response_yields_no_results(self, mock_genai: MagicMock) -> None:
        # Gemini occasionally hallucinates an object instead of an array.
        # The function should silently produce no rows for that batch
        # rather than raising.
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_genai_response(
            '{"page": 1, "has_specs": true}'
        )
        mock_genai.Client.return_value = mock_client

        results = classify_pages([b"img"], api_key="k")
        assert results == []

    @patch("specodex.page_finder.genai")
    def test_empty_response_yields_no_results(self, mock_genai: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_genai_response("")
        mock_genai.Client.return_value = mock_client

        results = classify_pages([b"img"], api_key="k")
        assert results == []


# ---------------------------------------------------------------------------
# find_spec_pages (top-level orchestration)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindSpecPages:
    @patch("specodex.page_finder.classify_pages")
    @patch("specodex.page_finder.pdf_pages_to_images")
    @patch("specodex.page_finder.get_document")
    def test_orchestrates_download_render_classify(
        self,
        mock_get: MagicMock,
        mock_render: MagicMock,
        mock_classify: MagicMock,
    ) -> None:
        mock_get.return_value = b"%PDF-fake"
        mock_render.return_value = [b"i0", b"i1", b"i2"]
        mock_classify.return_value = [
            {"page_number": 0, "page_display": 1, "has_specs": False},
            {"page_number": 1, "page_display": 2, "has_specs": True},
            {"page_number": 2, "page_display": 3, "has_specs": False},
        ]
        out = find_spec_pages("https://x/y.pdf", api_key="k")
        assert out["total_pages"] == 3
        assert out["spec_pages"] == [1]
        assert out["spec_page_count"] == 1

    @patch("specodex.page_finder.get_document")
    def test_download_failure_raises(self, mock_get: MagicMock) -> None:
        mock_get.return_value = None
        with pytest.raises(ValueError):
            find_spec_pages("https://x/y.pdf", api_key="k")

    @patch("specodex.page_finder.classify_pages")
    @patch("specodex.page_finder.pdf_pages_to_images")
    @patch("specodex.page_finder.get_document")
    def test_explicit_pages_subset_remaps_back(
        self,
        mock_get: MagicMock,
        mock_render: MagicMock,
        mock_classify: MagicMock,
    ) -> None:
        # Caller asks to classify pages [2, 5] of a 6-page doc; the
        # classifier sees images at internal indices 0 and 1 and the
        # orchestrator must remap to the original page numbers.
        mock_get.return_value = b"%PDF-fake"
        mock_render.return_value = [b"i"] * 6
        mock_classify.return_value = [
            {"page_number": 0, "page_display": 1, "has_specs": True},
            {"page_number": 1, "page_display": 2, "has_specs": True},
        ]
        out = find_spec_pages("https://x/y.pdf", api_key="k", pages=[2, 5])
        assert sorted(out["spec_pages"]) == [2, 5]

    @patch("specodex.page_finder.classify_pages")
    @patch("specodex.page_finder.pdf_pages_to_images")
    @patch("specodex.page_finder.get_document")
    def test_out_of_range_pages_dropped(
        self,
        mock_get: MagicMock,
        mock_render: MagicMock,
        mock_classify: MagicMock,
    ) -> None:
        # User asks for page 99 of a 3-page doc — must not crash, just skip.
        mock_get.return_value = b"%PDF-fake"
        mock_render.return_value = [b"i"] * 3
        mock_classify.return_value = []
        out = find_spec_pages("https://x/y.pdf", api_key="k", pages=[99])
        assert out["spec_pages"] == []


# ---------------------------------------------------------------------------
# CLI/admin paths — main(), _update_datasheet_pages, _scan_all_datasheets
# ---------------------------------------------------------------------------


def _fake_datasheet(
    *,
    url: str = "https://x.com/a.pdf",
    pages: list[int] | None = None,
    product_name: str = "M1",
    product_type: str = "motor",
    datasheet_id: str = "ds-1",
) -> MagicMock:
    """Stand-in for a Datasheet model — duck-typed; tests never touch the real model."""
    ds = MagicMock()
    ds.url = url
    ds.pages = pages
    ds.product_name = product_name
    ds.product_type = product_type
    ds.datasheet_id = datasheet_id
    ds.manufacturer = "Acme"
    ds.product_family = "M-series"
    return ds


@pytest.mark.unit
class TestUpdateDatasheetPages:
    @patch("specodex.db.dynamo.DynamoDBClient")
    def test_writes_matching_url(self, mock_cls: MagicMock) -> None:
        from specodex.page_finder import _update_datasheet_pages

        client = MagicMock()
        match = _fake_datasheet(url="https://x.com/a.pdf")
        miss = _fake_datasheet(url="https://x.com/b.pdf")
        client.get_all_datasheets.return_value = [match, miss]
        client.create.return_value = True
        mock_cls.return_value = client

        _update_datasheet_pages("https://x.com/a.pdf", [1, 2, 3])

        assert match.pages == [1, 2, 3]
        client.create.assert_called_once_with(match)

    @patch("specodex.db.dynamo.DynamoDBClient")
    @patch("specodex.page_finder.logger")
    def test_no_match_warns(self, mock_log: MagicMock, mock_cls: MagicMock) -> None:
        from specodex.page_finder import _update_datasheet_pages

        client = MagicMock()
        client.get_all_datasheets.return_value = []
        mock_cls.return_value = client

        _update_datasheet_pages("https://nope/x.pdf", [1])

        mock_log.warning.assert_called()
        client.create.assert_not_called()

    @patch("specodex.db.dynamo.DynamoDBClient")
    @patch("specodex.page_finder.logger")
    def test_create_failure_logs_error(
        self, mock_log: MagicMock, mock_cls: MagicMock
    ) -> None:
        from specodex.page_finder import _update_datasheet_pages

        client = MagicMock()
        ds = _fake_datasheet(url="u")
        client.get_all_datasheets.return_value = [ds]
        client.create.return_value = False
        mock_cls.return_value = client

        _update_datasheet_pages("u", [9])

        mock_log.error.assert_called()


@pytest.mark.unit
class TestScanAllDatasheets:
    @patch("specodex.db.dynamo.DynamoDBClient")
    @patch("specodex.utils.is_pdf_url", return_value=True)
    @patch("specodex.page_finder.find_spec_pages")
    def test_filters_pdfs_needing_pages(
        self,
        mock_find: MagicMock,
        mock_is_pdf: MagicMock,
        mock_cls: MagicMock,
    ) -> None:
        from specodex.page_finder import _scan_all_datasheets

        client = MagicMock()
        client.get_all_datasheets.return_value = [
            _fake_datasheet(url="u1", pages=None),  # candidate
            _fake_datasheet(url="u2", pages=[1, 2, 3, 4]),  # already scanned (>2)
            _fake_datasheet(url="", pages=None),  # no URL
        ]
        mock_cls.return_value = client
        mock_find.return_value = {"spec_pages": [5, 6]}

        _scan_all_datasheets(api_key="k", product_type=None, update_db=False)

        # Only u1 is a candidate — u2 already has pages, "" has no URL.
        assert mock_find.call_count == 1
        called_url = mock_find.call_args.args[0]
        assert called_url == "u1"

    @patch("specodex.db.dynamo.DynamoDBClient")
    @patch("specodex.utils.is_pdf_url")
    @patch("specodex.page_finder.find_spec_pages")
    def test_skips_non_pdf_urls(
        self,
        mock_find: MagicMock,
        mock_is_pdf: MagicMock,
        mock_cls: MagicMock,
    ) -> None:
        from specodex.page_finder import _scan_all_datasheets

        mock_is_pdf.side_effect = lambda url: url.endswith(".pdf")
        client = MagicMock()
        client.get_all_datasheets.return_value = [
            _fake_datasheet(url="https://x/a.html", pages=None),
            _fake_datasheet(url="https://x/b.pdf", pages=None),
        ]
        mock_cls.return_value = client
        mock_find.return_value = {"spec_pages": []}

        _scan_all_datasheets(api_key="k", product_type=None, update_db=False)
        called_urls = [c.args[0] for c in mock_find.call_args_list]
        assert called_urls == ["https://x/b.pdf"]

    @patch("specodex.db.dynamo.DynamoDBClient")
    @patch("specodex.utils.is_pdf_url", return_value=True)
    @patch("specodex.page_finder.find_spec_pages")
    def test_filters_by_product_type(
        self,
        mock_find: MagicMock,
        mock_is_pdf: MagicMock,
        mock_cls: MagicMock,
    ) -> None:
        from specodex.page_finder import _scan_all_datasheets

        client = MagicMock()
        client.get_all_datasheets.return_value = [
            _fake_datasheet(url="m.pdf", product_type="motor", pages=None),
            _fake_datasheet(url="d.pdf", product_type="drive", pages=None),
        ]
        mock_cls.return_value = client
        mock_find.return_value = {"spec_pages": []}

        _scan_all_datasheets(api_key="k", product_type="motor", update_db=False)

        called_urls = [c.args[0] for c in mock_find.call_args_list]
        assert called_urls == ["m.pdf"]

    @patch("specodex.db.dynamo.DynamoDBClient")
    @patch("specodex.utils.is_pdf_url", return_value=True)
    @patch("specodex.page_finder.find_spec_pages")
    @patch("specodex.page_finder._update_datasheet_pages")
    def test_update_db_called_only_when_specs_found(
        self,
        mock_update: MagicMock,
        mock_find: MagicMock,
        mock_is_pdf: MagicMock,
        mock_cls: MagicMock,
    ) -> None:
        from specodex.page_finder import _scan_all_datasheets

        client = MagicMock()
        client.get_all_datasheets.return_value = [
            _fake_datasheet(url="hit.pdf", pages=None),
            _fake_datasheet(url="miss.pdf", pages=None),
        ]
        mock_cls.return_value = client
        mock_find.side_effect = [
            {"spec_pages": [3, 4]},
            {"spec_pages": []},
        ]

        _scan_all_datasheets(api_key="k", product_type=None, update_db=True)

        mock_update.assert_called_once_with("hit.pdf", [3, 4])

    @patch("specodex.db.dynamo.DynamoDBClient")
    @patch("specodex.utils.is_pdf_url", return_value=True)
    @patch("specodex.page_finder.find_spec_pages")
    @patch("specodex.page_finder.logger")
    def test_per_datasheet_failure_does_not_abort_loop(
        self,
        mock_log: MagicMock,
        mock_find: MagicMock,
        mock_is_pdf: MagicMock,
        mock_cls: MagicMock,
    ) -> None:
        from specodex.page_finder import _scan_all_datasheets

        client = MagicMock()
        client.get_all_datasheets.return_value = [
            _fake_datasheet(url="boom.pdf", pages=None),
            _fake_datasheet(url="ok.pdf", pages=None),
        ]
        mock_cls.return_value = client
        mock_find.side_effect = [RuntimeError("download failed"), {"spec_pages": [1]}]

        _scan_all_datasheets(api_key="k", product_type=None, update_db=False)

        # Both URLs were attempted despite the first one raising.
        assert mock_find.call_count == 2


@pytest.mark.unit
class TestMain:
    @patch("specodex.page_finder.find_spec_pages")
    def test_single_url_path(
        self, mock_find: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from specodex.page_finder import main

        mock_find.return_value = {
            "url": "https://x/y.pdf",
            "total_pages": 2,
            "spec_pages": [1],
            "spec_page_count": 1,
            "all_pages": [
                {"has_specs": False, "page_display": 1, "description": "cover"},
                {"has_specs": True, "page_display": 2, "description": "specs"},
            ],
        }
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(
            "sys.argv", ["page_finder", "--url", "https://x/y.pdf", "--type", "motor"]
        )
        main()
        mock_find.assert_called_once()

    def test_missing_api_key_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from specodex.page_finder import main

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setattr("sys.argv", ["page_finder", "--url", "x"])
        with pytest.raises(SystemExit):
            main()

    def test_missing_url_without_scan_all_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from specodex.page_finder import main

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr("sys.argv", ["page_finder"])
        with pytest.raises(SystemExit):
            main()

    @patch("specodex.page_finder._scan_all_datasheets")
    def test_scan_all_dispatches(
        self, mock_scan: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from specodex.page_finder import main

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr("sys.argv", ["page_finder", "--scan-all"])
        main()
        mock_scan.assert_called_once_with("k", None, False)

    @patch("specodex.page_finder._update_datasheet_pages")
    @patch("specodex.page_finder.find_spec_pages")
    def test_update_db_calls_writer(
        self,
        mock_find: MagicMock,
        mock_update: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from specodex.page_finder import main

        mock_find.return_value = {
            "url": "u",
            "total_pages": 1,
            "spec_pages": [0],
            "spec_page_count": 1,
            "all_pages": [],
        }
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr("sys.argv", ["page_finder", "--url", "u", "--update-db"])
        main()
        mock_update.assert_called_once_with("u", [0])

    @patch("specodex.page_finder.find_spec_pages")
    def test_output_file_written(
        self,
        mock_find: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        from specodex.page_finder import main

        result = {
            "url": "u",
            "total_pages": 1,
            "spec_pages": [],
            "spec_page_count": 0,
            "all_pages": [],
        }
        mock_find.return_value = result
        out = tmp_path / "out.json"
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr("sys.argv", ["page_finder", "--url", "u", "-o", str(out)])
        main()
        import json as _json

        assert _json.loads(out.read_text()) == result
