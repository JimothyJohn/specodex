"""Property tests for ``cli.processor.parse_datasheet_id_from_key``.

The S3 upload-queue dispatch in ``cli.processor.run`` consumes S3
keys produced by ``list_objects_v2`` and pulls the datasheet_id
out of them before doing anything else (DynamoDB lookup, PDF
download, LLM extraction). A broken parse silently drops pending
uploads or — worse — passes garbage downstream as a primary key.

The example-based companion (``test_processor.py``) pins specific
malformed shapes. This file generates *adversarial* keys — unicode
mixed with separators, empty/whitespace ids, leading/trailing
slashes, non-string inputs — and asserts the documented contract
holds for every input the strategy can produce.

**Contract under test** (per the docstring):

1. The function returns either a non-empty ``str`` (the
   datasheet_id) or ``None`` — never any other type.
2. **It never raises.** Any exception escaping is a regression in
   the dispatch loop's input boundary; the caller's ``except`` (it
   has none) would otherwise let it crash the whole queue worker.
3. When the result is a string, it equals ``key.split("/")[1]``
   verbatim — no normalisation, no canonicalisation. (Whitespace
   id segments like ``" "`` round-trip; only the *empty* slot
   produces ``None``.)
4. Non-string inputs always produce ``None``.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from cli.processor import parse_datasheet_id_from_key


# ---------------------------------------------------------------------------
# Adversarial S3-key strategies
# ---------------------------------------------------------------------------


# Realistic-looking upload-queue keys — well-formed ``<prefix>/<id>/<file>``.
_WELL_FORMED_KEYS = st.from_regex(
    r"(queue|good_examples)/[A-Za-z0-9_\-]{1,30}/[A-Za-z0-9._\-]{1,30}\.pdf",
    fullmatch=True,
)


# Arbitrary text — most won't even contain a ``/`` and exercise the
# "fewer than 3 segments" path.
_ARBITRARY_TEXT = st.text(min_size=0, max_size=80)


# Unicode-laced strings — BMP + supplementary planes, including
# the C0/C1 control bands and U+2028/U+2029 line separators that
# trip up naive splitters.
_UNICODE_TEXT = st.text(
    alphabet=st.characters(min_codepoint=0, max_codepoint=0x10FFFF),
    min_size=0,
    max_size=40,
)


# Keys with explicit ``/`` separators — gives Hypothesis a shot at
# producing the awkward shapes (empty id slot, trailing slash,
# many slashes in a row) that the inline parser used to mis-handle.
_SLASH_HEAVY_KEYS = st.lists(
    st.text(
        alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E), max_size=15
    ),
    min_size=0,
    max_size=6,
).map(lambda parts: "/".join(parts))


# Specific attack-vector samples — what a malicious or malformed
# upload would produce.
_ATTACK_KEYS = st.sampled_from(
    [
        "",
        "/",
        "//",
        "///",
        "queue/",
        "queue//",
        "queue//file.pdf",  # empty id slot
        "queue/ /file.pdf",  # whitespace id — allowed, round-trips
        "queue/abc",  # only two segments
        "queue/abc/",
        "queue/abc/file.pdf",
        "good_examples/abc/file.pdf",
        "/queue/abc/file.pdf",  # leading slash → parts[0] is ""
        "queue/abc/sub/dir/file.pdf",  # nested → parts[1] is "abc"
        "QUEUE/abc/file.pdf",  # case-sensitive (prefix not used by parser)
        "other-prefix/abc/file.pdf",  # parser does not gate on prefix
        "queue/\x00null/file.pdf",  # NUL byte in id slot
        "queue/тест/file.pdf",  # Cyrillic id
        "queue/" + "a" * 300 + "/file.pdf",  # long id
        "queue\n/abc/file.pdf",  # newline before separator
    ]
)


_KEY_STRATEGY = st.one_of(
    _WELL_FORMED_KEYS,
    _ARBITRARY_TEXT,
    _UNICODE_TEXT,
    _SLASH_HEAVY_KEYS,
    _ATTACK_KEYS,
)


_NON_STRING_INPUTS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.binary(max_size=30),
    st.lists(st.text(max_size=10), max_size=3),
    st.dictionaries(st.text(max_size=5), st.text(max_size=10), max_size=3),
    st.tuples(st.text(max_size=5), st.text(max_size=5)),
)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestParseDatasheetIdContract:
    """The S3 key parser must obey its documented contract on every
    input the dispatch loop could plausibly feed it."""

    @given(key=_KEY_STRATEGY)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_returns_str_or_none_and_never_raises(self, key: str) -> None:
        """The function must return ``str | None`` for every string
        input and must never propagate an exception. The dispatch
        loop has no try/except around the call — any leak crashes
        the worker.
        """
        try:
            result = parse_datasheet_id_from_key(key)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"parse_datasheet_id_from_key raised {type(exc).__name__}: "
                f"{exc!r}\ninput: {key!r}"
            )

        assert result is None or isinstance(result, str), (
            f"parse_datasheet_id_from_key returned {type(result).__name__}, "
            f"expected str | None\ninput: {key!r}"
        )

    @given(key=_KEY_STRATEGY)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_non_none_results_are_nonempty_and_traceable(self, key: str) -> None:
        """When the function returns a string, that string must:
        * be non-empty (caller would otherwise do a DynamoDB scan
          with an empty datasheet_id and silently skip);
        * equal ``key.split('/')[1]`` verbatim — no normalisation.
        """
        result = parse_datasheet_id_from_key(key)
        if result is None:
            return
        assert result != "", (
            f"parse_datasheet_id_from_key returned empty string for {key!r}; "
            "must return None on an empty id slot"
        )
        parts = key.split("/")
        assert len(parts) >= 3, (
            f"parse_datasheet_id_from_key returned {result!r} for {key!r}, "
            "but the key has fewer than 3 segments — id slot does not exist"
        )
        assert result == parts[1], (
            f"parse_datasheet_id_from_key normalised the id slot: "
            f"returned {result!r}, expected {parts[1]!r} from {key!r}"
        )

    @given(key=_KEY_STRATEGY)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_none_results_have_documented_reason(self, key: str) -> None:
        """When the function returns ``None``, one of the documented
        reasons must apply: too few segments, or empty id slot.
        Anything else returning ``None`` means the contract has a
        gap.
        """
        result = parse_datasheet_id_from_key(key)
        if result is not None:
            return
        parts = key.split("/")
        too_few_segments = len(parts) < 3
        empty_id_slot = len(parts) >= 3 and parts[1] == ""
        assert too_few_segments or empty_id_slot, (
            f"parse_datasheet_id_from_key returned None for {key!r} but the "
            f"key has {len(parts)} segments with id slot {parts[1]!r} — "
            "neither documented rejection reason applies"
        )

    @given(value=_NON_STRING_INPUTS)
    @settings(max_examples=200, deadline=None)
    def test_non_string_input_returns_none(self, value: Any) -> None:
        """Non-string input — None, bool, int, float, bytes, list,
        dict, tuple — must always return None. The dispatch loop's
        ``item["key"]`` is a string in production, but the parser
        is the boundary that protects the rest of the worker from a
        bad ``list_objects_v2`` shape (e.g. boto3 mock returning a
        non-string).
        """
        try:
            result = parse_datasheet_id_from_key(value)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"parse_datasheet_id_from_key raised {type(exc).__name__} "
                f"on non-string input: {exc!r}\ninput: {value!r}"
            )
        assert result is None, (
            f"parse_datasheet_id_from_key accepted non-string input "
            f"{value!r} and returned {result!r}"
        )


class TestExplicitRegressionsFromAdversarialShapes:
    """Pin specific shapes the property test surfaces so they can't
    regress even if the strategy drifts.
    """

    def test_empty_id_slot_rejected(self) -> None:
        """``queue//file.pdf`` reaches the inline parser with
        ``parts == ["queue", "", "file.pdf"]`` — three segments,
        but the id slot is empty. The old inline logic returned
        ``""`` which then went into a DynamoDB scan as the
        datasheet_id; the helper now returns None so the dispatch
        loop skips with a clear log.
        """
        assert parse_datasheet_id_from_key("queue//file.pdf") is None
        assert parse_datasheet_id_from_key("good_examples//file.pdf") is None

    def test_leading_slash_makes_parts_zero_the_empty_string(self) -> None:
        """``/queue/abc/file.pdf`` splits to
        ``["", "queue", "abc", "file.pdf"]`` — 4 segments, id slot
        is ``"queue"``. This is the "leading slash" shape and the
        parser returns ``"queue"`` (the second segment), not
        ``"abc"``. Pinning so a future "strip leading slashes"
        change is a conscious choice, not a silent regression.
        """
        assert parse_datasheet_id_from_key("/queue/abc/file.pdf") == "queue"

    def test_trailing_slash_with_id_returns_id(self) -> None:
        """``queue/abc/`` has 3 segments (``["queue", "abc", ""]``)
        and a non-empty id slot — the trailing empty filename does
        not invalidate the key for parsing purposes.
        """
        assert parse_datasheet_id_from_key("queue/abc/") == "abc"

    def test_nested_path_returns_first_id_segment(self) -> None:
        """The id slot is always ``parts[1]``, regardless of how
        deep the rest of the path goes.
        """
        assert parse_datasheet_id_from_key("queue/abc/sub/dir/file.pdf") == "abc"
