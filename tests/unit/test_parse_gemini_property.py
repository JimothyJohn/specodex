"""Property tests for ``specodex.utils.parse_gemini_response``.

The function eats Gemini-generated JSON, which is by definition
untrusted bytes. The existing example-based tests cover the happy
path and a handful of edge cases; this file generates malformed,
unicode-laced, and recursively-nested JSON to confirm the
documented contract holds for every input the strategy can think of.

**Contract under test** (from the docstring):

1. Returns a ``List[<schema_type instance>]`` when at least one row
   validates.
2. Raises ``ValueError`` when the response is unusable: missing
   ``.text``, empty after stripping JSON fences, not valid JSON,
   top-level neither object nor array, or zero rows validate.
3. **Never raises any other exception type.** A ``KeyError`` /
   ``TypeError`` / ``AttributeError`` escaping the function on a
   malformed input is the regression to catch — Gemini in
   production sometimes emits surprising shapes (truncated mid-
   string, fence-leaked, half-object) and the parser is the
   isolation barrier.
4. **Never returns half-validated objects.** Every item in the
   returned list is an instance of ``schema_type`` constructed by
   Pydantic — not a raw dict.

Per HARDENING.md Phase 3.1.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.models.motor import Motor
from specodex.utils import parse_gemini_response


# Target schema for the property tests. Motor is the most-stable
# product model and exercises ValueUnit / MinMaxUnit / nested types.
SCHEMA = Motor
PRODUCT_TYPE = "motor"


# Silence the per-row validation-error logging — a 100-example
# property run would otherwise print 100 stack traces per case.
_LOG_NAME = "specodex.utils"


@pytest.fixture(autouse=True)
def _silence_validation_logs():
    logger = logging.getLogger(_LOG_NAME)
    prior_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(prior_level)


class _FakeResponse:
    """Minimal stand-in for ``google.generativeai`` response objects.

    The function accesses ``.text`` and treats truthiness on the
    response itself; nothing else.
    """

    def __init__(self, text: Any):
        self.text = text


# ---------------------------------------------------------------------------
# JSON generation strategies
# ---------------------------------------------------------------------------


# Primitive JSON values — including the surprising-but-legal ones
# (NaN-equivalent floats are dropped; embedded nulls in strings are
# kept; non-BMP unicode included).
_PRIMITIVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(min_size=0, max_size=64),
)


def _json_strategy() -> st.SearchStrategy[Any]:
    """A recursive strategy producing arbitrary JSON-shaped values."""
    return st.recursive(
        _PRIMITIVES,
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(st.text(min_size=0, max_size=12), children, max_size=5),
        ),
        max_leaves=20,
    )


_RAW_TEXT = st.one_of(
    # Pure JSON-encoded payload.
    _json_strategy().map(lambda v: json.dumps(v)),
    # JSON wrapped in markdown fences (Gemini sometimes does this).
    _json_strategy().map(lambda v: f"```json\n{json.dumps(v)}\n```"),
    # Plain text that's not JSON at all.
    st.text(min_size=0, max_size=80),
    # Truncated JSON — cut a real payload mid-string.
    _json_strategy().map(lambda v: json.dumps(v)[: max(0, len(json.dumps(v)) // 2)]),
)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestParseGeminiResponseProperties:
    """Adversarial inputs vs the documented contract."""

    @given(raw=_RAW_TEXT)
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_only_raises_value_error_or_returns_list(self, raw: str) -> None:
        """Malformed input either returns a list or raises ValueError.

        Any other exception type escaping is a regression — the
        parser must be the isolation barrier between Gemini's text
        and the rest of the pipeline.
        """
        response = _FakeResponse(text=raw)
        try:
            result = parse_gemini_response(response, SCHEMA, PRODUCT_TYPE)
        except ValueError:
            return  # documented contract — fine
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"parse_gemini_response raised {type(exc).__name__} "
                f"(expected ValueError or success): {exc!r}\n"
                f"input: {raw!r}"
            )
        # Success path: every returned element must be a Motor instance.
        assert isinstance(result, list)
        for i, item in enumerate(result):
            assert isinstance(item, SCHEMA), (
                f"row {i} is {type(item).__name__}, not Motor — "
                f"parser returned a half-validated object"
            )

    @given(payload=_json_strategy())
    @settings(max_examples=200, deadline=None)
    def test_arbitrary_json_payloads_never_break_invariant(self, payload: Any) -> None:
        """Same property, but the input is always a JSON-serialisable
        value — not a raw string.

        Catches the case where the parser pre-decodes successfully
        but the per-row validation misbehaves on a genuinely-weird
        Pydantic input.
        """
        raw = json.dumps(payload)
        response = _FakeResponse(text=raw)
        try:
            result = parse_gemini_response(response, SCHEMA, PRODUCT_TYPE)
        except ValueError:
            return
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"parse_gemini_response raised {type(exc).__name__} "
                f"on parseable JSON: {exc!r}\ninput: {payload!r}"
            )
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, SCHEMA)

    @given(
        # Ensure context dict has only string keys (Pydantic kwargs).
        context=st.dictionaries(
            st.text(min_size=1, max_size=20).filter(lambda s: s.isidentifier()),
            _PRIMITIVES,
            max_size=4,
        ),
    )
    @settings(max_examples=100, deadline=None)
    def test_context_merge_never_leaks_typeerror(self, context: dict) -> None:
        """Caller-provided context with arbitrary keys/values must not
        crash the merge — the parser swallows per-row failures by
        design (logs and skips), so a context-induced failure should
        either filter out rows or raise ValueError, never propagate.
        """
        raw = json.dumps([{"product_name": "test", "manufacturer": "Acme"}])
        response = _FakeResponse(text=raw)
        try:
            parse_gemini_response(response, SCHEMA, PRODUCT_TYPE, context=context)
        except ValueError:
            return
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"context merge raised {type(exc).__name__}: {exc!r}\n"
                f"context: {context!r}"
            )

    def test_none_response_raises_value_error(self) -> None:
        """Sanity check — the documented "no text" path raises
        ValueError, not AttributeError.
        """
        with pytest.raises(ValueError):
            parse_gemini_response(None, SCHEMA, PRODUCT_TYPE)

    def test_response_without_text_raises_value_error(self) -> None:
        class _NoText:
            pass

        with pytest.raises(ValueError):
            parse_gemini_response(_NoText(), SCHEMA, PRODUCT_TYPE)

    def test_empty_text_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            parse_gemini_response(_FakeResponse(""), SCHEMA, PRODUCT_TYPE)

    def test_top_level_string_raises_value_error(self) -> None:
        """JSON strings (top-level scalars) are not arrays/objects."""
        with pytest.raises(ValueError):
            parse_gemini_response(
                _FakeResponse(json.dumps("just a string")),
                SCHEMA,
                PRODUCT_TYPE,
            )

    def test_top_level_number_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            parse_gemini_response(_FakeResponse(json.dumps(42)), SCHEMA, PRODUCT_TYPE)
