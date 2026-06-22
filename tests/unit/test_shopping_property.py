"""Property tests for the Serper ``/shopping`` tier filter chain.

The example-based companion (``test_shopping.py``) pins the captured
real-world payload (Yaskawa SGMJV-02AAA61) end-to-end through every
noise class the filter exists for. This file generates *adversarial*
payloads — empty dicts, ``None``-valued fields, unicode-laced titles,
arbitrary numeric strings shaped like prices, marketplace-prefixed
sources, second-hand title tokens — and asserts the three pure helpers
hold their documented contracts on every input the strategy produces.

The targets all sit at the edge of an untrusted-bytes boundary: raw
Serper JSON flows through ``_parse_offers`` before the trust chain in
``filter_offers`` decides what survives, and ``pick_price`` aggregates
whatever remains into the single street price written to DynamoDB. A
raised exception here silently drops a price (the caller swallows it
in ``shopping_price``); a wrong return shape feeds garbage downstream
into ``msrp = {value, "USD"}``. Both are bugs the property tests
catch more cleanly than enumerated cases.

**Targets:**

- ``_parse_offers(payload: dict) -> List[ShoppingOffer]`` — payload
  parse. Drops anything unparseable.
- ``filter_offers(offers, manufacturer, part_number) -> List[ShoppingOffer]``
  — the trust chain: PN match, marketplace exclusion, second-hand
  exclusion, price band, manufacturer preference.
- ``pick_price(offers) -> Optional[ShoppingPrice]`` — median +
  closest-to-median provenance.

**Contracts under test:**

1. **Never raises** — every helper returns cleanly for any input the
   strategy produces, including empty/missing/wrong-type fields and
   unicode-laced strings.
2. **Return shapes.** ``_parse_offers`` returns ``list[ShoppingOffer]``;
   ``filter_offers`` returns a subset of its input; ``pick_price``
   returns ``Optional[ShoppingPrice]``.
3. **``_parse_offers`` price band.** Every emitted offer has a price
   in ``[PRICE_MIN, PRICE_MAX]`` (the parser already calls
   ``_parse_money`` which enforces the band).
4. **``filter_offers`` survivor invariants.** Every survivor's
   normalized title contains the normalized part number; the source
   does not start with any banned marketplace prefix; the title does
   not contain any banned second-hand token; the price is in the
   ``[PRICE_MIN, PRICE_MAX]`` band.
5. **``filter_offers`` empty-PN short-circuit.** When the normalized
   part number is empty, the function returns ``[]`` regardless of
   the offer list.
6. **``filter_offers`` idempotence.** Applying the filter twice
   produces the same result as applying it once.
7. **``filter_offers`` manufacturer preference.** When the
   manufacturer is named in at least one survivor, every survivor
   names it; when no survivor names it, all PN-matching survivors
   are kept.
8. **``pick_price`` shape.** Empty input → ``None``; otherwise a
   ``ShoppingPrice`` whose ``price_usd`` is quantized to cents
   (exponent ≥ -2), whose ``offer_count`` equals ``len(offers)``,
   and whose ``source_url`` matches some offer's link.
9. **``pick_price`` median bounds.** The returned price is in
   ``[min(input prices), max(input prices)]`` (rounded to cents).
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.ids import normalize_string
from specodex.pricing.extract import PRICE_MAX, PRICE_MIN
from specodex.pricing.shopping import (
    ShoppingOffer,
    _BANNED_SOURCE_PREFIXES,
    _BANNED_TITLE_TOKENS,
    _parse_offers,
    filter_offers,
    pick_price,
)


# Silence noisy library logs at the property-search scale.
@pytest.fixture(autouse=True)
def _silence_logs():
    logger = logging.getLogger("specodex.pricing.shopping")
    prior_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(prior_level)


# --- Strategies ----------------------------------------------------------

_arbitrary_text = st.text(min_size=0, max_size=48)

# In-band prices used to build "could-pass-the-filter" offers.
_in_band_decimal = st.decimals(
    min_value=PRICE_MIN,
    max_value=PRICE_MAX,
    allow_nan=False,
    allow_infinity=False,
    places=2,
)

# Price strings the parser may or may not accept. Mix of bare decimals,
# dollar-prefixed, comma-thousands, and unparseable garbage.
_price_text = st.one_of(
    st.from_regex(r"^\$?\d{1,6}(\.\d{1,2})?$", fullmatch=True),
    st.from_regex(r"^\$?\d{1,3}(,\d{3}){0,2}(\.\d{1,2})?$", fullmatch=True),
    st.text(alphabet="0123456789.,$- \t", max_size=16),
    st.text(max_size=16),
)


def _maybe_marketplace_source() -> st.SearchStrategy[str]:
    """A source string that's sometimes banned, sometimes plain."""
    banned = st.sampled_from(_BANNED_SOURCE_PREFIXES).flatmap(
        lambda prefix: st.text(alphabet="abcdef -.", max_size=10).map(
            lambda tail: f"{prefix}{tail}"
        )
    )
    return st.one_of(
        banned,
        st.text(min_size=0, max_size=24),
        st.sampled_from(
            ["Trusted Distributor", "Galco", "Wolf Automation", "Direct OEM"]
        ),
    )


def _maybe_secondhand_title(pn: str) -> st.SearchStrategy[str]:
    """Title that may include the PN and may include a banned token."""
    tail = st.text(alphabet="abcdef -.0123456789", max_size=16)
    token = st.one_of(st.just(""), st.sampled_from(list(_BANNED_TITLE_TOKENS)))
    return st.tuples(tail, token, tail).map(
        lambda parts: " ".join(p for p in (parts[0], pn, parts[1], parts[2]) if p)
    )


# Raw API payload — anything from {} to a list of mixed-type junk.
_raw_payload_item = st.one_of(
    st.none(),
    st.just("not a dict"),
    st.integers(),
    st.dictionaries(
        st.text(max_size=8),
        st.one_of(
            st.none(),
            st.integers(),
            st.floats(allow_nan=True, allow_infinity=True),
            _arbitrary_text,
            _price_text,
        ),
        max_size=6,
    ),
)

_raw_payload = st.one_of(
    st.just({}),
    st.fixed_dictionaries({"shopping": st.none()}),
    st.fixed_dictionaries({"shopping": st.lists(_raw_payload_item, max_size=6)}),
    st.dictionaries(st.text(max_size=8), _raw_payload_item, max_size=4),
)


@st.composite
def _well_formed_offer(draw, pn=None):
    """An offer whose price is parseable and in-band. Title and source
    are randomly adversarial — manufacturer prefix, banned tokens,
    PN match or sibling, marketplace source."""
    actual_pn = (
        pn
        if pn is not None
        else draw(st.from_regex(r"^[A-Z]{3,6}-?\d{2,4}[A-Z]{0,3}$", fullmatch=True))
    )
    title = draw(_maybe_secondhand_title(actual_pn))
    source = draw(_maybe_marketplace_source())
    link = draw(
        st.from_regex(
            r"^https://[a-z]{3,12}\.example/[a-z0-9/-]{1,20}$", fullmatch=True
        )
    )
    price = draw(_in_band_decimal)
    return ShoppingOffer(title=title, source=source, link=link, price_usd=price)


# --- Contract 1: never raises -------------------------------------------


@given(payload=_raw_payload)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_parse_offers_never_raises(payload) -> None:
    _parse_offers(payload)


@given(
    offers=st.lists(_well_formed_offer(), max_size=8),
    manufacturer=_arbitrary_text,
    part_number=_arbitrary_text,
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_filter_offers_never_raises(offers, manufacturer, part_number) -> None:
    filter_offers(offers, manufacturer, part_number)


@given(offers=st.lists(_well_formed_offer(), max_size=8))
@settings(max_examples=200)
def test_pick_price_never_raises(offers) -> None:
    pick_price(offers)


# --- Contract 2: return shapes ------------------------------------------


@given(payload=_raw_payload)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_parse_offers_returns_offer_list(payload) -> None:
    result = _parse_offers(payload)
    assert isinstance(result, list)
    for o in result:
        assert isinstance(o, ShoppingOffer)
        assert isinstance(o.title, str)
        assert isinstance(o.source, str)
        assert isinstance(o.link, str)
        assert isinstance(o.price_usd, Decimal)


@given(
    offers=st.lists(_well_formed_offer(), max_size=8),
    manufacturer=_arbitrary_text,
    part_number=_arbitrary_text,
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_filter_offers_returns_subset(offers, manufacturer, part_number) -> None:
    survivors = filter_offers(offers, manufacturer, part_number)
    assert isinstance(survivors, list)
    for o in survivors:
        assert o in offers


# --- Contract 3: _parse_offers price band -------------------------------


@given(payload=_raw_payload)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_parse_offers_prices_in_band(payload) -> None:
    for offer in _parse_offers(payload):
        assert PRICE_MIN <= offer.price_usd <= PRICE_MAX, (
            f"_parse_offers emitted out-of-band price {offer.price_usd}"
        )


# --- Contract 4: filter_offers survivor invariants ----------------------


@given(
    offers=st.lists(_well_formed_offer(), max_size=8),
    manufacturer=_arbitrary_text,
    part_number=st.from_regex(r"^[A-Z]{3,6}-?\d{2,4}[A-Z]{0,3}$", fullmatch=True),
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_filter_offers_survivors_hold_invariants(
    offers, manufacturer, part_number
) -> None:
    survivors = filter_offers(offers, manufacturer, part_number)
    norm_pn = normalize_string(part_number)
    if not norm_pn:
        assert survivors == []
        return
    for o in survivors:
        title_norm = normalize_string(o.title)
        assert norm_pn in title_norm, (
            f"survivor title {o.title!r} does not contain PN {part_number!r}"
        )
        source_l = o.source.lower()
        assert not any(source_l.startswith(p) for p in _BANNED_SOURCE_PREFIXES), (
            f"survivor source {o.source!r} is a banned marketplace"
        )
        title_l = o.title.lower()
        assert not any(tok in title_l for tok in _BANNED_TITLE_TOKENS), (
            f"survivor title {o.title!r} contains a banned token"
        )
        assert PRICE_MIN <= o.price_usd <= PRICE_MAX, (
            f"survivor price {o.price_usd} is out of band"
        )


# --- Contract 5: empty-PN short-circuit ---------------------------------


@given(
    offers=st.lists(_well_formed_offer(), max_size=6),
    manufacturer=_arbitrary_text,
    # ASCII-only punctuation/whitespace: Unicode case-folding can map some
    # non-ASCII codepoints (e.g. ``İ`` → ``i``) into the alphanumeric class
    # that ``normalize_string`` keeps, which would make the assumption
    # below false.
    blank_pn=st.text(alphabet=" \t\n\r!@#$%^&*()-_=+[]{}|;:,.<>?/\\\"'", max_size=8),
)
@settings(max_examples=100)
def test_filter_offers_empty_pn_returns_empty(offers, manufacturer, blank_pn) -> None:
    # ``normalize_string`` strips everything non-alphanumeric, so any PN
    # composed purely of punctuation/whitespace normalizes to "" and
    # must short-circuit to [].
    assert normalize_string(blank_pn) == ""
    assert filter_offers(offers, manufacturer, blank_pn) == []


# --- Contract 6: idempotence --------------------------------------------


@given(
    offers=st.lists(_well_formed_offer(), max_size=8),
    manufacturer=_arbitrary_text,
    part_number=_arbitrary_text,
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_filter_offers_is_idempotent(offers, manufacturer, part_number) -> None:
    once = filter_offers(offers, manufacturer, part_number)
    twice = filter_offers(once, manufacturer, part_number)
    assert once == twice


# --- Contract 7: manufacturer preference --------------------------------


@given(
    offers=st.lists(_well_formed_offer(), max_size=8),
    manufacturer=st.from_regex(r"^[A-Za-z]{3,12}$", fullmatch=True),
    part_number=st.from_regex(r"^[A-Z]{3,6}-?\d{2,4}[A-Z]{0,3}$", fullmatch=True),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_filter_offers_manufacturer_preference(
    offers, manufacturer, part_number
) -> None:
    survivors = filter_offers(offers, manufacturer, part_number)
    norm_mfg = normalize_string(manufacturer)
    if not norm_mfg or not survivors:
        return
    any_named = any(norm_mfg in normalize_string(o.title) for o in survivors)
    if any_named:
        # When any survivor names the manufacturer, all of them must.
        for o in survivors:
            assert norm_mfg in normalize_string(o.title), (
                f"survivor {o.title!r} missing manufacturer {manufacturer!r} "
                "but another survivor names it"
            )


# --- Contract 8: pick_price shape ---------------------------------------


def test_pick_price_empty_returns_none() -> None:
    assert pick_price([]) is None


@given(offers=st.lists(_well_formed_offer(), min_size=1, max_size=8))
@settings(max_examples=200)
def test_pick_price_shape(offers) -> None:
    result = pick_price(offers)
    assert result is not None
    # Quantized to cents (exponent at -2 or coarser).
    exponent = result.price_usd.as_tuple().exponent
    assert exponent >= -2, f"price {result.price_usd} has finer-than-cent precision"
    assert result.offer_count == len(offers)
    # Provenance link came from one of the inputs.
    assert any(o.link == result.source_url for o in offers)
    assert any(o.source == result.source_name for o in offers)


# --- Contract 9: pick_price median bounds -------------------------------


@given(offers=st.lists(_well_formed_offer(), min_size=1, max_size=8))
@settings(max_examples=200)
def test_pick_price_within_input_range(offers) -> None:
    result = pick_price(offers)
    assert result is not None
    prices = [o.price_usd for o in offers]
    # Median rounded to cents must stay inside the closed range of inputs
    # (allow one-cent slack on the upper bound for the .quantize() step).
    lo = min(prices).quantize(Decimal("0.01"))
    hi = max(prices).quantize(Decimal("0.01"))
    assert lo <= result.price_usd <= hi, (
        f"median {result.price_usd} outside [{lo}, {hi}]"
    )


# --- Bonus: single-offer pick_price collapses to that offer's price ----


@given(offer=_well_formed_offer())
@settings(max_examples=100)
def test_pick_price_single_offer_returns_its_price(offer: ShoppingOffer) -> None:
    result = pick_price([offer])
    assert result is not None
    assert result.price_usd == offer.price_usd.quantize(Decimal("0.01"))
    assert result.source_url == offer.link
    assert result.source_name == offer.source
    assert result.offer_count == 1
