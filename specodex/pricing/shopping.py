"""Serper ``/shopping`` tier — structured per-PN prices, no scraping.

One POST per part number returns Google Shopping offers as JSON
(merchant name, title, price). No page fetch, no robots exposure, no
HTML parsing — but the raw results are noisy in exactly the ways the
2026-06-12 live sample (tests/unit/fixtures/serper_shopping_sgmjv.json)
shows: eBay gray-market listings with a 5x price spread, near-miss part
numbers (``SGMJV-02A3A61`` offered against a ``SGMJV-02AAA61`` query),
"Used Surplus" stock, and broker markup. The filter chain:

1. **Exact part-number match** — ``normalize_string(pn)`` must appear
   in the normalized title. Kills sibling-variant offers.
2. **Marketplace / second-hand exclusion** — eBay/Amazon-style sources
   and used/surplus/refurb titles are never trusted for list price.
3. **Manufacturer preference** — when any surviving offer names the
   manufacturer in its title, offers that don't are dropped (catches
   mislabeled-brand listings).
4. **Price band** — same ``[$10, $100K]`` guard as the extractor.
5. **Median** of survivors — robust to one outlier broker.

The returned price is a *street* price, not a book list price — the
caller records the offer link as ``msrp_source_url`` so the provenance
is visible per row.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import List, Optional

import httpx

from specodex.ids import normalize_string
from specodex.pricing.extract import PRICE_MAX, PRICE_MIN, _parse_money

logger = logging.getLogger(__name__)

_SHOPPING_ENDPOINT = "https://google.serper.dev/shopping"

# Circuit breaker: once Serper says the account is out of credits,
# every further query this process makes is doomed — flip this and
# stop asking (observed live 2026-06-12: an 819-product batch burned
# through 819 instant 400s after the credits ran out mid-sweep).
_credits_exhausted = False


def reset_circuit_breaker() -> None:
    """Re-arm the shopping tier (tests / long-lived processes)."""
    global _credits_exhausted
    _credits_exhausted = False


# Sources we never take prices from: marketplaces and auction sites are
# gray-market/used territory regardless of what the title claims.
_BANNED_SOURCE_PREFIXES = (
    "ebay",
    "amazon",
    "aliexpress",
    "alibaba",
    "walmart",
    "etsy",
    "wish",
)

# Title tokens that mark second-hand or service listings.
_BANNED_TITLE_TOKENS = (
    "used",
    "surplus",
    "refurb",
    "recondition",
    "repair",
    "pre-owned",
    "preowned",
    "open box",
    "for parts",
)


@dataclass(frozen=True)
class ShoppingOffer:
    title: str
    source: str
    link: str
    price_usd: Decimal


@dataclass(frozen=True)
class ShoppingPrice:
    price_usd: Decimal
    source_url: str
    source_name: str
    offer_count: int  # surviving offers the median was taken over


def _parse_offers(payload: dict) -> List[ShoppingOffer]:
    """Raw API payload → typed offers. Drops anything unparseable."""
    out: List[ShoppingOffer] = []
    for item in payload.get("shopping") or []:
        if not isinstance(item, dict):
            continue
        price = _parse_money(str(item.get("price") or ""))
        if price is None:
            continue
        out.append(
            ShoppingOffer(
                title=str(item.get("title") or ""),
                source=str(item.get("source") or ""),
                link=str(item.get("link") or ""),
                price_usd=price,
            )
        )
    return out


def filter_offers(
    offers: List[ShoppingOffer], manufacturer: str, part_number: str
) -> List[ShoppingOffer]:
    """Apply the trust chain (see module docstring). Pure — no I/O."""
    norm_pn = normalize_string(part_number)
    if not norm_pn:
        return []

    survivors: List[ShoppingOffer] = []
    for o in offers:
        title_norm = normalize_string(o.title)
        if norm_pn not in title_norm:
            continue
        source_l = o.source.lower()
        if any(source_l.startswith(p) for p in _BANNED_SOURCE_PREFIXES):
            continue
        title_l = o.title.lower()
        if any(tok in title_l for tok in _BANNED_TITLE_TOKENS):
            continue
        if not (PRICE_MIN <= o.price_usd <= PRICE_MAX):
            continue
        survivors.append(o)

    # Manufacturer preference: if any survivor names the manufacturer,
    # drop the ones that don't (mislabeled-brand guard).
    norm_mfg = normalize_string(manufacturer)
    if norm_mfg:
        named = [o for o in survivors if norm_mfg in normalize_string(o.title)]
        if named:
            survivors = named
    return survivors


def pick_price(offers: List[ShoppingOffer]) -> Optional[ShoppingPrice]:
    """Median of surviving offers; the offer closest to the median
    provides the provenance link."""
    if not offers:
        return None
    med = Decimal(median(o.price_usd for o in offers)).quantize(Decimal("0.01"))
    closest = min(offers, key=lambda o: abs(o.price_usd - med))
    return ShoppingPrice(
        price_usd=med,
        source_url=closest.link,
        source_name=closest.source,
        offer_count=len(offers),
    )


def shopping_price(
    manufacturer: str,
    part_number: str,
    api_key: Optional[str] = None,
    timeout_s: float = 15.0,
) -> Optional[ShoppingPrice]:
    """One shopping query → filtered median price, or None.

    Returns None (never raises) on missing key, transport errors, or
    when no offer survives the trust chain.
    """
    global _credits_exhausted
    if _credits_exhausted:
        return None
    key = api_key or os.environ.get("SERPER_API_KEY")
    if not key:
        logger.debug("SERPER_API_KEY not set — shopping tier disabled")
        return None
    if not manufacturer or not part_number:
        return None

    try:
        resp = httpx.post(
            _SHOPPING_ENDPOINT,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": f"{manufacturer} {part_number}", "num": 20},
            timeout=timeout_s,
        )
        if resp.status_code == 400 and "credits" in resp.text.lower():
            _credits_exhausted = True
            logger.warning(
                "Serper account is out of credits — shopping tier disabled "
                "for the rest of this run (top up at serper.dev to resume)"
            )
            return None
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        logger.info("shopping query failed for %s %s: %s", manufacturer, part_number, e)
        return None

    offers = filter_offers(_parse_offers(payload), manufacturer, part_number)
    result = pick_price(offers)
    if result is not None:
        logger.debug(
            "shopping: %s %s → $%s (median of %d offers via %s)",
            manufacturer,
            part_number,
            result.price_usd,
            result.offer_count,
            result.source_name,
        )
    return result
