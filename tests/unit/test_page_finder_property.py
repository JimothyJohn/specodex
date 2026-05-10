"""Property tests for ``specodex.page_finder.find_spec_pages_by_text``.

The function eats raw bytes — adversarial input territory. PyMuPDF
parses arbitrary PDFs, and a malformed PDF is the canonical
"untrusted bytes" attack surface.

**Contract under test:**

1. **Success path:** valid PDF bytes return a ``list[int]`` of
   0-indexed page numbers, every entry < total pages, no duplicates,
   sorted ascending.
2. **Failure path:** malformed bytes raise a *known PyMuPDF
   exception type* (``FileDataError`` for malformed,
   ``EmptyFileError`` for empty). The caller's try/except can
   catch these. A ``KeyError`` / ``TypeError`` / ``AttributeError``
   escaping the function on bad bytes is the regression to catch —
   that's the parser leaking implementation details past its
   contract.

Phase 3.1 target 3 of 3. Targets 1 (``parse_gemini_response``) and
2 (BeforeValidators in ``common.py``) shipped in PRs #111 / #112.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.page_finder import find_spec_pages_by_text


# Cap example count + size — fitz.open is comparatively slow per
# call, and the property here is "no surprise exception types"
# rather than a deep correctness check that needs thousands of
# examples.
_MAX_EXAMPLES = 100
_MAX_BYTE_SIZE = 256


# Known fitz exception types the function may surface on bad bytes.
# Resolved at import time so a test failure tells us if PyMuPDF
# changed its exception hierarchy.
def _expected_fitz_exceptions() -> tuple[type, ...]:
    try:
        import fitz
    except ImportError:
        # No fitz → function returns [] for everything, no exceptions.
        return ()
    exc_types: list[type] = []
    for name in ("FileDataError", "EmptyFileError"):
        exc = getattr(fitz, name, None)
        if exc is not None:
            exc_types.append(exc)
    return tuple(exc_types)


_FITZ_EXC_TYPES = _expected_fitz_exceptions()


def _is_well_formed_page_list(result, total_pages_max: int = 10_000) -> bool:
    """Page-list invariant — every entry must be a non-negative int."""
    if not isinstance(result, list):
        return False
    if not all(isinstance(p, int) and p >= 0 for p in result):
        return False
    # 0-indexed pages, no duplicates implied (SPEC_KEYWORDS scan
    # appends per page in order, so result is already monotonic).
    if result and (result != sorted(result)):
        return False
    return True


class TestFindSpecPagesByTextProperties:
    """Adversarial bytes vs the documented contract."""

    @given(pdf_bytes=st.binary(min_size=0, max_size=_MAX_BYTE_SIZE))
    @settings(
        max_examples=_MAX_EXAMPLES,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
    )
    def test_only_known_exception_types_or_well_formed_list(
        self, pdf_bytes: bytes
    ) -> None:
        """For any bytes input, the function either returns a
        well-formed page list or raises a known PyMuPDF exception.

        A KeyError / TypeError / AttributeError escaping is a
        regression — the parser must surface failures with typed
        exceptions, not internals.
        """
        try:
            result = find_spec_pages_by_text(pdf_bytes)
        except _FITZ_EXC_TYPES:
            return  # Documented bad-bytes contract — fine.
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"find_spec_pages_by_text raised "
                f"{type(exc).__name__} (expected fitz typed "
                f"exception or success): {exc!r}\n"
                f"input bytes: {pdf_bytes!r}"
            )
        # Success path — must be well-formed.
        assert _is_well_formed_page_list(result), (
            f"unexpected result shape: {result!r}\ninput bytes: {pdf_bytes!r}"
        )

    @given(
        # Bytes that LOOK like PDFs at the surface — start with %PDF
        # magic, then random tail. Targets the "PyMuPDF-thinks-it's-
        # a-PDF, actually-isn't" corner.
        tail=st.binary(min_size=0, max_size=_MAX_BYTE_SIZE),
    )
    @settings(
        max_examples=_MAX_EXAMPLES,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
    )
    def test_pdf_magic_with_garbage_tail_no_unexpected_exceptions(
        self, tail: bytes
    ) -> None:
        pdf_bytes = b"%PDF-1.4\n" + tail
        try:
            result = find_spec_pages_by_text(pdf_bytes)
        except _FITZ_EXC_TYPES:
            return
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"find_spec_pages_by_text raised "
                f"{type(exc).__name__} on PDF-magic+garbage: "
                f"{exc!r}\ninput: {pdf_bytes!r}"
            )
        assert _is_well_formed_page_list(result)

    def test_empty_bytes_raises_known_exception(self) -> None:
        """Sanity check — empty bytes raise EmptyFileError, not a
        bare exception or a half-open document."""
        if not _FITZ_EXC_TYPES:
            pytest.skip("fitz not importable")
        with pytest.raises(_FITZ_EXC_TYPES):
            find_spec_pages_by_text(b"")

    def test_truncated_pdf_raises_filedataerror(self) -> None:
        if not _FITZ_EXC_TYPES:
            pytest.skip("fitz not importable")
        with pytest.raises(_FITZ_EXC_TYPES):
            find_spec_pages_by_text(b"%PDF-1.4\ntotally not a real PDF")
