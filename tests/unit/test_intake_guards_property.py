"""Property tests for ``cli.intake_guards``.

The intake guards sit at the promotion boundary between S3 ``triage/``
and ``good_examples/``. Every PDF that survives the guards produces a
Datasheet row in DynamoDB and drives downstream Gemini extraction.
A guard that silently promotes garbage (or blocks a real datasheet
with an unhandled exception) either poisons the catalogue or halts
the intake worker mid-queue.

The example-based companion (``test_intake_guards.py``) pins specific
shapes. This file generates *adversarial* inputs — arbitrary bytes for
the file-integrity guard, unicode-laced strings for the manufacturer
check, extremes on the density scale — and asserts the documented
contract holds for every input the strategies can produce.

**Contract under test** (per each guard's docstring):

1. **Totality.** Every guard returns a ``GuardVerdict`` for every input
   the dispatch loop could plausibly feed it. Guards run inside the
   ``run_guards`` orchestrator with no ``try/except`` around them —
   a leak crashes ``./Quickstart process``.
2. **``guard_name`` is stable.** Each guard's verdict carries its own
   name verbatim; the ``run_guards`` return list must expose 4 unique
   names (one per post-scan guard).
3. **File integrity.** ``passed=True`` iff bytes are non-empty, at
   least ``_MIN_PDF_SIZE`` long, AND start with the ``%PDF`` magic.
   Every rejection carries a non-empty ``reason``. The HTML sniffer
   only fires when the first 4 bytes are not ``%PDF`` AND the head
   contains a recognisable HTML marker.
4. **Manufacturer identity.** ``passed=True`` iff
   ``mfr.strip().lower()`` is non-empty AND not in
   ``GENERIC_MANUFACTURERS``. Insensitive to surrounding whitespace
   and case.
5. **Extraction feasibility.** ``passed=True`` iff the signal count
   (0..4) is ``>= 2``. Each signal is a documented predicate over
   one scan field; changing the threshold or the predicate set is a
   contract change and must break the property.
6. **Spec-density calibration.** ``passed=True`` iff ``density >=
   threshold`` where the threshold is the per-type entry from
   ``_DENSITY_THRESHOLDS`` (or the default). Missing density is
   treated as 0.0.
7. **``any_blocking``.** Returns the first verdict with
   ``not passed and severity == "block"``, else ``None``. ``warn``
   severity is never returned even if ``passed`` is False.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from cli.intake import IntakeScanResult
from cli.intake_guards import (
    _DEFAULT_DENSITY_THRESHOLD,
    _DENSITY_THRESHOLDS,
    _MIN_PDF_SIZE,
    GuardVerdict,
    any_blocking,
    check_document_scope,
    check_extraction_feasibility,
    check_file_integrity,
    check_manufacturer_identity,
    check_spec_density_calibrated,
    run_guards,
)
from specodex.spec_rules import GENERIC_MANUFACTURERS


# ---------------------------------------------------------------------------
# Input strategies
# ---------------------------------------------------------------------------


# Arbitrary bytes — mostly not PDFs. Exercises the "no magic bytes"
# path, the "too small" path, and the "empty file" path.
_ARBITRARY_BYTES = st.binary(min_size=0, max_size=4096)


# Bytes that DO carry the %PDF magic but may still be below the size
# floor. Pins the "size gate" vs "magic gate" ordering.
@st.composite
def _pdf_bytes_with_random_tail(draw: st.DrawFn) -> bytes:
    tail = draw(st.binary(min_size=0, max_size=4096))
    return b"%PDF-1.4 " + tail


# HTML-shaped bytes — must trigger the HTML sniffer (falsy magic +
# recognisable head). Padded past ``_MIN_PDF_SIZE`` so the size check
# doesn't short-circuit the magic + HTML check.
@st.composite
def _html_bytes(draw: st.DrawFn) -> bytes:
    prefix = draw(
        st.sampled_from(
            [
                b"<html>",
                b"<HTML>",
                b"<!doctype html>",
                b"<!DOCTYPE HTML>",
                b"  <html lang='en'>",
            ]
        )
    )
    body = draw(st.binary(min_size=0, max_size=2048))
    pad = b"\x00" * max(0, _MIN_PDF_SIZE - len(prefix) - len(body))
    return prefix + body + pad


_BYTE_INPUTS = st.one_of(
    _ARBITRARY_BYTES,
    _pdf_bytes_with_random_tail(),
    _html_bytes(),
)


# Manufacturer strings — a mix of canonical GENERIC_MANUFACTURERS
# entries (with re-casing + whitespace decoration), the "clearly-real"
# vendor names the example tests pin, and arbitrary unicode.
_GENERIC_MFRS = st.sampled_from(sorted(GENERIC_MANUFACTURERS))
_KNOWN_REAL_MFRS = st.sampled_from(
    ["Kollmorgen", "Yaskawa", "Allen-Bradley", "Siemens", "ABB", "Portescap"]
)
_UNICODE_MFRS = st.text(
    alphabet=st.characters(min_codepoint=0, max_codepoint=0x10FFFF),
    min_size=0,
    max_size=30,
)
_WHITESPACE_RUNS = st.text(alphabet=" \t\n\r", min_size=0, max_size=4)


@st.composite
def _decorated_generic_mfr(draw: st.DrawFn) -> str:
    base = draw(_GENERIC_MFRS)
    style = draw(st.sampled_from(["lower", "upper", "title", "mixed"]))
    if style == "lower":
        cased = base.lower()
    elif style == "upper":
        cased = base.upper()
    elif style == "title":
        cased = base.title()
    else:
        flips = draw(st.lists(st.booleans(), min_size=len(base), max_size=len(base)))
        cased = "".join(
            c.upper() if f and c.isalpha() else c for c, f in zip(base, flips)
        )
    left = draw(_WHITESPACE_RUNS)
    right = draw(_WHITESPACE_RUNS)
    return left + cased + right


_MANUFACTURER_INPUTS = st.one_of(
    st.none(),
    _decorated_generic_mfr(),
    _KNOWN_REAL_MFRS,
    _UNICODE_MFRS,
    st.text(min_size=0, max_size=40),
)


# Densities — cover the boundary of every documented threshold plus
# extremes (negative, > 1, NaN-adjacent, None).
_DENSITIES = st.one_of(
    st.none(),
    st.floats(min_value=-1.0, max_value=2.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([0.0, 0.1, 0.19, 0.2, 0.24, 0.25, 0.5, 1.0]),
)


# Product types — the real Literal set covered by ``_DENSITY_THRESHOLDS``
# plus a handful of "not-in-table" strings that must land on the default.
_KNOWN_PRODUCT_TYPES = st.sampled_from(sorted(_DENSITY_THRESHOLDS))
_UNKNOWN_PRODUCT_TYPES = st.sampled_from(
    ["widget", "", "contactor", "linear_actuator", "electric_cylinder", "unknown"]
)
_PRODUCT_TYPES = st.one_of(
    st.none(),
    _KNOWN_PRODUCT_TYPES,
    _UNKNOWN_PRODUCT_TYPES,
    st.text(min_size=0, max_size=20),
)


# Spec-page lists — including the "None" and empty-list shapes the
# guard treats as "no signal".
_SPEC_PAGES = st.one_of(
    st.none(),
    st.lists(st.integers(min_value=-5, max_value=500), min_size=0, max_size=10),
)


def _scan(**overrides: Any) -> IntakeScanResult:
    """Build an ``IntakeScanResult`` with the given overrides applied.

    Defaults come from the example test's ``_scan`` helper — a fully
    populated "healthy" scan — so overriding one field at a time lets
    a Hypothesis strategy vary that field in isolation without also
    accidentally flipping unrelated signals.
    """
    defaults: dict[str, Any] = {
        "is_valid_datasheet": True,
        "has_table_of_contents": True,
        "has_specification_tables": True,
        "product_type": "motor",
        "manufacturer": "Acme Corp",
        "product_name": "X100",
        "product_family": "X-Series",
        "category": "brushless dc motor",
        "spec_pages": [3, 4, 5],
        "spec_density": 0.7,
        "rejection_reason": None,
        "distinct_product_count": 5,
        "is_multi_category": False,
    }
    defaults.update(overrides)
    return IntakeScanResult(**defaults)


# ---------------------------------------------------------------------------
# check_file_integrity
# ---------------------------------------------------------------------------


class TestCheckFileIntegrityContract:
    """The file-integrity gate accepts arbitrary bytes off the wire —
    HTML redirect pages, truncated downloads, empty responses. It must
    survive every one of them and return an actionable verdict."""

    @given(pdf_bytes=_BYTE_INPUTS)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_returns_verdict_and_never_raises(self, pdf_bytes: bytes) -> None:
        """Rule 1: totality. The dispatch loop feeds this whatever the
        S3 download produced — that includes empty bodies, gzip-mangled
        streams, and half-transferred bytes. Any exception leak crashes
        the worker."""
        try:
            verdict = check_file_integrity(pdf_bytes)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"check_file_integrity raised {type(exc).__name__}: {exc!r}\n"
                f"input len: {len(pdf_bytes)}"
            )
        assert isinstance(verdict, GuardVerdict)
        assert verdict.guard_name == "file_integrity"

    @given(pdf_bytes=_BYTE_INPUTS)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_pass_condition_matches_documented_predicate(
        self, pdf_bytes: bytes
    ) -> None:
        """Rule 3: ``passed=True`` iff bytes are non-empty AND at least
        ``_MIN_PDF_SIZE`` AND start with the ``%PDF`` magic. Any drift
        (e.g. dropping the size gate, requiring ``%PDF-1.x`` versioning)
        surfaces here."""
        verdict = check_file_integrity(pdf_bytes)
        should_pass = len(pdf_bytes) >= _MIN_PDF_SIZE and pdf_bytes[:4].startswith(
            b"%PDF"
        )
        assert verdict.passed is should_pass, (
            f"check_file_integrity passed={verdict.passed} but predicate says "
            f"{should_pass}; len={len(pdf_bytes)}, head={pdf_bytes[:8]!r}"
        )

    @given(pdf_bytes=_BYTE_INPUTS)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_rejections_have_nonempty_reason(self, pdf_bytes: bytes) -> None:
        """Rule 3 addendum: every rejection carries a reason string the
        operator can act on. A blank reason means the ingest-report CLI
        renders an empty bullet and the operator has to open the raw
        bytes to figure out why the PDF was blocked."""
        verdict = check_file_integrity(pdf_bytes)
        if verdict.passed:
            return
        assert isinstance(verdict.reason, str) and verdict.reason.strip(), (
            f"check_file_integrity rejected input with blank reason; "
            f"len={len(pdf_bytes)}"
        )

    @given(html=_html_bytes())
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test_html_sniffer_fires_on_redirect_pages(self, html: bytes) -> None:
        """The HTML sniffer is the only reason we can name-and-shame a
        specific error mode to the operator; without it, the reason
        just says "missing %PDF magic" and the operator has to inspect
        the bytes to guess it was an HTTP 301. Pin that the sniffer
        actually fires for HTML-shaped bodies past the size floor."""
        verdict = check_file_integrity(html)
        assert verdict.passed is False
        assert "HTML" in verdict.reason


class TestCheckFileIntegrityRegressions:
    """Explicit shapes that pin adversarial edge cases discovered
    while writing the property test."""

    def test_exactly_min_size_pdf_passes(self) -> None:
        """The size gate is ``>=``, not ``>``. Pin the boundary so a
        future refactor to ``>`` silently drops the tightest valid PDF."""
        pdf = b"%PDF-1.4" + b"\x00" * (_MIN_PDF_SIZE - 8)
        assert len(pdf) == _MIN_PDF_SIZE
        assert check_file_integrity(pdf).passed is True

    def test_one_byte_below_min_size_blocked(self) -> None:
        pdf = b"%PDF" + b"\x00" * (_MIN_PDF_SIZE - 5)
        assert len(pdf) == _MIN_PDF_SIZE - 1
        v = check_file_integrity(pdf)
        assert v.passed is False
        assert "too small" in v.reason

    def test_html_at_min_size_is_reported_as_html(self) -> None:
        html = b"<html>" + b" " * (_MIN_PDF_SIZE - 6)
        v = check_file_integrity(html)
        assert v.passed is False
        assert "HTML" in v.reason

    def test_uppercase_doctype_still_html(self) -> None:
        html = b"<!DOCTYPE HTML>" + b" " * _MIN_PDF_SIZE
        v = check_file_integrity(html)
        assert v.passed is False
        assert "HTML" in v.reason


# ---------------------------------------------------------------------------
# check_manufacturer_identity
# ---------------------------------------------------------------------------


class TestCheckManufacturerIdentityContract:
    """Manufacturer strings come from the Gemini triage scan — meaning
    they can be any unicode-laced value the LLM produced. The guard
    must classify every one of them without raising."""

    @given(mfr=_MANUFACTURER_INPUTS)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_returns_verdict_and_never_raises(self, mfr: Any) -> None:
        scan = _scan(manufacturer=mfr)
        try:
            verdict = check_manufacturer_identity(scan)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"check_manufacturer_identity raised {type(exc).__name__}: "
                f"{exc!r}\ninput: {mfr!r}"
            )
        assert isinstance(verdict, GuardVerdict)
        assert verdict.guard_name == "manufacturer_identity"

    @given(mfr=_MANUFACTURER_INPUTS)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_pass_condition_matches_documented_predicate(self, mfr: Any) -> None:
        """``passed=True`` iff ``mfr.strip().lower()`` is non-empty AND
        not in ``GENERIC_MANUFACTURERS``. The predicate is repeated here
        so a drift (e.g. dropping ``.strip()``) fails loudly."""
        scan = _scan(manufacturer=mfr)
        verdict = check_manufacturer_identity(scan)
        normalized = (mfr or "").strip().lower()
        should_pass = bool(normalized) and normalized not in GENERIC_MANUFACTURERS
        assert verdict.passed is should_pass, (
            f"check_manufacturer_identity passed={verdict.passed} but predicate "
            f"says {should_pass}; input={mfr!r}, normalized={normalized!r}"
        )

    @given(mfr=_KNOWN_REAL_MFRS, left=_WHITESPACE_RUNS, right=_WHITESPACE_RUNS)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_whitespace_wrapping_does_not_change_verdict(
        self, mfr: str, left: str, right: str
    ) -> None:
        """Surrounding whitespace off a real vendor name must not
        block promotion. Pins that ``.strip()`` is applied before the
        set membership check."""
        base = check_manufacturer_identity(_scan(manufacturer=mfr))
        wrapped = check_manufacturer_identity(_scan(manufacturer=left + mfr + right))
        assert base.passed == wrapped.passed

    @given(mfr=_GENERIC_MFRS)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_every_generic_variant_blocks(self, mfr: str) -> None:
        """Every canonical generic-manufacturer token, in every casing,
        must classify as a rejection. Pins the set membership +
        case-insensitivity together."""
        for cased in (mfr, mfr.upper(), mfr.title()):
            verdict = check_manufacturer_identity(_scan(manufacturer=cased))
            assert verdict.passed is False, (
                f"generic manufacturer {cased!r} classified as real"
            )


# ---------------------------------------------------------------------------
# check_document_scope
# ---------------------------------------------------------------------------


class TestCheckDocumentScopeContract:
    """Document scope is a single-boolean check — trivially adversarial
    only in that the property must survive missing / wrong-typed
    values via the ``getattr(..., False)`` fallback."""

    @given(is_multi=st.booleans())
    @settings(max_examples=20)
    def test_pass_iff_single_category(self, is_multi: bool) -> None:
        scan = _scan(is_multi_category=is_multi)
        verdict = check_document_scope(scan)
        assert verdict.guard_name == "document_scope"
        assert verdict.passed is (not is_multi)
        if is_multi:
            assert "multi-category" in verdict.reason


# ---------------------------------------------------------------------------
# check_extraction_feasibility
# ---------------------------------------------------------------------------


class TestCheckExtractionFeasibilityContract:
    """Feasibility is a "count the signals" check — the property
    guarantees the count matches the documented predicate set for
    every field combination the strategies produce."""

    @given(
        mfr=_MANUFACTURER_INPUTS,
        product_name=st.one_of(st.none(), st.text(min_size=0, max_size=30)),
        spec_pages=_SPEC_PAGES,
        density=_DENSITIES,
    )
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_returns_verdict_and_never_raises(
        self,
        mfr: Any,
        product_name: Any,
        spec_pages: Any,
        density: Any,
    ) -> None:
        scan = _scan(
            manufacturer=mfr,
            product_name=product_name,
            spec_pages=spec_pages,
            spec_density=density,
        )
        try:
            verdict = check_extraction_feasibility(scan)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"check_extraction_feasibility raised {type(exc).__name__}: "
                f"{exc!r}\n"
                f"mfr={mfr!r}, name={product_name!r}, pages={spec_pages!r}, "
                f"density={density!r}"
            )
        assert isinstance(verdict, GuardVerdict)
        assert verdict.guard_name == "extraction_feasibility"

    @given(
        mfr=_MANUFACTURER_INPUTS,
        product_name=st.one_of(st.none(), st.text(min_size=0, max_size=30)),
        spec_pages=_SPEC_PAGES,
        density=_DENSITIES,
    )
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_pass_iff_at_least_two_signals(
        self,
        mfr: Any,
        product_name: Any,
        spec_pages: Any,
        density: Any,
    ) -> None:
        """The documented predicate set: manufacturer non-empty,
        product_name non-empty, spec_pages truthy list, density
        ``>= 0.2``. Threshold is ``>= 2`` signals. The property
        recomputes the signal count independently and asserts the
        guard agrees."""
        scan = _scan(
            manufacturer=mfr,
            product_name=product_name,
            spec_pages=spec_pages,
            spec_density=density,
        )
        expected_signals = 0
        if mfr and mfr.strip():
            expected_signals += 1
        if product_name and product_name.strip():
            expected_signals += 1
        if spec_pages and len(spec_pages) > 0:
            expected_signals += 1
        if (density or 0.0) >= 0.2:
            expected_signals += 1

        verdict = check_extraction_feasibility(scan)
        should_pass = expected_signals >= 2
        assert verdict.passed is should_pass, (
            f"check_extraction_feasibility passed={verdict.passed}, "
            f"expected {should_pass}; signals={expected_signals}; "
            f"mfr={mfr!r}, name={product_name!r}, pages={spec_pages!r}, "
            f"density={density!r}"
        )


# ---------------------------------------------------------------------------
# check_spec_density_calibrated
# ---------------------------------------------------------------------------


class TestCheckSpecDensityCalibratedContract:
    """The calibrated-density guard is a per-type threshold table.
    The property checks the table drives the verdict for every
    (product_type, density) pair the strategies produce."""

    @given(product_type=_PRODUCT_TYPES, density=_DENSITIES)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_pass_iff_density_at_or_above_threshold(
        self, product_type: Any, density: Any
    ) -> None:
        scan = _scan(product_type=product_type, spec_density=density)
        verdict = check_spec_density_calibrated(scan)
        assert verdict.guard_name == "spec_density_calibrated"

        effective_density = density or 0.0
        expected_threshold = _DENSITY_THRESHOLDS.get(
            product_type or "", _DEFAULT_DENSITY_THRESHOLD
        )
        should_pass = effective_density >= expected_threshold
        assert verdict.passed is should_pass, (
            f"check_spec_density_calibrated passed={verdict.passed}, expected "
            f"{should_pass}; density={density!r}, type={product_type!r}, "
            f"threshold={expected_threshold}"
        )

    @given(product_type=_KNOWN_PRODUCT_TYPES)
    @settings(max_examples=50, deadline=None)
    def test_threshold_boundary_passes(self, product_type: str) -> None:
        """At exactly the threshold density, the guard must pass.
        Pins the ``>=`` comparison so a future refactor to ``>``
        blocks datasheets that land exactly on the calibrated line."""
        threshold = _DENSITY_THRESHOLDS[product_type]
        v = check_spec_density_calibrated(
            _scan(product_type=product_type, spec_density=threshold)
        )
        assert v.passed is True

    @given(product_type=_UNKNOWN_PRODUCT_TYPES)
    @settings(max_examples=50, deadline=None)
    def test_unknown_type_uses_default_threshold(self, product_type: str) -> None:
        """Unknown / typoed product types must fall back to the
        default threshold, not to zero (which would silently accept
        any datasheet) or to infinity (which would silently block
        every new product type). Pin the ``dict.get(..., default)``
        contract explicitly."""
        v_at_default = check_spec_density_calibrated(
            _scan(product_type=product_type, spec_density=_DEFAULT_DENSITY_THRESHOLD)
        )
        v_below_default = check_spec_density_calibrated(
            _scan(
                product_type=product_type,
                spec_density=_DEFAULT_DENSITY_THRESHOLD - 0.01,
            )
        )
        assert v_at_default.passed is True
        assert v_below_default.passed is False


# ---------------------------------------------------------------------------
# run_guards orchestrator
# ---------------------------------------------------------------------------


class TestRunGuardsContract:
    """The orchestrator must run every guard regardless of individual
    verdicts and return a fixed-shape result. Anything else means a
    failing guard silently short-circuits later guards' signal."""

    @given(
        mfr=_MANUFACTURER_INPUTS,
        product_name=st.one_of(st.none(), st.text(min_size=0, max_size=30)),
        spec_pages=_SPEC_PAGES,
        density=_DENSITIES,
        product_type=_PRODUCT_TYPES,
        is_multi=st.booleans(),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_returns_four_verdicts_with_unique_names(
        self,
        mfr: Any,
        product_name: Any,
        spec_pages: Any,
        density: Any,
        product_type: Any,
        is_multi: bool,
    ) -> None:
        scan = _scan(
            manufacturer=mfr,
            product_name=product_name,
            spec_pages=spec_pages,
            spec_density=density,
            product_type=product_type,
            is_multi_category=is_multi,
        )
        verdicts = run_guards(scan)
        assert isinstance(verdicts, list)
        assert len(verdicts) == 4
        assert all(isinstance(v, GuardVerdict) for v in verdicts)
        names = [v.guard_name for v in verdicts]
        assert len(set(names)) == 4, (
            f"run_guards returned duplicate guard_names: {names!r}"
        )
        assert set(names) == {
            "manufacturer_identity",
            "document_scope",
            "extraction_feasibility",
            "spec_density_calibrated",
        }


# ---------------------------------------------------------------------------
# any_blocking
# ---------------------------------------------------------------------------


_SEVERITIES = st.sampled_from(["block", "warn"])


@st.composite
def _verdicts(draw: st.DrawFn) -> list[GuardVerdict]:
    n = draw(st.integers(min_value=0, max_value=8))
    verdicts: list[GuardVerdict] = []
    for i in range(n):
        passed = draw(st.booleans())
        severity = draw(_SEVERITIES)
        verdicts.append(
            GuardVerdict(
                passed=passed,
                guard_name=f"guard_{i}",
                reason=None if passed else draw(st.text(max_size=20)),
                severity=severity,
            )
        )
    return verdicts


class TestAnyBlockingContract:
    """``any_blocking`` is the promotion gate. If it returns the wrong
    verdict (or none), a bad datasheet either gets promoted or a good
    one gets held. The property pins the exact selection semantics."""

    @given(verdicts=_verdicts())
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_returns_first_blocker_or_none(self, verdicts: list[GuardVerdict]) -> None:
        try:
            result = any_blocking(verdicts)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(f"any_blocking raised {type(exc).__name__}: {exc!r}")

        expected = next(
            (v for v in verdicts if not v.passed and v.severity == "block"),
            None,
        )
        if expected is None:
            assert result is None
        else:
            assert result is expected

    @given(verdicts=_verdicts())
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_warn_severity_is_never_returned(
        self, verdicts: list[GuardVerdict]
    ) -> None:
        """Warn-severity verdicts inform the operator but do not block
        promotion. Pin that ``any_blocking`` filters them out even
        when they fail, so a warn-severity guard can't accidentally
        turn into a blocking one via a severity typo."""
        result = any_blocking(verdicts)
        if result is not None:
            assert result.severity == "block"
            assert result.passed is False
