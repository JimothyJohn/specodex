"""Tests for schema.org availability extraction (extract_availability).

Availability is parsed from the same JSON-LD ``Offer`` as price, with
the same SKU guard. These fixtures mirror the shapes seen live
2026-06-12 on Mitsubishi's store, Electromate, and the Kyklo
distributors.
"""

from __future__ import annotations

import json

import pytest

from specodex.pricing.extract import _normalize_availability, extract_availability


def _jsonld(
    availability: str,
    sku: str | None = None,
    mpn: str | None = None,
    product_id: str | None = None,
    price: str = "100.00",
) -> str:
    offer = {"@type": "Offer", "price": price, "priceCurrency": "USD"}
    if availability:
        offer["availability"] = availability
    product = {"@context": "https://schema.org", "@type": "Product", "offers": offer}
    if sku is not None:
        product["sku"] = sku
    if mpn is not None:
        product["mpn"] = mpn
    if product_id is not None:
        product["productID"] = product_id
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(product)
        + "</script></head><body>x</body></html>"
    )


class TestNormalizeAvailability:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("https://schema.org/InStock", "in_stock"),
            ("http://schema.org/InStock", "in_stock"),
            ("https://schema.org/BackOrder", "back_order"),
            ("https://schema.org/OutOfStock", "out_of_stock"),
            ("https://schema.org/SoldOut", "out_of_stock"),
            ("https://schema.org/PreOrder", "pre_order"),
            ("https://schema.org/LimitedAvailability", "limited"),
            ("https://schema.org/Discontinued", "discontinued"),
            ("InStock", "in_stock"),
            ("schema.org/InStock/", "in_stock"),
        ],
    )
    def test_known_values(self, raw, expected):
        assert _normalize_availability(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "Foo", "https://schema.org/Unknown", 42])
    def test_unknown_values_return_none(self, raw):
        assert _normalize_availability(raw) is None


class TestExtractAvailability:
    def test_in_stock(self):
        html = _jsonld("https://schema.org/InStock")
        assert (
            extract_availability(html, "https://x.example/p", "HG-KR43") == "in_stock"
        )

    def test_back_order(self):
        html = _jsonld("https://schema.org/BackOrder")
        assert (
            extract_availability(html, "https://x.example/p", "MR-J4") == "back_order"
        )

    def test_no_availability_field_returns_none(self):
        html = _jsonld("")
        assert extract_availability(html, "https://x.example/p", "X1") is None

    def test_empty_html_returns_none(self):
        assert extract_availability("", "https://x.example/p", "X1") is None

    def test_sku_guard_rejects_when_all_identifiers_mismatch(self):
        # The guard shares the price path's _sku_matches, which treats a
        # MISSING identifier as a pass. So it only rejects when every
        # PRESENT identifier mismatches — a single bad sku is not enough.
        html = _jsonld(
            "https://schema.org/InStock",
            sku="OTHER-1",
            mpn="OTHER-2",
            product_id="OTHER-3",
        )
        assert extract_availability(html, "https://x.example/", "HG-KR43") is None

    def test_single_mismatching_sku_is_accepted(self):
        # Internal-id store pattern (Electromate sku="322827"): a lone
        # mismatching sku must NOT reject, or we'd lose those pages' data.
        # This leniency is intentional and load-bearing — it's why the
        # price path captures Electromate at all.
        html = _jsonld("https://schema.org/InStock", sku="322827")
        assert (
            extract_availability(html, "https://x.example/p", "HG-KR43") == "in_stock"
        )

    def test_sku_guard_accepts_matching_sku(self):
        html = _jsonld("https://schema.org/InStock", sku="HG-KR43")
        assert (
            extract_availability(html, "https://x.example/p", "HG-KR43") == "in_stock"
        )

    def test_no_sku_present_is_accepted(self):
        # Single-product page without a structured SKU — accept.
        html = _jsonld("https://schema.org/InStock")
        assert (
            extract_availability(html, "https://x.example/p", "ANYTHING") == "in_stock"
        )

    def test_offers_list_first_recognized_wins(self):
        product = {
            "@context": "https://schema.org",
            "@type": "Product",
            "offers": [
                {"@type": "Offer", "availability": "https://schema.org/InStock"},
                {"@type": "Offer", "availability": "https://schema.org/BackOrder"},
            ],
        }
        html = (
            '<html><body><script type="application/ld+json">'
            + json.dumps(product)
            + "</script></body></html>"
        )
        assert extract_availability(html, "https://x.example/p", "") == "in_stock"
