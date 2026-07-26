"""Tests for the dimensional-drawing page-finder heuristics in
``specodex.page_finder``.

Mirrors ``tests/unit/test_page_finder_edge.py``'s in-memory PDF pattern:
PyMuPDF builds synthetic PDFs so the suite has no fixture dependencies.
Vector-path density (the drawing-page analogue of the spec-table finder's
table-cell signal) is exercised by drawing real rectangles/lines onto a
page so ``page.get_drawings()`` has something to count.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from specodex.page_finder import (
    DRAWING_KEYWORD_THRESHOLD,
    DRAWING_KEYWORDS,
    _MAX_PAGES_LARGE_DOC,
    _MAX_PAGES_SMALL_DOC,
    _score_drawing_page,
    find_drawing_pages_by_text,
    find_drawing_pages_scored,
)


def _make_pdf(pages: list[str]) -> bytes:
    """Build a PDF where each entry in ``pages`` is one page's text body."""
    import fitz

    doc = fitz.open()
    for body in pages:
        page = doc.new_page()
        if body:
            page.insert_text((50, 72), body)
    data = doc.tobytes()
    doc.close()
    return data


def _make_pdf_with_drawings(n_shapes: int, text: str = "") -> bytes:
    """Build a single-page PDF with ``n_shapes`` vector rectangles drawn
    on it, optionally with a text body — for exercising the vector-path
    density signal in ``_score_drawing_page``/``find_drawing_pages_scored``."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((50, 72), text)
    for i in range(n_shapes):
        page.draw_rect(fitz.Rect(10 + i, 100 + i, 30 + i, 120 + i))
    data = doc.tobytes()
    doc.close()
    return data


def _drawing_text(extra_groups: int = 4) -> str:
    """Synthesize text matching ``extra_groups`` distinct DRAWING_KEYWORDS groups."""
    chosen = DRAWING_KEYWORDS[:extra_groups]
    return "\n".join(group[0] for group in chosen)


# ---------------------------------------------------------------------------
# find_drawing_pages_by_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindDrawingPagesByText:
    def test_empty_pdf_returns_empty(self) -> None:
        pdf = _make_pdf([""])
        assert find_drawing_pages_by_text(pdf) == []

    def test_text_without_keywords_returns_empty(self) -> None:
        pdf = _make_pdf(["the quick brown fox jumps over the lazy dog"])
        assert find_drawing_pages_by_text(pdf) == []

    def test_below_group_threshold_skipped(self) -> None:
        text = _drawing_text(DRAWING_KEYWORD_THRESHOLD - 1)
        pdf = _make_pdf([text])
        assert find_drawing_pages_by_text(pdf) == []

    def test_at_threshold_selected(self) -> None:
        text = _drawing_text(DRAWING_KEYWORD_THRESHOLD)
        pdf = _make_pdf([text])
        assert find_drawing_pages_by_text(pdf) == [0]

    def test_returns_zero_indexed(self) -> None:
        text = _drawing_text(DRAWING_KEYWORD_THRESHOLD)
        pdf = _make_pdf(["", text, ""])
        assert find_drawing_pages_by_text(pdf) == [1]

    def test_spec_keyword_page_does_not_match_drawing_keywords(self) -> None:
        # A spec-table page ("rated voltage", "rated torque", ...) has no
        # bolt-circle/keyway/pilot vocabulary — the two finders should
        # target genuinely different pages, per the "different pages"
        # premise this whole feature is built on.
        spec_text = "rated voltage\nrated torque\nrated speed\nencoder"
        pdf = _make_pdf([spec_text])
        assert find_drawing_pages_by_text(pdf) == []


# ---------------------------------------------------------------------------
# _score_drawing_page
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreDrawingPage:
    def test_blank_page_scores_zero(self) -> None:
        info = _score_drawing_page("", 0)
        assert info["score"] == 0.0

    def test_vector_signal_caps_at_one(self) -> None:
        # Far above the 150-path saturation point.
        info = _score_drawing_page("", 5000)
        assert info["drawings_count"] == 5000
        assert info["score"] <= 0.45 + 1e-9

    def test_full_breadth_groups_plus_drawings_scores_high(self) -> None:
        text = "\n".join(g[0] for g in DRAWING_KEYWORDS) + "\n" + "filler\n" * 5
        info = _score_drawing_page(text, 200)
        assert info["groups_matched"] == len(DRAWING_KEYWORDS)
        assert info["score"] > 0.7

    def test_score_components_exposed(self) -> None:
        info = _score_drawing_page("bolt circle 63mm", 10)
        for key in (
            "groups_matched",
            "keyword_hits",
            "n_lines",
            "keyword_density",
            "drawings_count",
            "score",
        ):
            assert key in info


# ---------------------------------------------------------------------------
# find_drawing_pages_scored
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindDrawingPagesScored:
    def test_blank_pdf_scores_zero_and_is_not_selected(self) -> None:
        pdf = _make_pdf([""])
        pages, details = find_drawing_pages_scored(pdf)
        assert pages == []
        assert len(details) == 1
        assert details[0]["empty_text"] is True
        assert details[0]["score"] == 0.0

    def test_returns_pages_in_document_order(self) -> None:
        # Build a 3-page doc directly (helper only builds single-page docs).
        import fitz

        doc = fitz.open()
        for _ in range(3):
            page = doc.new_page()
            page.insert_text((50, 72), _drawing_text(len(DRAWING_KEYWORDS)))
            for i in range(120):
                page.draw_rect(fitz.Rect(10 + i, 100 + i, 30 + i, 120 + i))
        pdf = doc.tobytes()
        doc.close()

        pages, _ = find_drawing_pages_scored(pdf)
        assert pages == sorted(pages)
        assert pages == [0, 1, 2]

    def test_below_min_score_dropped(self) -> None:
        pdf = _make_pdf(["nothing here", "nothing there"])
        pages, _ = find_drawing_pages_scored(pdf, min_score=0.15)
        assert pages == []

    def test_text_free_page_can_still_score_on_vector_density_alone(self) -> None:
        # No drawing-keyword text at all, but heavy line art — real
        # dimensional drawings are frequently near-text-free.
        pdf = _make_pdf_with_drawings(400, text="")
        pages, details = find_drawing_pages_scored(pdf, min_score=0.15)
        assert pages == [0]
        assert details[0]["empty_text"] is True

    def test_cap_engages_on_large_doc(self) -> None:
        import fitz

        doc = fitz.open()
        for _ in range(25):
            page = doc.new_page()
            page.insert_text((50, 72), _drawing_text(len(DRAWING_KEYWORDS)))
            for i in range(150):
                page.draw_rect(fitz.Rect(10 + i, 100 + i, 30 + i, 120 + i))
        pdf = doc.tobytes()
        doc.close()

        pages, details = find_drawing_pages_scored(pdf)
        assert len(pages) <= _MAX_PAGES_LARGE_DOC
        assert len(details) == 25

    def test_explicit_max_pages_overrides_adaptive(self) -> None:
        import fitz

        doc = fitz.open()
        for _ in range(5):
            page = doc.new_page()
            page.insert_text((50, 72), _drawing_text(len(DRAWING_KEYWORDS)))
            for i in range(150):
                page.draw_rect(fitz.Rect(10 + i, 100 + i, 30 + i, 120 + i))
        pdf = doc.tobytes()
        doc.close()

        pages, _ = find_drawing_pages_scored(pdf, max_pages=2)
        assert len(pages) == 2

    def test_small_doc_cap_is_generous(self) -> None:
        import fitz

        doc = fitz.open()
        for _ in range(10):
            page = doc.new_page()
            page.insert_text((50, 72), _drawing_text(len(DRAWING_KEYWORDS)))
            for i in range(150):
                page.draw_rect(fitz.Rect(10 + i, 100 + i, 30 + i, 120 + i))
        pdf = doc.tobytes()
        doc.close()

        pages, _ = find_drawing_pages_scored(pdf)
        assert len(pages) <= _MAX_PAGES_SMALL_DOC
        assert pages == list(range(10))

    @patch("specodex.page_finder.find_drawing_pages_by_text")
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
            pages, details = find_drawing_pages_scored(b"")
        assert pages == [3, 7]
        assert details == []

    def test_get_drawings_failure_is_swallowed(self) -> None:
        text = _drawing_text(len(DRAWING_KEYWORDS))
        pdf = _make_pdf([text])

        import fitz

        real_open = fitz.open

        def fake_open(*args: object, **kwargs: object) -> object:
            doc = real_open(*args, **kwargs)
            for i in range(len(doc)):
                doc[i].get_drawings = MagicMock(side_effect=RuntimeError("boom"))
            return doc

        with patch.object(fitz, "open", side_effect=fake_open):
            pages, details = find_drawing_pages_scored(pdf, min_score=0.15)
        assert details[0]["drawings_count"] == 0
