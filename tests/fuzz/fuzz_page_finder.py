"""Atheris fuzz target for PDF intake (todo/HARDENING.md Phase 3.2).

``specodex/page_finder.py`` hands arbitrary bytes to PyMuPDF. Every
datasheet the scraper downloads goes through it before any LLM call,
so it is the repo's classic untrusted-bytes parser. The Hypothesis
companions (``tests/unit/test_page_finder_property.py``,
``test_drawing_page_finder_property.py``) cover small ``st.binary``
blobs; this target lets libFuzzer mutate *valid* PDFs from the seed
corpus, which reaches the page-iteration and scoring code that
random bytes almost never get past ``fitz.open`` to see.

**Contract under test** (same as the property tests, applied to all
four finders):

1. Bad bytes raise ``fitz.FileDataError`` (``EmptyFileError`` is a
   subclass) or nothing. Any other exception type escaping is a crash.
2. The by-text finders return a sorted, duplicate-free ``list[int]``
   of non-negative page indices.
3. The scored finders return ``(pages, details)`` where ``pages`` is
   sorted, duplicate-free, within ``range(len(details))``, and each
   detail's ``page`` equals its position.

``check_one_input`` is plain Python with no atheris import, so the
committed corpus also replays in the normal unit suite
(``tests/unit/test_fuzz_corpus_replay.py``) where atheris isn't
installed.

Run a fuzz session on Linux (atheris lives in the opt-in ``fuzz``
dependency group; it publishes Linux x86_64 wheels only, so on macOS
run it in a Linux container)::

    uv run --group fuzz python tests/fuzz/fuzz_page_finder.py \\
        tests/fuzz/corpus -max_total_time=600

On a crash libFuzzer writes ``crash-<sha>`` to the cwd. Minimise it
(``-minimize_crash=1``), commit the minimised input under
``tests/fuzz/corpus/`` so the replay test pins it, then fix.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

CORPUS_DIR = Path(__file__).parent / "corpus"


def _quiet_fitz():
    import fitz

    # MuPDF prints a line to stderr for every repair it attempts on a
    # malformed xref; at fuzzing rates that drowns libFuzzer's status.
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)
    return fitz


def _check_page_list(pages: object, label: str, total: int | None = None) -> None:
    assert isinstance(pages, list), f"{label}: {type(pages).__name__} not list"
    for p in pages:
        assert type(p) is int and p >= 0, f"{label}: bad page index {p!r}"
        if total is not None:
            assert p < total, f"{label}: page {p} >= total {total}"
    assert pages == sorted(set(pages)), f"{label}: not sorted/unique: {pages!r}"


def _check_scored(result: object, label: str) -> None:
    assert isinstance(result, tuple) and len(result) == 2, f"{label}: {result!r}"
    pages, details = result
    assert isinstance(details, list), f"{label}: details not list"
    for i, d in enumerate(details):
        assert isinstance(d, dict) and d.get("page") == i, f"{label}: detail {i}"
    _check_page_list(pages, label, total=len(details))


def check_one_input(data: bytes) -> None:
    """Run every page finder over ``data`` and assert the contract.

    Raises ``AssertionError`` on a contract violation and lets any
    exception outside ``fitz.FileDataError`` propagate — both are
    crashes from the fuzzer's point of view.
    """
    from specodex import page_finder as pf

    fitz = _quiet_fitz()
    try:
        _check_page_list(pf.find_spec_pages_by_text(data), "spec_by_text")
        _check_scored(pf.find_spec_pages_scored(data), "spec_scored")
        _check_page_list(pf.find_drawing_pages_by_text(data), "drawing_by_text")
        _check_scored(pf.find_drawing_pages_scored(data), "drawing_scored")
    except fitz.FileDataError:
        pass


def make_seed_corpus() -> dict[str, bytes]:
    """Build the committed seed PDFs.

    Kept as code so the seeds are reproducible and reviewable: one
    page that clears the spec-keyword threshold, one that clears the
    drawing threshold (text + vector paths), a blank page, and a
    three-page mix. ``tests/fuzz/corpus/*.pdf`` is this function's
    output; regenerate with ``python tests/fuzz/fuzz_page_finder.py
    --write-seeds``.
    """
    fitz = _quiet_fitz()

    def _pdf(pages: list[tuple[str, bool]]) -> bytes:
        doc = fitz.open()
        for text, draw in pages:
            page = doc.new_page(width=300, height=300)
            if text:
                page.insert_text((20, 40), text, fontsize=8)
            if draw:
                for r in range(20, 120, 20):
                    page.draw_circle((150, 200), r)
                page.draw_line((20, 280), (280, 280))
        out = doc.tobytes(garbage=4, deflate=True)
        doc.close()
        return out

    spec = (
        "Specifications\nRated voltage 230 V\nRated current 4.2 A\n"
        "Rated torque 1.3 Nm\nRated speed 3000 rpm\nWeight 1.2 kg"
    )
    drawing = (
        "Dimensional drawing\nBolt circle 100 mm\nKeyway DIN 6885\nPilot diameter 80 mm"
    )
    return {
        "seed_spec_page.pdf": _pdf([(spec, False)]),
        "seed_drawing_page.pdf": _pdf([(drawing, True)]),
        "seed_blank_page.pdf": _pdf([("", False)]),
        "seed_mixed_3page.pdf": _pdf([(spec, False), ("", True), (drawing, True)]),
    }


def _write_seeds() -> None:
    CORPUS_DIR.mkdir(exist_ok=True)
    for name, data in make_seed_corpus().items():
        (CORPUS_DIR / name).write_bytes(data)


def main() -> None:
    if "--write-seeds" in sys.argv:
        _write_seeds()
        return

    import atheris

    logging.disable(logging.CRITICAL)
    with atheris.instrument_imports():
        import specodex.page_finder  # noqa: F401

    atheris.Setup(sys.argv, check_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
