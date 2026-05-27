"""Property tests for ``specodex.placeholders.is_placeholder``.

The placeholder check sits on every path that asks "is this field
meaningfully populated?" — quality scoring, frontend rendering, and
the Pydantic BeforeValidators that coerce LLM-emitted strings. A
regression here either inflates quality scores (treating "N/A" as
real data) or rejects valid product fields (treating real strings
as placeholders).

The example-based companion (``test_placeholders.py``) pins the
canonical token set and the obvious shapes. This file generates
*adversarial* inputs — arbitrary unicode, case-drift, surrounding
whitespace, and every non-string Python type the dispatch path
could plausibly hand us — and asserts the documented contract
holds for every input the strategy can produce.

**Contract under test** (per the docstring on ``is_placeholder``):

1. **Total.** The function never raises on any Python value. The
   call sites have no ``try/except`` and any leak would crash the
   scorer or the validator.
2. **Return shape.** The result is always a Python ``bool`` — not
   a falsy/truthy proxy.
3. **``None`` is a placeholder.** Always returns True.
4. **Non-None non-string values are NOT placeholders.** Numbers,
   booleans, lists, dicts, tuples, bytes, sets all return False.
   Empty containers (``[]``, ``{}``) are explicitly *not* placeholders
   per the docstring.
5. **String placement is case-insensitive.** ``is_placeholder(s)``
   equals ``is_placeholder(s.upper())`` and ``is_placeholder(s.lower())``
   for any string.
6. **Surrounding whitespace is stripped.** ``is_placeholder(s)``
   equals ``is_placeholder("  " + s + "  ")`` for any string.
7. **Determinism.** The function is pure — repeated calls on the
   same input always return the same result.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.placeholders import PLACEHOLDER_STRINGS, is_placeholder


# ---------------------------------------------------------------------------
# Input strategies
# ---------------------------------------------------------------------------


# A sampled placeholder token, optionally re-cased and wrapped in
# surrounding whitespace. Exercises rules 5 + 6 + the canonical set.
_PLACEHOLDER_STRINGS = st.sampled_from(sorted(PLACEHOLDER_STRINGS))


_WHITESPACE_RUNS = st.text(alphabet=" \t\n\r", min_size=0, max_size=4)


@st.composite
def _decorated_placeholder(draw: st.DrawFn) -> str:
    """A canonical placeholder with random casing + surrounding whitespace."""
    base = draw(_PLACEHOLDER_STRINGS)
    # Re-case randomly: lower / upper / title / mixed.
    style = draw(st.sampled_from(["lower", "upper", "title", "mixed"]))
    if style == "lower":
        cased = base.lower()
    elif style == "upper":
        cased = base.upper()
    elif style == "title":
        cased = base.title()
    else:
        # Mixed: flip each alpha char with 50% probability.
        flips = draw(st.lists(st.booleans(), min_size=len(base), max_size=len(base)))
        cased = "".join(c.upper() if f and c.isalpha() else c for c, f in zip(base, flips))
    left = draw(_WHITESPACE_RUNS)
    right = draw(_WHITESPACE_RUNS)
    return left + cased + right


# Arbitrary text (mostly NOT a placeholder — gives Hypothesis a shot
# at finding a string that's "almost" a placeholder).
_ARBITRARY_TEXT = st.text(min_size=0, max_size=60)


# Unicode-laced text — covers C0/C1 control bands, supplementary planes,
# U+2028/U+2029 separators, Turkish I (a classic case-folding trap).
_UNICODE_TEXT = st.text(
    alphabet=st.characters(min_codepoint=0, max_codepoint=0x10FFFF),
    min_size=0,
    max_size=30,
)


_STRINGS = st.one_of(
    _decorated_placeholder(),
    _ARBITRARY_TEXT,
    _UNICODE_TEXT,
)


# Every non-string, non-None Python value that the call sites might
# plausibly hand us — the docstring promises all of these return False.
_NON_STRING_NON_NONE = st.one_of(
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.binary(max_size=30),
    st.lists(st.integers(), max_size=4),
    st.dictionaries(st.text(max_size=5), st.integers(), max_size=4),
    st.tuples(st.integers(), st.integers()),
    st.sets(st.integers(), max_size=4),
    st.frozensets(st.integers(), max_size=4),
)


# Everything together, including None.
_ANY_INPUT = st.one_of(
    st.none(),
    _STRINGS,
    _NON_STRING_NON_NONE,
)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestIsPlaceholderContract:
    """The documented contract on ``is_placeholder`` must hold for
    every input the strategy can produce.
    """

    @given(value=_ANY_INPUT)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_returns_bool_and_never_raises(self, value: Any) -> None:
        """Rule 1 + Rule 2: the function never raises and always
        returns a true Python ``bool``. The call sites depend on
        ``result is True`` / ``result is False`` semantics in places
        (scorer's tight loops), so a falsy proxy is a contract
        violation."""
        try:
            result = is_placeholder(value)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"is_placeholder raised {type(exc).__name__}: {exc!r}\n"
                f"input: {value!r}"
            )
        assert isinstance(result, bool), (
            f"is_placeholder returned {type(result).__name__}, expected bool\n"
            f"input: {value!r}"
        )

    @given(value=_NON_STRING_NON_NONE)
    @settings(max_examples=300, deadline=None)
    def test_non_string_non_none_is_never_placeholder(self, value: Any) -> None:
        """Rule 4: numbers, bools, containers, bytes are never
        placeholders, even when they look "empty". The docstring is
        explicit that ``[]`` / ``{}`` are NOT placeholders — callers
        that want empty-container-as-missing must handle that
        themselves."""
        assert is_placeholder(value) is False, (
            f"is_placeholder treated non-string non-None value as placeholder: "
            f"{value!r} ({type(value).__name__})"
        )

    @given(text=_STRINGS)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_case_insensitive(self, text: str) -> None:
        """Rule 5: any string and its upper/lower variants share the
        same placeholder classification. The canonical set is stored
        lowercased; this property pins that the comparison stays
        case-folded regardless of input casing.
        """
        baseline = is_placeholder(text)
        assert is_placeholder(text.lower()) == baseline, (
            f"is_placeholder({text!r}) != is_placeholder({text.lower()!r})"
        )
        assert is_placeholder(text.upper()) == baseline, (
            f"is_placeholder({text!r}) != is_placeholder({text.upper()!r})"
        )

    @given(text=_STRINGS, left=_WHITESPACE_RUNS, right=_WHITESPACE_RUNS)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_surrounding_whitespace_is_stripped(
        self, text: str, left: str, right: str
    ) -> None:
        """Rule 6: wrapping a string in surrounding whitespace must
        not change its placeholder classification. Pins that
        ``.strip()`` is applied before the membership test, and that
        the membership test isn't accidentally sensitive to leading
        / trailing space in the *canonical* set (it isn't — every
        token in PLACEHOLDER_STRINGS is already trimmed).
        """
        baseline = is_placeholder(text)
        wrapped = left + text + right
        # If the inner text has internal whitespace that the strip
        # would expose to the membership test, the property still
        # holds: ``"  N/A  ".strip() == "N/A"`` == ``"N/A".strip()``.
        assert is_placeholder(wrapped) == baseline, (
            f"is_placeholder({text!r}) != is_placeholder({wrapped!r}) — "
            "surrounding whitespace altered the classification"
        )

    @given(value=_ANY_INPUT)
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_deterministic(self, value: Any) -> None:
        """Pure function — repeated calls with the same input must
        return the same result. Pins that no global state (locale,
        caches, time) leaks into the comparison.
        """
        first = is_placeholder(value)
        second = is_placeholder(value)
        assert first == second, (
            f"is_placeholder({value!r}) non-deterministic: {first!r} vs {second!r}"
        )


class TestCanonicalSetExhaustive:
    """Every token in the canonical set, plus every variant the
    contract promises to handle, must classify as a placeholder.
    Anchors the property tests in concrete shapes."""

    def test_every_canonical_token_is_placeholder(self) -> None:
        for tok in PLACEHOLDER_STRINGS:
            assert is_placeholder(tok) is True, (
                f"canonical token {tok!r} did not classify as a placeholder"
            )

    def test_every_canonical_token_upper_is_placeholder(self) -> None:
        for tok in PLACEHOLDER_STRINGS:
            upper = tok.upper()
            assert is_placeholder(upper) is True, (
                f"upper-cased canonical token {upper!r} did not classify"
            )

    def test_every_canonical_token_wrapped_is_placeholder(self) -> None:
        for tok in PLACEHOLDER_STRINGS:
            wrapped = f"  {tok}  "
            assert is_placeholder(wrapped) is True, (
                f"whitespace-wrapped canonical token {wrapped!r} did not classify"
            )


class TestExplicitRegressionsFromAdversarialShapes:
    """Pin specific shapes that exercise edge cases in the documented
    contract. The property tests above generate adversarial inputs;
    these examples lock the exact shapes so they can't regress even
    if a Hypothesis strategy drifts.
    """

    def test_whitespace_only_string_is_placeholder(self) -> None:
        """``""`` is in the canonical set, and ``"   ".strip() == ""``,
        so any whitespace-only string classifies as a placeholder.
        Pins the "empty after strip" path explicitly."""
        assert is_placeholder("   ") is True
        assert is_placeholder("\t\n") is True
        assert is_placeholder("") is True

    def test_bytes_is_not_placeholder(self) -> None:
        """Bytes look stringy but the docstring is explicit:
        ``isinstance(value, str)`` is the gate, and bytes objects
        fail it. Pin so a future ``isinstance(value, (str, bytes))``
        "helpful" widening is caught."""
        assert is_placeholder(b"") is False
        assert is_placeholder(b"n/a") is False
        assert is_placeholder(b"N/A") is False

    def test_bool_is_not_placeholder(self) -> None:
        """``False`` is falsy and ``bool`` is a subclass of ``int``,
        but the docstring is explicit that non-string non-None
        values always return False. ``is_placeholder(False)`` must
        not somehow become True via the falsiness route."""
        assert is_placeholder(False) is False
        assert is_placeholder(True) is False

    def test_empty_containers_not_placeholder(self) -> None:
        """Docstring is explicit: empty list / dict are NOT
        placeholders. Callers that want that behaviour must opt in
        themselves. Pin so a future "be helpful" change doesn't
        silently widen the placeholder definition."""
        assert is_placeholder([]) is False
        assert is_placeholder({}) is False
        assert is_placeholder(()) is False
        assert is_placeholder(set()) is False

    def test_substring_not_placeholder(self) -> None:
        """``"na-12345"`` contains ``"na"`` as a substring but is not
        equal to it after strip+lower. The membership test must be
        exact-match, not substring. Pinning the part-number-as-
        placeholder regression the example tests already catch."""
        assert is_placeholder("na-12345") is False
        assert is_placeholder("none of the above") is False
        assert is_placeholder("tba-01") is False
        assert is_placeholder("-EC45") is False
