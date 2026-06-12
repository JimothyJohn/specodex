"""Property tests for specodex.pricing.pricebook — the price-book
parser and join eat external bytes (downloaded XLSX) and book-emitted
strings, so they get the adversarial treatment per CLAUDE.md.

Contracts pinned:

- ``parse_xlsx_rows`` raises only ``ValueError`` on garbage bytes and
  returns ``list[list[str]]`` on success.
- ``pairs_from_xlsx`` never returns an out-of-band price and never a
  blank part number.
- ``join_pairs`` only ever matches on normalize_string equality, never
  touches an already-priced product, and returns a subset of its
  inputs (no invented pairs, no invented products).
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from specodex.ids import normalize_string
from specodex.models.drive import Drive
from specodex.pricing.extract import PRICE_MAX, PRICE_MIN
from specodex.pricing.pricebook import (
    MIN_JOIN_KEY_LEN,
    PricePair,
    join_pairs,
    parse_xlsx_rows,
)
from tests.unit.test_pricebook import make_xlsx

# ── parse_xlsx_rows: arbitrary bytes never raise outside ValueError ──

adversarial_bytes = st.one_of(
    st.binary(max_size=2048),
    st.just(b"PK\x03\x04 not really a zip"),
    st.just(b"%PDF-1.7 wrong format"),
    st.just(b""),
    st.binary(min_size=1, max_size=64).map(lambda b: b"PK" + b),
)


@given(adversarial_bytes)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_parse_xlsx_rows_contract(data):
    try:
        rows = parse_xlsx_rows(data)
    except ValueError:
        return
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, list)
        assert all(isinstance(cell, str) for cell in row)


# ── pairs_from_xlsx: output shape from arbitrary cell content ────────

# XML 1.0 cannot represent most control characters even escaped, and
# real Excel files can't carry them — exclude them from cell content
# (the byte-level garbage path is covered by test_parse_xlsx_rows_contract).
_xml_legal = st.characters(codec="utf-8", exclude_categories=("Cs", "Cc", "Co", "Cn"))
cell_text = st.one_of(
    st.text(alphabet=_xml_legal, max_size=20),
    st.just("—"),
    st.just("n/a"),
    st.sampled_from(["478", "1,031.50", "$99.99", "-5", "0", "1e9", "NaN", "inf"]),
)


@given(
    st.lists(
        st.tuples(cell_text, cell_text).map(list),
        min_size=0,
        max_size=20,
    )
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_pairs_from_xlsx_band_and_shape(data_rows):
    from specodex.pricing.pricebook import pairs_from_xlsx

    rows = [["Catalog Number", "List Price"]] + data_rows
    pairs = pairs_from_xlsx(make_xlsx(rows))
    for pair in pairs:
        assert isinstance(pair, PricePair)
        assert pair.part_number.strip()
        assert PRICE_MIN <= pair.price_usd <= PRICE_MAX
        assert pair.price_usd == pair.price_usd  # not NaN


# ── join_pairs: subset + key-equality + no-overwrite invariants ──────

pn_text = st.text(
    alphabet=st.characters(codec="utf-8", exclude_categories=("Cs",)),
    min_size=0,
    max_size=24,
)

price_pairs = st.lists(
    st.builds(
        PricePair,
        part_number=pn_text,
        price_usd=st.decimals(
            min_value=Decimal("10"),
            max_value=Decimal("100000"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        ),
    ),
    max_size=10,
)


def _drive(pn, msrp=None):
    return Drive(
        product_type="drive",
        product_name="d",
        manufacturer="TestCo",
        part_number=pn or None,
        msrp=msrp,
    )


products_strategy = st.lists(
    st.tuples(pn_text, st.booleans()).map(
        lambda t: _drive(t[0], msrp="42;USD" if t[1] else None)
    ),
    max_size=10,
)


@given(pairs=price_pairs, products=products_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_join_pairs_contract(pairs, products):
    matches = join_pairs(pairs, products)

    pair_keys = {normalize_string(p.part_number) for p in pairs}
    seen_products = set()
    for m in matches:
        # subset: every match references a real input product and pair
        assert m.product in products
        assert any(
            p.part_number == m.pair.part_number and p.price_usd == m.pair.price_usd
            for p in pairs
        )
        # key equality on the normalized part number
        key = normalize_string(m.product.part_number)
        assert key and key in pair_keys
        assert key == normalize_string(m.pair.part_number)
        # ambiguity guard: short keys never join
        assert len(key) >= MIN_JOIN_KEY_LEN
        # enrich-only: never matches an already-priced product
        assert m.product.msrp is None
        # one match per product
        assert id(m.product) not in seen_products
        seen_products.add(id(m.product))
