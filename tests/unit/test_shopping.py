"""Tests for the Serper /shopping tier (specodex/pricing/shopping.py).

The fixture is a real captured response (2026-06-12) for
``Yaskawa SGMJV-02AAA61`` — it contains every noise class the filter
exists for: eBay gray-market offers with a 5x spread, sibling part
numbers (SGMJV-02A3A61, SGMAH-...), a "Used Surplus" listing, and a
mislabeled-brand broker offer ("Omron SGMJV-02AAA61").
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from specodex.pricing.shopping import (
    ShoppingOffer,
    _parse_offers,
    filter_offers,
    pick_price,
    shopping_price,
)

FIXTURE = Path(__file__).parent / "fixtures" / "serper_shopping_sgmjv.json"


@pytest.fixture()
def sgmjv_offers():
    payload = json.loads(FIXTURE.read_text())
    return _parse_offers(payload)


def _offer(
    title, source="Trusted Distributor", price="100.00", link="https://x.example/p"
):
    return ShoppingOffer(
        title=title, source=source, link=link, price_usd=Decimal(price)
    )


class TestParseOffers:
    def test_fixture_parses(self, sgmjv_offers):
        assert len(sgmjv_offers) >= 20
        assert all(o.price_usd > 0 for o in sgmjv_offers)

    def test_garbage_payloads(self):
        assert _parse_offers({}) == []
        assert _parse_offers({"shopping": None}) == []
        assert _parse_offers({"shopping": ["not-a-dict", {"price": "n/a"}]}) == []


class TestFilterOffers:
    def test_fixture_filter_chain(self, sgmjv_offers):
        survivors = filter_offers(sgmjv_offers, "Yaskawa", "SGMJV-02AAA61")
        assert survivors, "expected at least one trusted offer"
        for o in survivors:
            assert "02aaa61" in o.title.lower().replace("-", "")
            assert not o.source.lower().startswith("ebay")
            assert "used" not in o.title.lower()
            assert "surplus" not in o.title.lower()

    def test_sibling_part_number_rejected(self):
        offers = [_offer("Yaskawa SGMJV-02A3A61 Servo Motor")]
        assert filter_offers(offers, "Yaskawa", "SGMJV-02AAA61") == []

    def test_marketplace_sources_rejected(self):
        offers = [
            _offer("Yaskawa SGMJV-02AAA61 New", source="eBay - someseller"),
            _offer("Yaskawa SGMJV-02AAA61", source="Amazon.com - Seller"),
        ]
        assert filter_offers(offers, "Yaskawa", "SGMJV-02AAA61") == []

    def test_secondhand_titles_rejected(self):
        offers = [
            _offer("Yaskawa SGMJV-02AAA61 Used Surplus"),
            _offer("Yaskawa SGMJV-02AAA61 Refurbished"),
            _offer("Yaskawa SGMJV-02AAA61 open box"),
        ]
        assert filter_offers(offers, "Yaskawa", "SGMJV-02AAA61") == []

    def test_manufacturer_preference_drops_mislabeled(self):
        offers = [
            _offer("Yaskawa SGMJV-02AAA61 New In Box", price="450.00"),
            _offer("Omron SGMJV-02AAA61", source="Broker", price="1260.00"),
        ]
        survivors = filter_offers(offers, "Yaskawa", "SGMJV-02AAA61")
        assert len(survivors) == 1
        assert survivors[0].price_usd == Decimal("450.00")

    def test_no_manufacturer_named_keeps_pn_matches(self):
        offers = [_offer("SGMJV-02AAA61 servo", price="500.00")]
        survivors = filter_offers(offers, "Yaskawa", "SGMJV-02AAA61")
        assert len(survivors) == 1

    def test_out_of_band_rejected(self):
        offers = [
            _offer("Yaskawa SGMJV-02AAA61", price="5.00"),
            _offer("Yaskawa SGMJV-02AAA61", price="250000.00"),
        ]
        assert filter_offers(offers, "Yaskawa", "SGMJV-02AAA61") == []

    def test_empty_pn_returns_empty(self):
        assert filter_offers([_offer("anything")], "Yaskawa", "") == []


class TestPickPrice:
    def test_median_of_survivors(self):
        offers = [
            _offer("a SGMJV-02AAA61", price="445.44"),
            _offer("b SGMJV-02AAA61", price="497.87"),
            _offer("c SGMJV-02AAA61", price="1260.45"),
        ]
        result = pick_price(offers)
        assert result is not None
        assert result.price_usd == Decimal("497.87")
        assert result.offer_count == 3
        # provenance link comes from the offer closest to the median
        assert result.source_url == offers[1].link

    def test_empty_returns_none(self):
        assert pick_price([]) is None

    def test_even_count_median_quantizes_to_cents(self):
        # median of two offers averages them — must not emit half-cents
        # (live run wrote $471.655 before this guard).
        offers = [
            _offer("a SGMJV-02AAA61", price="445.44"),
            _offer("b SGMJV-02AAA61", price="497.87"),
        ]
        result = pick_price(offers)
        assert result is not None
        assert result.price_usd == Decimal("471.66")
        assert -result.price_usd.as_tuple().exponent <= 2

    def test_fixture_end_to_end(self, sgmjv_offers):
        result = pick_price(filter_offers(sgmjv_offers, "Yaskawa", "SGMJV-02AAA61"))
        assert result is not None
        # Sane street price for a 200W servo motor; never an eBay outlier.
        assert Decimal("200") < result.price_usd < Decimal("2000")


class TestShoppingPrice:
    def test_no_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        assert shopping_price("Yaskawa", "SGMJV-02AAA61") is None

    def test_credit_exhaustion_trips_circuit_breaker(self, monkeypatch):
        # Regression (2026-06-12): an 819-product sweep batch fired 819
        # doomed queries after Serper ran out of credits mid-run. The
        # first "Not enough credits" 400 must disable the tier for the
        # rest of the process.
        import httpx

        import specodex.pricing.shopping as shopping_mod

        shopping_mod.reset_circuit_breaker()
        monkeypatch.setenv("SERPER_API_KEY", "test-key-not-real")
        calls = {"n": 0}

        def fake_post(*args, **kwargs):
            calls["n"] += 1
            return httpx.Response(
                400,
                text='{"message":"Not enough credits","statusCode":400}',
                request=httpx.Request("POST", shopping_mod._SHOPPING_ENDPOINT),
            )

        monkeypatch.setattr(shopping_mod.httpx, "post", fake_post)
        assert shopping_price("Yaskawa", "SGMJV-02AAA61") is None
        assert shopping_price("Yaskawa", "SGM7J-A5AFC6S") is None
        assert calls["n"] == 1, "second query must not reach the API"
        shopping_mod.reset_circuit_breaker()

    def test_empty_inputs_return_none(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "test-key-not-real")
        assert shopping_price("", "X100") is None
        assert shopping_price("Acme", "") is None
