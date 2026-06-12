"""Price extraction cascade.

Order of operations (cheap → expensive):

  1. JSON-LD ``Product.offers.price`` / ``priceCurrency``
  2. Microdata: ``meta[itemprop=price]`` / ``meta[property="product:price:amount"]``
  3. Per-domain regex + CSS selector fallback (known price containers)
  4. Gemini LLM last-resort, on cleaned page text

The cascade short-circuits on the first hit. Every result carries the
URL it came from and a ``source_type`` tag assigned by the resolver.

Quality gate: reject prices outside ``[$10, $100K]`` — real industrial
servo drives/motors land comfortably inside that band, and the most
common failure mode is scraping a ``$5`` shipping label or a coupon.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, List, Literal, Optional, Tuple
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from specodex.ids import normalize_string
from specodex.pricing.resolver import source_type_for_domain

logger = logging.getLogger(__name__)

SourceType = Literal["oem", "distributor", "aggregator", "serp"]
PRICE_MIN = Decimal("10")
PRICE_MAX = Decimal("100000")

# Accept JSON-LD prices in any ISO-ish currency but we only record USD.
# Foreign-currency pages are skipped — converting FX is out of scope.
_ACCEPTED_CURRENCIES = {"USD", "US$", "$"}

_PRICE_REGEX = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)")


@dataclass
class PriceResult:
    price_usd: Decimal
    source_url: str
    source_type: SourceType
    extractor: str  # "json-ld", "microdata", "regex", "llm"


# ── JSON-LD ─────────────────────────────────────────────────────────


def _walk_jsonld(node: Any, out: List[dict]) -> None:
    if isinstance(node, list):
        for n in node:
            _walk_jsonld(n, out)
    elif isinstance(node, dict):
        out.append(node)
        for v in node.values():
            _walk_jsonld(v, out)


def _parse_json_loose(text: str) -> Any:
    """Parse JSON-LD leniently — some sites emit multiple top-level objects
    or HTML-escaped content. Try strict, then stripped, then bail."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(text.strip().strip(";,"))
    except json.JSONDecodeError:
        return None


def _sku_matches(candidate: Any, target_pn: str) -> bool:
    """True if JSON-LD SKU/MPN/productID matches ``target_pn``.

    When the page carries no SKU identifier, accept — single-product pages
    don't always repeat the part number in a structured field. When SKU is
    present it MUST match to guard against redirect-to-home pages that
    surface an unrelated product's JSON-LD.
    """
    norm_target = normalize_string(target_pn)
    if not norm_target or not candidate:
        return True
    return normalize_string(str(candidate)) == norm_target


def _extract_jsonld(tree: HTMLParser, target_pn: str = "") -> Optional[Decimal]:
    for node in tree.css('script[type="application/ld+json"]'):
        text = node.text() or ""
        data = _parse_json_loose(text)
        if data is None:
            continue
        nodes: List[dict] = []
        _walk_jsonld(data, nodes)
        for d in nodes:
            type_ = d.get("@type")
            if type_ not in ("Product", "IndividualProduct", "ProductModel"):
                continue
            # SKU guard: many Kyklo-backed stores redirect unknown parts to
            # the homepage, which carries featured-product JSON-LD with an
            # unrelated price. Accept only when the structured SKU matches
            # (or no SKU is present at all).
            sku_candidates = (d.get("sku"), d.get("mpn"), d.get("productID"))
            has_sku = any(sku_candidates)
            if (
                target_pn
                and has_sku
                and not any(_sku_matches(s, target_pn) for s in sku_candidates)
            ):
                continue
            offers = d.get("offers")
            if not offers:
                continue
            if isinstance(offers, list):
                offers_iter = offers
            else:
                offers_iter = [offers]
            for offer in offers_iter:
                if not isinstance(offer, dict):
                    continue
                currency = str(offer.get("priceCurrency") or "").upper()
                if currency and currency not in _ACCEPTED_CURRENCIES:
                    continue
                price_raw = offer.get("price") or offer.get("lowPrice")
                if price_raw is None:
                    continue
                try:
                    return Decimal(str(price_raw).replace(",", "").strip().lstrip("$"))
                except InvalidOperation:
                    continue
    return None


# ── Microdata / Open Graph ──────────────────────────────────────────


def _extract_microdata(tree: HTMLParser) -> Optional[Decimal]:
    # Microdata `content` attributes hold bare decimals like "799.50", not
    # "$799.50" — so try bare-number parsing first, then fall back to the
    # $-prefixed regex for the inner text.
    for node in tree.css('[itemprop="price"]'):
        content_attr = node.attributes.get("content") or ""
        price = _parse_bare_decimal(content_attr)
        if price is None:
            price = _parse_money(node.text() or "")
        if price is not None and _in_band(price):
            return price

    for sel in (
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
        'meta[name="twitter:data1"]',
    ):
        for node in tree.css(sel):
            content = node.attributes.get("content") or ""
            price = _parse_bare_decimal(content)
            if price is None:
                price = _parse_money(content)
            if price is not None and _in_band(price):
                return price
    return None


def _parse_bare_decimal(text: str) -> Optional[Decimal]:
    """Parse ``"1234.00"`` or ``"1,234.00"`` — no currency symbol required.

    Non-finite values are rejected: ``Decimal("NaN")`` / ``Decimal("inf")``
    parse fine but blow up the ``PRICE_MIN <= v`` band comparison with
    ``InvalidOperation`` downstream.
    """
    cleaned = (text or "").strip().replace(",", "").lstrip("$").strip()
    if not cleaned:
        return None
    try:
        val = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not val.is_finite():
        return None
    return val


# ── Regex fallback (per-domain nudges) ──────────────────────────────

# Domain-specific price containers. Kept conservative — if a site's DOM
# changes we'd rather fall through to LLM than extract a wrong price.
_DOMAIN_SELECTORS: dict[str, list[str]] = {
    "galco.com": [".price", "#lblPrice", ".product-price"],
    "wolfautomation.com": [".price", ".product-price", "[class*=price]"],
    "motionindustries.com": [".price", ".productPrice"],
    "newark.com": [".priceWrap", ".productPrice"],
    "alliedelec.com": [".priceWrap", ".productPrice"],
    "grainger.com": [".price-display"],
    "radwell.com": [".product-price", ".price"],
    "plccenter.com": [".product-price", ".price"],
    "orientalmotor.com": [".price", ".product-price"],
    "maxongroup.com": [".price"],
    "automationdirect.com": [".listPrice", ".price"],
    "se.com": [".price"],
}


def _extract_regex(tree: HTMLParser, url: str) -> Optional[Decimal]:
    host = urlparse(url).netloc.lower().lstrip("www.")
    selectors: list[str] = []
    for domain, sel_list in _DOMAIN_SELECTORS.items():
        if host == domain or host.endswith("." + domain):
            selectors = sel_list
            break

    for sel in selectors:
        for node in tree.css(sel):
            price = _parse_money(node.text() or "")
            if price is not None:
                return price

    # Last-chance: the very first $###.## in the page body. Noisy but
    # occasionally catches sites we haven't mapped yet.
    body = tree.body
    if body is not None:
        text = body.text(separator=" ")
        price = _parse_money(text, first_only=True)
        if price is not None:
            return price
    return None


def _parse_money(text: str, first_only: bool = False) -> Optional[Decimal]:
    matches = _PRICE_REGEX.findall(text or "")
    if not matches:
        return None
    for raw in matches:
        try:
            val = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            continue
        if _in_band(val):
            return val
        if first_only:
            return None
    return None


def _in_band(v: Decimal) -> bool:
    return PRICE_MIN <= v <= PRICE_MAX


# ── LLM last resort ─────────────────────────────────────────────────


def _strip_html(tree: HTMLParser) -> str:
    body = tree.body
    if body is None:
        return ""
    text = body.text(separator=" ")
    # Collapse whitespace and clip — we don't want to send an entire
    # catalog page to the LLM.
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]


def _extract_llm(
    tree: HTMLParser,
    url: str,
    manufacturer: str,
    part_number: str,
) -> Optional[Decimal]:
    import os

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.debug("GEMINI_API_KEY unset — skipping LLM price extraction")
        return None

    text = _strip_html(tree)
    if not text:
        return None

    try:
        from google import genai  # type: ignore
    except ImportError:
        logger.info("google-genai SDK not installed — skipping LLM extraction")
        return None

    prompt = (
        f"Extract the USD list price for part number '{part_number}' from "
        f"manufacturer '{manufacturer}'. Respond with ONLY a JSON object of "
        f'the form {{"price_usd": <number>}} or {{"price_usd": null}} if '
        f"the page shows no list price for this part.\n\n"
        f"Page URL: {url}\n\nPage text:\n{text}"
    )

    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "max_output_tokens": 100,
            },
        )
    except Exception as e:  # genai errors or network
        logger.info("LLM price extraction errored on %s: %s", url, e)
        return None

    content = getattr(resp, "text", None) or ""
    if not content:
        return None

    match = re.search(r'"price_usd"\s*:\s*([0-9]+(?:\.[0-9]+)?|null)', content)
    if not match:
        return None
    if match.group(1) == "null":
        return None
    try:
        val = Decimal(match.group(1))
    except InvalidOperation:
        return None
    if not _in_band(val):
        return None
    return val


# ── Public entry ────────────────────────────────────────────────────


def extract_price(
    html: str,
    url: str,
    manufacturer: str,
    part_number: str,
    allow_llm: bool = False,
) -> Optional[Tuple[Decimal, str]]:
    """Run the full cascade. Returns ``(price, extractor_name)`` or None.

    ``source_type`` is deliberately not assigned here — the caller knows
    which tier generated the candidate URL and tags accordingly.
    """
    if not html:
        return None

    tree = HTMLParser(html)

    price = _extract_jsonld(tree, target_pn=part_number)
    if price is not None and _in_band(price):
        return price, "json-ld"

    price = _extract_microdata(tree)
    if price is not None and _in_band(price):
        return price, "microdata"

    price = _extract_regex(tree, url)
    if price is not None and _in_band(price):
        return price, "regex"

    if allow_llm:
        price = _extract_llm(tree, url, manufacturer, part_number)
        if price is not None:
            return price, "llm"

    return None


def classify_url(url: str) -> Optional[SourceType]:
    """Map URL → ``source_type``. Convenience for callers."""
    return source_type_for_domain(urlparse(url).netloc)
