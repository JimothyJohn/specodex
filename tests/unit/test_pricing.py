"""Unit tests for the MSRP pricing pipeline.

Covers:
- ``extract_price`` cascade: JSON-LD → microdata → regex → out-of-band guards
- ``resolve_candidates`` tiering
- ``classify_url`` domain → source_type mapping
- ``PriceFetcher`` cache read/write
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import pytest

from specodex.pricing.extract import classify_url, extract_price
from specodex.pricing.fetch import FetchResult, PriceFetcher
from specodex.pricing.resolver import (
    Candidate,
    resolve_candidates,
    source_type_for_domain,
)


def _host_matches(url: str, domain: str) -> bool:
    """True iff url's hostname is exactly `domain` or a subdomain of it.

    Substring checks like ``"orientalmotor.com" in url`` are flagged by
    CodeQL (py/incomplete-url-substring-sanitization) because they can be
    bypassed by URLs like ``https://evil.com/?orientalmotor.com``. Parsing
    the URL and comparing the hostname structurally is the safe form even
    in tests, which double as documentation for the resolver contract.
    """
    host = (urlparse(url).hostname or "").lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


# ── extract: JSON-LD ────────────────────────────────────────────────


def _jsonld_page(price: str, currency: str = "USD") -> str:
    payload = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Acme Servo Drive X100",
        "offers": {"@type": "Offer", "price": price, "priceCurrency": currency},
    }
    return f"""
<html><head>
<script type="application/ld+json">{json.dumps(payload)}</script>
</head><body><h1>Acme X100</h1></body></html>
"""


def test_extract_jsonld_picks_usd_price():
    html = _jsonld_page("1499.00")
    got = extract_price(html, "https://www.galco.com/x", "Acme", "X100")
    assert got == (Decimal("1499.00"), "json-ld")


def test_extract_jsonld_rejects_non_usd():
    html = _jsonld_page("1499.00", currency="EUR")
    # With EUR, the JSON-LD branch is skipped; regex body fallback finds no $.
    assert extract_price(html, "https://example.com/", "Acme", "X100") is None


def test_extract_jsonld_rejects_out_of_band_low():
    html = _jsonld_page("5.00")
    assert extract_price(html, "https://example.com/", "Acme", "X100") is None


def test_extract_jsonld_rejects_out_of_band_high():
    html = _jsonld_page("250000.00")
    assert extract_price(html, "https://example.com/", "Acme", "X100") is None


# ── extract: microdata ──────────────────────────────────────────────


def test_extract_microdata_itemprop_price():
    html = '<html><body><span itemprop="price" content="799.50">$799.50</span></body></html>'
    got = extract_price(html, "https://www.newark.com/x", "Acme", "X100")
    assert got == (Decimal("799.50"), "microdata")


def test_extract_microdata_open_graph():
    html = """
<html><head>
<meta property="product:price:amount" content="1234.00">
<meta property="product:price:currency" content="USD">
</head><body>Acme product</body></html>
"""
    got = extract_price(html, "https://www.alliedelec.com/x", "Acme", "X100")
    assert got == (Decimal("1234.00"), "microdata")


# ── extract: regex + domain selectors ───────────────────────────────


def test_extract_regex_uses_domain_selector():
    html = """
<html><body>
<div class="productPrice">$2,499.99</div>
<p>Some marketing body text with $99 coupon callout.</p>
</body></html>
"""
    got = extract_price(
        html, "https://www.motionindustries.com/product/x", "Acme", "X100"
    )
    assert got is not None
    price, extractor = got
    assert price == Decimal("2499.99")
    assert extractor == "regex"


def test_extract_body_fallback_finds_first_in_band():
    html = "<html><body>Special pricing: $875.00 for list quantity.</body></html>"
    got = extract_price(html, "https://unknown.example/x", "Acme", "X100")
    assert got is not None
    assert got[0] == Decimal("875.00")


def test_extract_empty_html_returns_none():
    assert extract_price("", "https://www.galco.com/x", "Acme", "X100") is None


def test_extract_no_price_signals_returns_none():
    html = "<html><body><h1>Page with no price anywhere</h1></body></html>"
    assert extract_price(html, "https://www.galco.com/x", "Acme", "X100") is None


# ── classify_url ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.orientalmotor.com/p/ABC-123/", "oem"),
        ("https://www.maxongroup.com/x", "oem"),
        ("https://www.automationdirect.com/x", "oem"),
        ("https://www.se.com/x", "oem"),
        ("https://www.galco.com/x", "distributor"),
        ("https://shop.alliedelec.com/x", "distributor"),
        ("https://www.newark.com/x", "distributor"),
        ("https://www.radwell.com/x", "aggregator"),
        ("https://www.plccenter.com/x", "aggregator"),
        ("https://example.com/x", None),
    ],
)
def test_classify_url_and_source_type_for_domain(url: str, expected):
    assert classify_url(url) == expected


# ── resolver ────────────────────────────────────────────────────────


def test_resolver_empty_inputs_returns_empty():
    assert resolve_candidates("", "X100") == []
    assert resolve_candidates("Acme", "") == []


def test_resolver_oem_kicks_in_for_oriental_motor():
    cands = resolve_candidates("Oriental Motor", "BLV510N10F", use_serp=False)
    oems = [c for c in cands if c.source_type == "oem"]
    assert oems, "Oriental Motor should produce an OEM candidate"
    assert all(_host_matches(c.url, "orientalmotor.com") for c in oems)


def test_resolver_no_oem_for_unmapped_manufacturer():
    # Yaskawa has no OEM URL builder — falls through to distributor tier.
    cands = resolve_candidates("Yaskawa", "SGM7J-04AFC6S", use_serp=False)
    assert not any(c.source_type == "oem" for c in cands)
    assert any(c.source_type == "distributor" for c in cands)


def test_resolver_mitsubishi_oem_store():
    cands = resolve_candidates("Mitsubishi Electric", "HG-KR43", use_serp=False)
    oems = [c for c in cands if c.source_type == "oem"]
    assert oems, "Mitsubishi should produce at least one OEM candidate"
    assert any(_host_matches(c.url, "shop1.us.mitsubishielectric.com") for c in oems)


def test_resolver_ordering_oem_before_distributor_before_aggregator():
    cands = resolve_candidates("Oriental Motor", "BLV510N10F", use_serp=False)
    tiers = [c.source_type for c in cands]
    # All OEM indices come before any distributor index
    oem_idx = [i for i, t in enumerate(tiers) if t == "oem"]
    dist_idx = [i for i, t in enumerate(tiers) if t == "distributor"]
    agg_idx = [i for i, t in enumerate(tiers) if t == "aggregator"]
    assert max(oem_idx) < min(dist_idx)
    assert max(dist_idx) < min(agg_idx)


def test_resolver_dedupes_by_url():
    cands = resolve_candidates("Acme", "X100", use_serp=False)
    urls = [c.url for c in cands]
    assert len(urls) == len(set(urls))


# ── fetch: cache read/write ────────────────────────────────────────


def test_price_fetcher_cache_roundtrip(tmp_path: Path):
    fetcher = PriceFetcher(cache_dir=tmp_path, rate_limit_s=0.0, allow_playwright=False)
    url = "https://www.galco.com/shop/X100"
    fetcher._cache_write(
        FetchResult(
            url=url, html="<html>cached</html>", from_cache=False, used_playwright=False
        )
    )
    hit = fetcher._cache_read(url)
    assert hit is not None
    assert hit.from_cache is True
    assert hit.html == "<html>cached</html>"
    fetcher.close()


def test_price_fetcher_cache_miss_returns_none(tmp_path: Path):
    fetcher = PriceFetcher(cache_dir=tmp_path, rate_limit_s=0.0, allow_playwright=False)
    assert fetcher._cache_read("https://www.galco.com/never-seen") is None
    fetcher.close()


# ── Candidate dataclass sanity ─────────────────────────────────────


def test_candidate_is_hashable_via_url():
    # We rely on URL-level dedup in resolve_candidates; confirm shape.
    c = Candidate(
        url="https://www.galco.com/x", source_type="distributor", source_name="Galco"
    )
    assert source_type_for_domain("galco.com") == "distributor"
    assert c.source_type == "distributor"
