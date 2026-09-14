"""Property tests for the dimensional-drawing page finder in
``specodex.page_finder``.

Sibling to ``tests/unit/test_drawing_page_finder.py`` (example-based)
and to ``tests/unit/test_page_finder_property.py``, which covers the
spec-table finder's twin, ``find_spec_pages_by_text``. The drawing
finder is the newer of the pair — it feeds
``./Quickstart mounting-extract`` — and shipped without a Hypothesis
companion, so this file closes that gap per CLAUDE.md "Property testing
— adversarial by default".

Three surfaces, three contracts:

1. ``find_drawing_pages_by_text(bytes)`` — eats raw untrusted PDF bytes.
   Either returns a well-formed 0-indexed page list or raises a *known
   PyMuPDF exception type*. A ``KeyError`` / ``TypeError`` /
   ``AttributeError`` escaping is the regression to catch.
2. ``_score_drawing_page(text, drawings_count)`` — pure, total over any
   ``str`` and any non-negative path count (the only thing a caller can
   hand it, since it comes from ``len(page.get_drawings())``). Returns
   the documented dict shape with ``score`` in ``[0, 1]``, and is
   monotonic in ``drawings_count``: more line art never lowers the score.
3. ``find_drawing_pages_scored(...)`` — the selection contract. Every
   returned page is in range, unique, sorted, scored at or above
   ``min_score``, and the result never exceeds the cap.

**Bug found by this round (fixed in the same PR):** a negative
``max_pages`` was a Python negative slice, so the cap *expanded* the
selection instead of shrinking it — ``max_pages=-1`` on a 6-candidate
document returned 5 pages. Reachable from ``./Quickstart
mounting-extract --max-pages -1`` (argparse ``type=int``, no lower
bound), and every extra selected page is another billed Gemini image
call. ``find_spec_pages_scored`` carried the identical slice. Explicit
regression cases live in the example-based siblings.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.page_finder import (
    DRAWING_KEYWORDS,
    _MIN_DRAWING_SCORE,
    _score_drawing_page,
    find_drawing_pages_by_text,
    find_drawing_pages_scored,
)


# ``fitz.open`` is slow per call, so keep the byte-level searches modest;
# the pure scorer is cheap and gets the routine 200.
_MAX_BYTES_EXAMPLES = 100
_MAX_BYTE_SIZE = 256
_MAX_SCORER_EXAMPLES = 200
_MAX_PDF_EXAMPLES = 25

_SCORE_KEYS = (
    "groups_matched",
    "keyword_hits",
    "n_lines",
    "keyword_density",
    "drawings_count",
    "score",
)


def _expected_fitz_exceptions() -> tuple[type, ...]:
    """Known fitz exception types the finder may surface on bad bytes.

    Resolved at import time so a failure here tells us PyMuPDF changed
    its exception hierarchy rather than that our code regressed.
    """
    try:
        import fitz
    except ImportError:
        # No fitz → the finder returns [] for everything, never raises.
        return ()
    exc_types: list[type] = []
    for name in ("FileDataError", "EmptyFileError"):
        exc = getattr(fitz, name, None)
        if exc is not None:
            exc_types.append(exc)
    return tuple(exc_types)


_FITZ_EXC_TYPES = _expected_fitz_exceptions()


def _is_well_formed_page_list(result: object) -> bool:
    """Page-list invariant: sorted, unique, non-negative ints."""
    if not isinstance(result, list):
        return False
    if not all(
        isinstance(p, int) and not isinstance(p, bool) and p >= 0 for p in result
    ):
        return False
    if result != sorted(result):
        return False
    if len(set(result)) != len(result):
        return False
    return True


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Text as PyMuPDF's ``get_text()`` could plausibly return it: arbitrary
# unicode, newline-laced, sometimes carrying real drawing vocabulary so
# the keyword branches actually get exercised rather than always
# scoring 0.
_KEYWORDS = [kw for group in DRAWING_KEYWORDS for kw in group]

_page_text = st.one_of(
    st.text(max_size=400),
    st.lists(
        st.one_of(st.sampled_from(_KEYWORDS), st.text(max_size=40)),
        max_size=30,
    ).map("\n".join),
)

# ``drawings_count`` is always ``len(page.get_drawings())`` — a list
# length, so a non-negative int. That is the contract's input domain.
_drawings_count = st.integers(min_value=0, max_value=10_000)


# ---------------------------------------------------------------------------
# 1. find_drawing_pages_by_text — untrusted bytes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindDrawingPagesByTextProperties:
    """Adversarial bytes vs the documented contract."""

    @given(pdf_bytes=st.binary(min_size=0, max_size=_MAX_BYTE_SIZE))
    @settings(
        max_examples=_MAX_BYTES_EXAMPLES,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
    )
    def test_only_known_exception_types_or_well_formed_list(
        self, pdf_bytes: bytes
    ) -> None:
        try:
            result = find_drawing_pages_by_text(pdf_bytes)
        except _FITZ_EXC_TYPES:
            return  # Documented bad-bytes contract.
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"find_drawing_pages_by_text raised {type(exc).__name__} "
                f"(expected a fitz typed exception or success): {exc!r}\n"
                f"input bytes: {pdf_bytes!r}"
            )
        assert _is_well_formed_page_list(result), (
            f"unexpected result shape: {result!r}\ninput bytes: {pdf_bytes!r}"
        )

    @given(tail=st.binary(min_size=0, max_size=_MAX_BYTE_SIZE))
    @settings(
        max_examples=_MAX_BYTES_EXAMPLES,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
    )
    def test_pdf_magic_with_garbage_tail_no_unexpected_exceptions(
        self, tail: bytes
    ) -> None:
        """The "PyMuPDF-thinks-it's-a-PDF, actually-isn't" corner."""
        pdf_bytes = b"%PDF-1.4\n" + tail
        try:
            result = find_drawing_pages_by_text(pdf_bytes)
        except _FITZ_EXC_TYPES:
            return
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"find_drawing_pages_by_text raised {type(exc).__name__} on "
                f"PDF-magic+garbage: {exc!r}\ninput: {pdf_bytes!r}"
            )
        assert _is_well_formed_page_list(result)


# ---------------------------------------------------------------------------
# 2. _score_drawing_page — pure scorer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreDrawingPageProperties:
    """The scorer is pure and total over its documented input domain."""

    @given(text=_page_text, drawings_count=_drawings_count)
    @settings(max_examples=_MAX_SCORER_EXAMPLES, deadline=None)
    def test_returns_documented_shape(self, text: str, drawings_count: int) -> None:
        info = _score_drawing_page(text, drawings_count)
        assert isinstance(info, dict)
        for key in _SCORE_KEYS:
            assert key in info, f"missing key {key!r} in {info!r}"
        assert info["drawings_count"] == drawings_count
        # ``n_lines`` is floored at 1 so it can be a denominator.
        assert isinstance(info["n_lines"], int) and info["n_lines"] >= 1
        assert 0 <= info["groups_matched"] <= len(DRAWING_KEYWORDS)
        assert info["keyword_hits"] >= 0
        assert info["keyword_density"] >= 0

    @given(text=_page_text, drawings_count=_drawings_count)
    @settings(max_examples=_MAX_SCORER_EXAMPLES, deadline=None)
    def test_score_is_a_bounded_finite_float(
        self, text: str, drawings_count: int
    ) -> None:
        """The composite weights sum to 1.0, so the score is in [0, 1].

        ``find_drawing_pages_scored`` compares this against
        ``min_score``; a score outside the unit interval would make that
        threshold meaningless.
        """
        score = _score_drawing_page(text, drawings_count)["score"]
        assert isinstance(score, float)
        assert score == score, f"NaN score for {text!r} / {drawings_count}"  # noqa: PLR0124
        assert 0.0 <= score <= 1.0, (
            f"score {score} out of [0, 1] for text={text!r} "
            f"drawings_count={drawings_count}"
        )

    @given(
        text=_page_text,
        low=_drawings_count,
        delta=st.integers(min_value=0, max_value=10_000),
    )
    @settings(max_examples=_MAX_SCORER_EXAMPLES, deadline=None)
    def test_score_is_monotonic_in_vector_density(
        self, text: str, low: int, delta: int
    ) -> None:
        """More line art never lowers the drawing score.

        This is the whole premise of the vector-path signal: a
        dimensional drawing is sparse text over dense line art.
        """
        lo = _score_drawing_page(text, low)["score"]
        hi = _score_drawing_page(text, low + delta)["score"]
        assert hi >= lo - 1e-9, (
            f"score dropped from {lo} to {hi} when drawings_count went "
            f"{low} → {low + delta} (text={text!r})"
        )

    @given(text=_page_text, drawings_count=_drawings_count)
    @settings(max_examples=_MAX_SCORER_EXAMPLES, deadline=None)
    def test_deterministic(self, text: str, drawings_count: int) -> None:
        """No hidden state — the same page always scores the same."""
        assert _score_drawing_page(text, drawings_count) == _score_drawing_page(
            text, drawings_count
        )


# ---------------------------------------------------------------------------
# 3. find_drawing_pages_scored — selection contract
# ---------------------------------------------------------------------------


def _make_pdf(page_specs: list[tuple[str, int]]) -> bytes:
    """Build a PDF from (text, n_shapes) pairs, one page each."""
    import fitz

    doc = fitz.open()
    for text, n_shapes in page_specs:
        page = doc.new_page()
        if text:
            page.insert_text((50, 72), text)
        for i in range(n_shapes):
            page.draw_rect(fitz.Rect(10 + i, 100 + i, 30 + i, 120 + i))
    data = doc.tobytes()
    doc.close()
    return data


# Keep documents small — each page is a real PyMuPDF render.
_page_spec = st.tuples(
    st.one_of(
        st.just(""),
        st.sampled_from(
            ["\n".join(g[0] for g in DRAWING_KEYWORDS), "bolt circle", "nothing here"]
        ),
    ),
    st.integers(min_value=0, max_value=200),
)


@pytest.mark.unit
class TestFindDrawingPagesScoredProperties:
    """Whatever the document, the selection obeys its own contract."""

    @given(
        page_specs=st.lists(_page_spec, min_size=1, max_size=8),
        max_pages=st.one_of(st.none(), st.integers(min_value=-5, max_value=10)),
    )
    @settings(
        max_examples=_MAX_PDF_EXAMPLES,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
    )
    def test_selection_contract(
        self, page_specs: list[tuple[str, int]], max_pages: int | None
    ) -> None:
        pdf = _make_pdf(page_specs)
        n_pages = len(page_specs)

        pages, details = find_drawing_pages_scored(pdf, max_pages=max_pages)

        # Details cover every page, selection is a well-formed subset.
        assert len(details) == n_pages
        assert _is_well_formed_page_list(pages)
        assert all(0 <= p < n_pages for p in pages)
        assert set(pages) <= {d["page"] for d in details}

        # The cap is a cap. A negative cap selects nothing — it must
        # never behave as a negative slice (the bug this round found).
        if max_pages is not None:
            assert len(pages) <= max(max_pages, 0), (
                f"selected {len(pages)} pages with max_pages={max_pages}"
            )

        # Every selected page cleared the threshold.
        by_page = {d["page"]: d for d in details}
        for p in pages:
            assert by_page[p]["score"] >= _MIN_DRAWING_SCORE

    @given(
        n_pages=st.integers(min_value=2, max_value=6),
        max_pages=st.integers(min_value=-6, max_value=0),
    )
    @settings(
        max_examples=_MAX_PDF_EXAMPLES,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
    )
    def test_non_positive_cap_selects_nothing(
        self, n_pages: int, max_pages: int
    ) -> None:
        """A cap of zero or less selects no pages — never all-but-N.

        Targeted at the negative-slice bug: every page here scores well
        above ``min_score``, so with the unclamped ``candidates[:-1]``
        the selection came back one short of the full document instead
        of empty. Generated documents rarely hit that combination by
        chance, so it gets its own property.
        """
        drawing_text = "\n".join(g[0] for g in DRAWING_KEYWORDS)
        pdf = _make_pdf([(drawing_text, 150)] * n_pages)

        # Sanity: without a cap these pages are all selected, so the
        # assertion below is about the cap and not about the threshold.
        uncapped, _ = find_drawing_pages_scored(pdf)
        assert len(uncapped) == n_pages

        pages, _ = find_drawing_pages_scored(pdf, max_pages=max_pages)
        assert pages == [], (
            f"max_pages={max_pages} selected {pages} from a {n_pages}-page "
            f"document — a non-positive cap must select nothing"
        )

    @given(
        page_specs=st.lists(_page_spec, min_size=1, max_size=6),
        min_score=st.floats(min_value=0.0, max_value=1.0),
    )
    @settings(
        max_examples=_MAX_PDF_EXAMPLES,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
    )
    def test_raising_min_score_never_widens_the_selection(
        self, page_specs: list[tuple[str, int]], min_score: float
    ) -> None:
        """``min_score`` is a floor: a higher floor selects a subset."""
        pdf = _make_pdf(page_specs)
        loose, _ = find_drawing_pages_scored(pdf, min_score=0.0)
        strict, _ = find_drawing_pages_scored(pdf, min_score=min_score)
        # Both run under the same adaptive cap, and the cap keeps the
        # highest-scoring pages, so the stricter run is a subset.
        assert set(strict) <= set(loose)
