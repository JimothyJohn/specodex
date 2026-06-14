"""Property tests for the private parsers in ``specodex.pricing.extract``.

The example-based companion (``test_pricing.py``) pins the price-cascade
happy path end-to-end (JSON-LD → microdata → regex → out-of-band guard).
This file generates *adversarial* HTML/JSON byte strings — empty,
whitespace-only, unicode-laced, arbitrary ASCII, malformed JSON, numbers
shaped like prices but not quite — and asserts the three private parsers
hold their documented contracts on every input the strategy produces.

The targets sit at the edge of an untrusted-bytes boundary: arbitrary
scraped HTML / JSON-LD payloads flow through these three helpers before
the cascade in ``extract_price`` decides whether to keep them. A raised
exception here aborts the cascade for that source and silently drops a
candidate price; a wrong return shape feeds a bad Decimal into the
``PRICE_MIN ≤ v ≤ PRICE_MAX`` band check downstream. Both are bugs the
property tests catch more cleanly than enumerated cases.

**Targets:**

- ``_parse_bare_decimal(text)`` — coerces bare decimal text (``"1234.00"``,
  ``"1,234.00"``, optionally ``$``-prefixed) into ``Optional[Decimal]``.
- ``_parse_money(text, first_only)`` — regex-scans for ``$###[,###]*[.##]``,
  returns first in-band ``Decimal`` or ``None``.
- ``_parse_json_loose(text)`` — lenient JSON-LD parse: ``json.loads`` →
  strip ``;,`` → ``json.loads`` → ``None``.

**Contracts under test:**

1. **Never raises** — every parser returns cleanly (``None`` or a
   well-typed value) for any ``str`` input, including empty,
   whitespace-only, unicode, control characters, and adversarial
   ASCII patterns. ``_parse_bare_decimal`` additionally accepts
   ``None`` per its ``(text or "")`` guard.
2. **Return shape.** ``_parse_bare_decimal`` and ``_parse_money``
   return ``None`` or a ``Decimal``; ``_parse_json_loose`` returns
   ``None`` or a JSON-compatible value (``dict``, ``list``, ``str``,
   ``int``, ``float``, ``bool``, or ``None``).
3. **Empty / whitespace-only inputs return None** for both decimal
   parsers — the cascade depends on this to fall through to the next
   tier instead of pinning a zero price.
4. **``_parse_money`` band guarantee.** When the function returns a
   ``Decimal``, the value is in ``[PRICE_MIN, PRICE_MAX]``.
5. **``_parse_money`` first-only short-circuit.** When ``first_only=True``
   and the first regex match is out of band, the function returns
   ``None`` (does NOT scan subsequent matches).
6. **``_parse_bare_decimal`` round-trip.** For any finite, in-band
   ``Decimal`` rendered via ``str()``, the parser returns the same
   ``Decimal``. Same for the variant prefixed with ``"$"`` or with
   ``,`` thousands separators.
7. **``_parse_json_loose`` strict-JSON pass-through.** For any
   ``json.dumps``-able Python object, the parser returns a value
   equal to the original (modulo the standard JSON-trip caveats —
   we restrict the strategy to JSON-safe values).
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.pricing.extract import (
    PRICE_MAX,
    PRICE_MIN,
    _parse_bare_decimal,
    _parse_json_loose,
    _parse_money,
)


# Silence noisy library logs at the property-search scale.
@pytest.fixture(autouse=True)
def _silence_logs():
    logger = logging.getLogger("specodex.pricing.extract")
    prior_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(prior_level)


# --- Strategies ----------------------------------------------------------

# Arbitrary text — covers empty, whitespace-only, ASCII, unicode, control chars.
_arbitrary_text = st.one_of(
    st.text(min_size=0, max_size=64),
    st.just(""),
    st.just("   "),
    st.just("\t\n\r"),
    st.text(alphabet=st.characters(blacklist_categories=()), max_size=32),
)

# Numeric-looking text that may or may not parse as Decimal.
_decimal_like_text = st.one_of(
    st.from_regex(r"^\d{1,6}(\.\d{1,2})?$", fullmatch=True),
    st.from_regex(r"^\$\d{1,6}(\.\d{1,2})?$", fullmatch=True),
    st.from_regex(r"^\d{1,3}(,\d{3}){0,2}(\.\d{1,2})?$", fullmatch=True),
    st.from_regex(r"^\$\d{1,3}(,\d{3}){0,2}(\.\d{1,2})?$", fullmatch=True),
    st.text(alphabet="0123456789.,$- \t", max_size=20),
)

# In-band Decimal values that the cascade would actually accept.
_in_band_decimal = st.decimals(
    min_value=PRICE_MIN,
    max_value=PRICE_MAX,
    allow_nan=False,
    allow_infinity=False,
    places=2,
)

# JSON-safe Python values for the strict-JSON round-trip test.
_json_value = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(2**31), max_value=2**31 - 1),
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        st.text(max_size=16),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=8), children, max_size=4),
    ),
    max_leaves=8,
)


# --- Contract 1: never raises -------------------------------------------


@given(text=_arbitrary_text)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_parse_bare_decimal_never_raises(text: str) -> None:
    _parse_bare_decimal(text)


@given(text=_arbitrary_text, first_only=st.booleans())
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_parse_money_never_raises(text: str, first_only: bool) -> None:
    _parse_money(text, first_only=first_only)


@given(text=_arbitrary_text)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_parse_json_loose_never_raises(text: str) -> None:
    _parse_json_loose(text)


# ``_parse_bare_decimal`` documents ``(text or "")`` — None is part of the
# accepted input domain even though the type hint says ``str``.
def test_parse_bare_decimal_accepts_none() -> None:
    assert _parse_bare_decimal(None) is None  # type: ignore[arg-type]


# --- Contract 2: return shape -------------------------------------------


@given(text=_arbitrary_text)
@settings(max_examples=200)
def test_parse_bare_decimal_return_shape(text: str) -> None:
    result = _parse_bare_decimal(text)
    assert result is None or isinstance(result, Decimal)


@given(text=_arbitrary_text, first_only=st.booleans())
@settings(max_examples=200)
def test_parse_money_return_shape(text: str, first_only: bool) -> None:
    result = _parse_money(text, first_only=first_only)
    assert result is None or isinstance(result, Decimal)


@given(text=_arbitrary_text)
@settings(max_examples=200)
def test_parse_json_loose_return_shape(text: str) -> None:
    result = _parse_json_loose(text)
    # JSON values: dict, list, str, int, float, bool, None.
    assert result is None or isinstance(result, (dict, list, str, int, float, bool))


# --- Contract 3: empty/whitespace returns None ---------------------------


@given(text=st.from_regex(r"^[\s$]*$", fullmatch=True).filter(lambda s: len(s) <= 32))
@settings(max_examples=200)
def test_parse_bare_decimal_blank_returns_none(text: str) -> None:
    # Empty / whitespace / lone ``$`` characters carry no numeric content;
    # the cascade depends on this to fall through to the next tier.
    assert _parse_bare_decimal(text) is None


@given(text=st.from_regex(r"^\s*$", fullmatch=True).filter(lambda s: len(s) <= 32))
@settings(max_examples=100)
def test_parse_money_blank_returns_none(text: str) -> None:
    assert _parse_money(text) is None
    assert _parse_money(text, first_only=True) is None


# --- Contract 4: _parse_money band guarantee ----------------------------


@given(text=_arbitrary_text, first_only=st.booleans())
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_parse_money_result_is_in_band(text: str, first_only: bool) -> None:
    result = _parse_money(text, first_only=first_only)
    if result is not None:
        assert PRICE_MIN <= result <= PRICE_MAX, (
            f"_parse_money returned out-of-band {result} for {text!r}"
        )


# --- Contract 5: first_only short-circuits ------------------------------


@given(
    out_of_band=st.one_of(
        st.integers(min_value=0, max_value=int(PRICE_MIN) - 1),
        st.integers(min_value=int(PRICE_MAX) + 1, max_value=10**9),
    ),
    in_band=st.integers(min_value=int(PRICE_MIN), max_value=int(PRICE_MAX)),
)
@settings(max_examples=100)
def test_parse_money_first_only_skips_later_matches(
    out_of_band: int, in_band: int
) -> None:
    # First match out of band, second in band. With first_only=True the
    # function returns None; with first_only=False it returns the second.
    text = f"$ {out_of_band} ... $ {in_band}"
    if not (PRICE_MIN <= Decimal(in_band) <= PRICE_MAX):
        # Strategy excludes most of these but Decimal(int) == int conversion is safe.
        return
    assert _parse_money(text, first_only=True) is None
    assert _parse_money(text, first_only=False) == Decimal(in_band)


# --- Contract 6: _parse_bare_decimal round-trip --------------------------


@given(value=_in_band_decimal)
@settings(max_examples=200)
def test_parse_bare_decimal_roundtrip_plain(value: Decimal) -> None:
    rendered = str(value)
    parsed = _parse_bare_decimal(rendered)
    assert parsed == value


@given(value=_in_band_decimal)
@settings(max_examples=200)
def test_parse_bare_decimal_roundtrip_dollar_prefix(value: Decimal) -> None:
    rendered = f"${value}"
    parsed = _parse_bare_decimal(rendered)
    assert parsed == value


@given(value=_in_band_decimal, leading=st.text(alphabet=" \t", max_size=4))
@settings(max_examples=100)
def test_parse_bare_decimal_strips_surrounding_whitespace(
    value: Decimal, leading: str
) -> None:
    rendered = f"{leading}{value}{leading}"
    parsed = _parse_bare_decimal(rendered)
    assert parsed == value


# --- Contract 7: _parse_json_loose strict-JSON pass-through --------------


@given(value=_json_value)
@settings(max_examples=200)
def test_parse_json_loose_roundtrips_valid_json(value) -> None:
    encoded = json.dumps(value)
    decoded = _parse_json_loose(encoded)
    # json round-trip: NaN/inf excluded by the strategy, no precision games.
    assert decoded == value


@given(
    value=_json_value,
    trailer=st.sampled_from([";", ",", ";;", ",;", ";,"]),
)
@settings(max_examples=100)
def test_parse_json_loose_tolerates_trailing_punctuation(value, trailer: str) -> None:
    # Some sites emit JSON-LD with a trailing ``;`` or ``,`` inside the
    # script tag. The second branch of _parse_json_loose strips them.
    encoded = json.dumps(value) + trailer
    decoded = _parse_json_loose(encoded)
    assert decoded == value


def test_parse_json_loose_malformed_returns_none() -> None:
    assert _parse_json_loose("not json at all") is None
    assert _parse_json_loose("{unclosed") is None
    assert _parse_json_loose("") is None
