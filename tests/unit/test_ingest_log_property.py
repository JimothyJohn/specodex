"""Property tests for ``specodex.ingest_log``.

The example-based companion (``test_ingest_log.py``) pins specific
key formats and the four status-branching cases of ``should_skip``.
This file generates *adversarial* inputs to the same surface — unicode
URLs, near-threshold quality ratios, last-attempt dicts with garbage
values for the numeric fields, ``fields_missing`` lists with duplicates
and arbitrary ordering — and asserts the documented contracts hold for
every input the strategies can produce.

``ingest_log`` sits between scraper outcomes (which can be arbitrarily
shaped after Gemini parsing fails) and DynamoDB writes (where a torn
record short-circuits future re-runs via ``should_skip``). Both
boundaries deserve adversarial coverage:

- ``url_hash`` / ``pk_for_url`` produce the DynamoDB primary key. A
  collision on different URLs or a non-deterministic hash would corrupt
  the per-URL latest-attempt query.
- ``sk_now`` produces the sort key. Two successive calls must remain
  lexicographically ordered (``ScanIndexForward=False, Limit=1`` reads
  the *newest* attempt — chronological order must match lexicographic).
- ``build_record`` raises ``ValueError`` on an unknown status string
  and only on that. Any other exception escaping leaks an exception
  type the caller can't anticipate.
- ``should_skip`` returns a bool from the union of statuses + arbitrary
  garbage in the numeric fields a malformed prior record could carry.

**Contracts under test:**

1. **``url_hash``** — total function from any ``str`` to a 16-character
   lowercase hex string, deterministic on the input.
2. **``pk_for_url``** — total function from any ``str`` to
   ``"INGEST#" + url_hash(url)``; length always 23.
3. **``sk_now``** — never raises; output starts with ``"INGEST#"``;
   monotonically non-decreasing across successive calls within a single
   process (``time.time`` is monotonic in practice for our second-
   resolution timestamps).
4. **``build_record``** — raises ``ValueError`` iff status is not in
   ``VALID_STATUSES``; otherwise returns a dict with every required
   key, numeric coercions applied, ``fields_missing`` deduped and
   sorted, optional hint/token fields present iff provided.
5. **``should_skip``** — total function from
   ``Optional[dict[str, Any]]`` to ``bool``. Never raises, even when
   the prior record's numeric fields are non-numeric garbage that the
   ``int()`` / ``float()`` coercions could choke on. Branching matches
   the documented status table.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.ingest_log import (
    MIN_RETRY_THRESHOLD,
    SCHEMA_VERSION,
    STATUS_EXTRACT_FAIL,
    STATUS_QUALITY_FAIL,
    STATUS_SKIPPED_DUP,
    STATUS_SUCCESS,
    VALID_STATUSES,
    build_record,
    pk_for_url,
    should_skip,
    sk_now,
    url_hash,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


# Plausible URLs — http/https with arbitrary path/query bytes. The hash
# is over the full string so we don't need to constrain to RFC 3986;
# any valid Python string is a legal input. ``st.text`` already excludes
# lone surrogates by default, which keeps the input space to what could
# survive a real UTF-8 round-trip through DynamoDB.
_URL_TEXT = st.text(min_size=0, max_size=200)

# Unicode-laced URLs — full BMP + supplementary planes, but excluding
# the surrogate band (U+D800..U+DFFF) which isn't UTF-8-encodable. The
# function reads URL strings out of DynamoDB items, which can only
# contain UTF-8-roundtrippable scalars in the first place.
_UNICODE_URL = st.text(
    alphabet=st.characters(
        min_codepoint=0,
        max_codepoint=0x10FFFF,
        blacklist_categories=("Cs",),  # exclude surrogate codepoints
    ),
    min_size=0,
    max_size=100,
)

# Realistic vendor URLs — the dominant happy path.
_REALISTIC_URL = st.from_regex(
    r"https?://[a-z0-9.\-]{3,40}\.(?:com|net|io|de|jp)/"
    r"[a-zA-Z0-9_\-/]{0,60}\.pdf",
    fullmatch=True,
)

_ANY_URL = st.one_of(_URL_TEXT, _UNICODE_URL, _REALISTIC_URL)


_STATUSES = st.sampled_from(sorted(VALID_STATUSES))


# Free-text strings — manufacturer, product_type, hints. The function
# doesn't enforce any structure on these; just round-trips them.
_FREE_TEXT = st.text(min_size=0, max_size=40)


# Numbers that the build_record kwargs accept. Mix bools, ints, and
# floats so the int()/float() coercions get exercised.
_NUMERIC_INT_INPUT = st.one_of(
    st.integers(min_value=-1000, max_value=10000),
    st.booleans(),
)
_NUMERIC_FLOAT_INPUT = st.one_of(
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-1000, max_value=10000),
    st.booleans(),
)


# Arbitrary tokens for the iterable kwargs — duplicates intentional so
# the dedupe+sort property gets pressure.
_FIELD_TOKEN = st.text(min_size=0, max_size=15)
_FIELD_TOKEN_LIST = st.lists(_FIELD_TOKEN, min_size=0, max_size=12)


# ---------------------------------------------------------------------------
# url_hash + pk_for_url
# ---------------------------------------------------------------------------


_HEX_CHARS = set("0123456789abcdef")


class TestUrlHashContract:
    """Key generation must be total and deterministic — DynamoDB writes
    can't recover from a hash that changes between two reads of the
    same URL."""

    @given(url=_ANY_URL)
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises_and_returns_16_hex_chars(self, url: str) -> None:
        try:
            result = url_hash(url)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(f"url_hash raised {type(exc).__name__}: {exc!r}\nurl={url!r}")
        assert isinstance(result, str)
        assert len(result) == 16
        # SHA-256 hex digest is lowercase 0-9a-f only.
        assert set(result) <= _HEX_CHARS, (
            f"non-hex chars in result: {set(result) - _HEX_CHARS}"
        )

    @given(url=_ANY_URL)
    @settings(max_examples=200, deadline=None)
    def test_deterministic(self, url: str) -> None:
        """Same input → same output. A non-deterministic hash would
        corrupt the per-URL latest-attempt query."""
        assert url_hash(url) == url_hash(url)

    @given(a=_ANY_URL, b=_ANY_URL)
    @settings(max_examples=200, deadline=None)
    def test_equal_inputs_iff_equal_outputs_within_strategy(
        self, a: str, b: str
    ) -> None:
        """If two inputs are equal, hashes match. (The converse —
        no collisions — isn't testable with finite examples, but
        SHA-256 makes it astronomically improbable; ``url_hash``'s
        contract relies on the cryptographic primitive's promise.)"""
        if a == b:
            assert url_hash(a) == url_hash(b)


class TestPkForUrlContract:
    @given(url=_ANY_URL)
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_pk_format_and_length(self, url: str) -> None:
        pk = pk_for_url(url)
        assert isinstance(pk, str)
        assert pk.startswith("INGEST#")
        # "INGEST#" (7 chars) + 16-char hash = 23.
        assert len(pk) == 23
        # The hash suffix matches url_hash(url) exactly.
        assert pk[len("INGEST#") :] == url_hash(url)

    @given(url=_ANY_URL)
    @settings(max_examples=100, deadline=None)
    def test_deterministic(self, url: str) -> None:
        assert pk_for_url(url) == pk_for_url(url)


# ---------------------------------------------------------------------------
# sk_now
# ---------------------------------------------------------------------------


class TestSkNowContract:
    def test_never_raises_and_format(self) -> None:
        sk = sk_now()
        assert isinstance(sk, str)
        assert sk.startswith("INGEST#")
        # Body is an ISO-8601 second-precision timestamp ending in Z.
        body = sk[len("INGEST#") :]
        assert len(body) == 20
        assert body[-1] == "Z"
        # YYYY-MM-DDTHH:MM:SSZ — punctuation positions fixed.
        assert body[4] == "-" and body[7] == "-" and body[10] == "T"
        assert body[13] == ":" and body[16] == ":"

    def test_monotonic_across_successive_calls(self) -> None:
        """ScanIndexForward=False, Limit=1 reads the newest attempt.
        Lexicographic order must therefore match chronological order
        across successive calls in the same process."""
        # 5 back-to-back samples — at second resolution they're likely
        # the same string, but the comparison must hold either way.
        samples = [sk_now() for _ in range(5)]
        for prev, nxt in zip(samples, samples[1:]):
            assert prev <= nxt, (
                f"sk_now produced a non-monotonic sequence: {prev!r} > {nxt!r}"
            )


# ---------------------------------------------------------------------------
# build_record
# ---------------------------------------------------------------------------


class TestBuildRecordContract:
    """``build_record`` accepts the union of every prior-attempt outcome
    the scraper produces. The Pydantic-style guard is intentionally
    narrow — only the status string is enforced — so the property tests
    pin the numeric coercion + optional-field rules that the rest of
    the body promises in passing.
    """

    @given(
        url=_ANY_URL,
        manufacturer=_FREE_TEXT,
        product_type=_FREE_TEXT,
        status=_STATUSES,
        products_extracted=_NUMERIC_INT_INPUT,
        products_written=_NUMERIC_INT_INPUT,
        fields_total=_NUMERIC_INT_INPUT,
        fields_filled_avg=_NUMERIC_FLOAT_INPUT,
        fields_missing=_FIELD_TOKEN_LIST,
        pages_detected=_NUMERIC_INT_INPUT,
        pages_used=st.lists(
            st.integers(min_value=0, max_value=1000), min_size=0, max_size=8
        ),
        extracted_part_numbers=_FIELD_TOKEN_LIST,
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_valid_status_returns_well_formed_record(
        self,
        url: str,
        manufacturer: str,
        product_type: str,
        status: str,
        products_extracted: Any,
        products_written: Any,
        fields_total: Any,
        fields_filled_avg: Any,
        fields_missing: list,
        pages_detected: Any,
        pages_used: list,
        extracted_part_numbers: list,
    ) -> None:
        record = build_record(
            url=url,
            manufacturer=manufacturer,
            product_type=product_type,
            status=status,
            products_extracted=products_extracted,
            products_written=products_written,
            fields_total=fields_total,
            fields_filled_avg=fields_filled_avg,
            fields_missing=fields_missing,
            pages_detected=pages_detected,
            pages_used=pages_used,
            extracted_part_numbers=extracted_part_numbers,
        )

        # Required keys always present.
        for key in (
            "PK",
            "SK",
            "url",
            "manufacturer",
            "product_type",
            "status",
            "products_extracted",
            "products_written",
            "fields_total",
            "fields_filled_avg",
            "fields_missing",
            "pages_detected",
            "pages_used",
            "extracted_part_numbers",
            "schema_version",
        ):
            assert key in record, f"missing required key {key!r} on valid status"

        # Identity-preserving fields.
        assert record["PK"] == pk_for_url(url)
        assert record["url"] == url
        assert record["manufacturer"] == manufacturer
        assert record["product_type"] == product_type
        assert record["status"] == status
        assert record["schema_version"] == SCHEMA_VERSION
        assert record["SK"].startswith("INGEST#")

        # Numeric coercions hold (bools count as ints — Python's
        # int(True) == 1, that's the documented behaviour).
        assert isinstance(record["products_extracted"], int)
        assert isinstance(record["products_written"], int)
        assert isinstance(record["fields_total"], int)
        assert isinstance(record["fields_filled_avg"], float)
        assert isinstance(record["pages_detected"], int)
        assert record["products_extracted"] == int(products_extracted)
        assert record["products_written"] == int(products_written)
        assert record["fields_total"] == int(fields_total)
        assert record["fields_filled_avg"] == float(fields_filled_avg)
        assert record["pages_detected"] == int(pages_detected)

        # fields_missing: deduped, sorted, every input element present.
        out_missing = record["fields_missing"]
        assert isinstance(out_missing, list)
        assert out_missing == sorted(set(fields_missing))

        # pages_used / extracted_part_numbers pass through as lists
        # (no dedupe — they intentionally preserve order/multiplicity).
        assert record["pages_used"] == list(pages_used)
        assert record["extracted_part_numbers"] == list(extracted_part_numbers)

        # Optional fields NOT included when caller omits them.
        for optional in (
            "product_name_hint",
            "product_family_hint",
            "page_finder_method",
            "gemini_input_tokens",
            "gemini_output_tokens",
            "error_message",
        ):
            assert optional not in record, (
                f"optional key {optional!r} appeared when not provided"
            )

    @given(
        url=_ANY_URL,
        manufacturer=_FREE_TEXT,
        product_type=_FREE_TEXT,
        status=_STATUSES,
        product_name_hint=_FREE_TEXT,
        product_family_hint=_FREE_TEXT,
        page_finder_method=_FREE_TEXT,
        gemini_input_tokens=_NUMERIC_INT_INPUT,
        gemini_output_tokens=_NUMERIC_INT_INPUT,
        error_message=_FREE_TEXT,
    )
    @settings(max_examples=100, deadline=None)
    def test_optional_fields_round_trip(
        self,
        url: str,
        manufacturer: str,
        product_type: str,
        status: str,
        product_name_hint: str,
        product_family_hint: str,
        page_finder_method: str,
        gemini_input_tokens: Any,
        gemini_output_tokens: Any,
        error_message: str,
    ) -> None:
        record = build_record(
            url=url,
            manufacturer=manufacturer,
            product_type=product_type,
            status=status,
            product_name_hint=product_name_hint,
            product_family_hint=product_family_hint,
            page_finder_method=page_finder_method,
            gemini_input_tokens=gemini_input_tokens,
            gemini_output_tokens=gemini_output_tokens,
            error_message=error_message,
        )
        assert record["product_name_hint"] == product_name_hint
        assert record["product_family_hint"] == product_family_hint
        assert record["page_finder_method"] == page_finder_method
        assert isinstance(record["gemini_input_tokens"], int)
        assert isinstance(record["gemini_output_tokens"], int)
        assert record["gemini_input_tokens"] == int(gemini_input_tokens)
        assert record["gemini_output_tokens"] == int(gemini_output_tokens)
        assert record["error_message"] == error_message

    @given(
        url=_ANY_URL,
        manufacturer=_FREE_TEXT,
        product_type=_FREE_TEXT,
        bad_status=st.text(min_size=0, max_size=20).filter(
            lambda s: s not in VALID_STATUSES
        ),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    )
    def test_invalid_status_raises_value_error(
        self,
        url: str,
        manufacturer: str,
        product_type: str,
        bad_status: str,
    ) -> None:
        with pytest.raises(ValueError):
            build_record(
                url=url,
                manufacturer=manufacturer,
                product_type=product_type,
                status=bad_status,
            )

    @given(
        url=_ANY_URL,
        manufacturer=_FREE_TEXT,
        product_type=_FREE_TEXT,
        status=_STATUSES,
        explicit_sk=st.text(min_size=1, max_size=40),
    )
    @settings(max_examples=100, deadline=None)
    def test_explicit_sk_is_honored(
        self,
        url: str,
        manufacturer: str,
        product_type: str,
        status: str,
        explicit_sk: str,
    ) -> None:
        """When the caller supplies ``sk=``, the record uses it
        verbatim (e.g. replaying a known-time record from a fixture).
        Without ``sk=``, ``sk_now()`` produces the timestamp.
        """
        record = build_record(
            url=url,
            manufacturer=manufacturer,
            product_type=product_type,
            status=status,
            sk=explicit_sk,
        )
        assert record["SK"] == explicit_sk


# ---------------------------------------------------------------------------
# should_skip — branching contract + total/never-raises behaviour
# ---------------------------------------------------------------------------


# Adversarial values for the numeric fields a prior record might carry.
# Real DynamoDB rows shouldn't carry these, but a malformed write or a
# replayed test fixture could — and ``should_skip`` runs on the result
# of ``read_ingest`` before anything validates the shape.
_GARBAGE_NUMERIC: Any = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(min_size=0, max_size=8),
)


@st.composite
def _arbitrary_last_record(draw: st.DrawFn) -> dict[str, Any]:
    """A prior-attempt dict that ``should_skip`` might receive. Status
    may be a known status, an unknown status string, or omitted.
    Numeric fields are deliberately garbage."""
    keys_present = draw(
        st.sampled_from(["full", "no_status", "no_total", "no_filled", "empty"])
    )
    record: dict[str, Any] = {}
    if keys_present in ("full", "no_total", "no_filled"):
        record["status"] = draw(
            st.one_of(
                _STATUSES,
                st.text(min_size=0, max_size=10),  # unknown status string
            )
        )
    if keys_present in ("full", "no_filled"):
        record["fields_total"] = draw(_GARBAGE_NUMERIC)
    if keys_present in ("full", "no_total"):
        record["fields_filled_avg"] = draw(_GARBAGE_NUMERIC)
    return record


class TestShouldSkipContract:
    @given(record=_arbitrary_last_record())
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises_returns_bool(self, record: dict[str, Any]) -> None:
        """Garbage numeric fields can land in DynamoDB; ``should_skip``
        is the first read of that row and is called without a
        surrounding try/except — leaks would crash the scraper before
        it could overwrite the bad row."""
        try:
            result = should_skip(record)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"should_skip raised {type(exc).__name__}: {exc!r}\nrecord={record!r}"
            )
        assert isinstance(result, bool)

    @given(empty=st.one_of(st.none(), st.just({})))
    @settings(max_examples=20, deadline=None)
    def test_none_or_empty_means_run(self, empty: Optional[dict[str, Any]]) -> None:
        """``None`` (no prior record) and ``{}`` (truthy guard fails)
        both fall through the ``if not last`` early-return as "run"."""
        assert should_skip(empty) is False

    @given(extra=st.dictionaries(_FREE_TEXT, _GARBAGE_NUMERIC, max_size=4))
    @settings(max_examples=100, deadline=None)
    def test_success_always_skips(self, extra: dict[str, Any]) -> None:
        record = {"status": STATUS_SUCCESS, **extra}
        # Force the canonical status back in case `extra` overwrote it.
        record["status"] = STATUS_SUCCESS
        assert should_skip(record) is True

    @given(status=st.sampled_from([STATUS_EXTRACT_FAIL, STATUS_SKIPPED_DUP]))
    @settings(max_examples=20, deadline=None)
    def test_extract_fail_and_skipped_dup_always_retry(self, status: str) -> None:
        """extract_fail and skipped_dup are documented as "worth a
        re-attempt" — they fall through the status branches to the
        default ``return False``."""
        assert should_skip({"status": status}) is False

    @given(
        unknown=st.text(min_size=0, max_size=15).filter(
            lambda s: s not in VALID_STATUSES
        )
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    )
    def test_unknown_status_retries(self, unknown: str) -> None:
        """Any unknown status string falls through the explicit
        branches; the safe default is "retry"."""
        assert should_skip({"status": unknown}) is False

    @given(
        filled=st.floats(
            min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False
        ),
        total=st.integers(min_value=1, max_value=1000),
    )
    @settings(max_examples=200, deadline=None)
    def test_quality_fail_threshold_matches_arithmetic(
        self, filled: float, total: int
    ) -> None:
        """The skip decision on a quality_fail must match
        ``(filled / total) >= MIN_RETRY_THRESHOLD``. A sign flip or
        ``>`` instead of ``>=`` would either pin every retry off
        (wasted budget) or every retry on (silent under-coverage)."""
        record = {
            "status": STATUS_QUALITY_FAIL,
            "fields_filled_avg": filled,
            "fields_total": total,
        }
        expected = (filled / total) >= MIN_RETRY_THRESHOLD
        assert should_skip(record) is expected

    @given(
        total=st.integers(min_value=-1000, max_value=0),
        filled=st.floats(
            min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100, deadline=None)
    def test_quality_fail_with_nonpositive_total_retries(
        self, total: int, filled: float
    ) -> None:
        """``fields_total <= 0`` short-circuits the division and
        returns False (retry). A division-by-zero raise here would
        crash the scraper on every malformed row."""
        record = {
            "status": STATUS_QUALITY_FAIL,
            "fields_filled_avg": filled,
            "fields_total": total,
        }
        assert should_skip(record) is False
