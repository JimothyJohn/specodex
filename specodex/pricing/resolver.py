"""URL resolver cascade for MSRP lookup.

Given ``(manufacturer, part_number)`` produce an ordered list of candidate
URLs, each tagged with a source tier so downstream code can prefer OEM
over distributor over aggregator. Tiers:

  - ``oem``         — manufacturer's own product-detail pages
  - ``distributor`` — authorized-distributor search or product URLs
  - ``aggregator``  — Radwell / PLCCenter (low-confidence)
  - ``serp``        — Serper.dev SERP fallback, constrained to our known
                      Tier 1-3 domains via a ``site:`` OR-list

Direct OEM product URLs are only attempted for vendors known to publish
list prices and to use deterministic URL schemes. Everything else goes
through distributor search endpoints that return an HTML result page;
the extractor parses that page just like any other product page.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Iterable, List, Literal, Optional
from urllib.parse import quote_plus, urlparse

import httpx

from specodex.ids import normalize_string

logger = logging.getLogger(__name__)

SourceType = Literal["oem", "distributor", "aggregator", "serp"]

# Domains we're willing to take a price from. SERP fallback constrains
# queries to this list, and the extractor uses it to tag source_type.
_OEM_DOMAINS: dict[str, str] = {
    "orientalmotor.com": "Oriental Motor",
    "maxongroup.com": "Maxon Group",
    "automationdirect.com": "AutomationDirect",
    "se.com": "Schneider Electric",
    # Mitsubishi's US factory-automation store publishes JSON-LD list prices
    # per part number. Confirmed live on HG-KR43.
    "shop1.us.mitsubishielectric.com": "Mitsubishi Electric (official store)",
}

_DISTRIBUTOR_DOMAINS: dict[str, str] = {
    "galco.com": "Galco",
    "wolfautomation.com": "Wolf Automation",
    "motionindustries.com": "Motion Industries",
    "newark.com": "Newark",
    "alliedelec.com": "Allied Electronics",
    "grainger.com": "Grainger",
    # Magento storefront with direct lowercase-PN product slugs and
    # server-rendered prices (verified live 2026-06-12). 404 on miss.
    # NOTE: bodine-electric.com was probed and rejected — /products/<pn>
    # 302s to the catalog page, which would body-fallback a wrong price.
    "electromate.com": "Electromate",  # AMC, Galil, Maxon, Applied Motion
    # Kyklo-backed storefronts. Same JSON-LD Product.offers.price scheme
    # as shop1.us.mitsubishielectric.com; URL pattern is /products/{pn}
    # with a redirect to / when the part isn't carried (handled by the
    # fetcher's redirect-to-root guard).
    "shop.iecsupply.com": "IEC Supply",  # Phoenix Contact, Rittal
    "shop.lakewoodautomation.com": "Lakewood Automation",  # Omron, Wago
    "shop.lakelandengineering.com": "Lakeland Engineering",  # ABB, Crouzet, Dynapar, Marathon Special
    "shop.fabco-air.com": "Fabco-Air",  # fluid power + Fabco-Air own brand
}

# Kyklo distributors all use the same product-URL scheme. Listing them here
# keeps resolver.py readable — adding a new Kyklo distributor just means
# appending one line to _KYKLO_DISTRIBUTORS and _DISTRIBUTOR_DOMAINS.
_KYKLO_DISTRIBUTORS: tuple[tuple[str, str], ...] = (
    ("shop.iecsupply.com", "IEC Supply"),
    ("shop.lakewoodautomation.com", "Lakewood Automation"),
    ("shop.lakelandengineering.com", "Lakeland Engineering"),
    ("shop.fabco-air.com", "Fabco-Air"),
)

_AGGREGATOR_DOMAINS: dict[str, str] = {
    "radwell.com": "Radwell International",
    "plccenter.com": "PLC Center",
}


@dataclass
class Candidate:
    url: str
    source_type: SourceType
    source_name: str  # human-readable site name


def source_type_for_domain(netloc: str) -> Optional[SourceType]:
    """Classify a URL's netloc into our tier system."""
    host = netloc.lower().lstrip(".")
    # strip leading "www."
    if host.startswith("www."):
        host = host[4:]
    for d in _OEM_DOMAINS:
        if host == d or host.endswith("." + d):
            return "oem"
    for d in _DISTRIBUTOR_DOMAINS:
        if host == d or host.endswith("." + d):
            return "distributor"
    for d in _AGGREGATOR_DOMAINS:
        if host == d or host.endswith("." + d):
            return "aggregator"
    return None


def source_name_for_domain(netloc: str) -> str:
    host = netloc.lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    for d, name in {
        **_OEM_DOMAINS,
        **_DISTRIBUTOR_DOMAINS,
        **_AGGREGATOR_DOMAINS,
    }.items():
        if host == d or host.endswith("." + d):
            return name
    return host


# ── Tier 1: OEM direct URLs ──────────────────────────────────────────
#
# Only vendors whose part-number URL scheme is stable get direct
# construction. Everything else falls through to distributor search.


def _oem_candidates(manufacturer: str, part_number: str) -> List[Candidate]:
    out: List[Candidate] = []
    mfg = normalize_string(manufacturer)
    pn = part_number.strip()
    pn_enc = quote_plus(pn)

    if mfg in ("orientalmotor", "oriental", "orientalmotorusa"):
        # Oriental Motor: product pages at /p/<part_number>/ and a search endpoint.
        out.append(
            Candidate(
                url=f"https://www.orientalmotor.com/search/?q={pn_enc}",
                source_type="oem",
                source_name="Oriental Motor",
            )
        )

    if mfg in ("maxon", "maxongroup", "maxonmotor"):
        out.append(
            Candidate(
                url=f"https://www.maxongroup.com/maxon/view/search?keyword={pn_enc}",
                source_type="oem",
                source_name="Maxon Group",
            )
        )

    if mfg in ("automationdirect", "adc"):
        out.append(
            Candidate(
                url=f"https://www.automationdirect.com/adc/shopping/catalog?search_term={pn_enc}",
                source_type="oem",
                source_name="AutomationDirect",
            )
        )

    if mfg in ("schneider", "schneiderelectric", "se"):
        out.append(
            Candidate(
                url=f"https://www.se.com/us/en/search/?pssearch=true&q={pn_enc}",
                source_type="oem",
                source_name="Schneider Electric",
            )
        )

    if mfg in (
        "mitsubishi",
        "mitsubishielectric",
        "mitsubishielectrics",
        "mitsubishielectriccorp",
        "mitsubishielectricautomation",
    ):
        # The shop returns a product detail page directly at /products/{pn}
        # when the part number is exact; otherwise returns a search results
        # page. Either is crawlable by our extractor.
        out.append(
            Candidate(
                url=f"https://shop1.us.mitsubishielectric.com/products/{pn_enc}",
                source_type="oem",
                source_name="Mitsubishi Electric (official store)",
            )
        )
        out.append(
            Candidate(
                url=f"https://shop1.us.mitsubishielectric.com/catalog?q={pn_enc}",
                source_type="oem",
                source_name="Mitsubishi Electric (official store)",
            )
        )

    return out


# ── Tier 2: distributor search URLs ─────────────────────────────────


def _distributor_candidates(part_number: str) -> List[Candidate]:
    # Wolf Automation, Motion Industries, and Allied Electronics search
    # endpoints are robots.txt-disallowed (verified live 2026-06-11) and
    # we respect robots — their direct search candidates are gone. The
    # domains stay in _DISTRIBUTOR_DOMAINS so SERP-organic product-detail
    # URLs on them (often robots-allowed) still classify and extract.
    pn = quote_plus(part_number.strip())
    out: List[Candidate] = [
        Candidate(
            url=f"https://www.galco.com/search/default.aspx?query={pn}",
            source_type="distributor",
            source_name="Galco",
        ),
        # Newark's search endpoint was dropped 2026-06-12: it timed out
        # on 100% of observed requests (15s each, on every miss) and
        # never produced a single hit — Newark is electronics-focused
        # and barely carries industrial drives/motors. The domain stays
        # in _DISTRIBUTOR_DOMAINS for SERP-result classification.
        Candidate(
            url=f"https://www.grainger.com/search?searchQuery={pn}",
            source_type="distributor",
            source_name="Grainger",
        ),
        # Electromate (motion-control distributor: AMC, Galil, Maxon,
        # Applied Motion, Copley accessories) serves product pages at
        # the lowercase part-number slug with the price in static HTML
        # (data-price-amount + $-text; JSON-LD sku is their internal id
        # so the SKU guard falls through to regex — correct on a PDP).
        # Unknown parts 404 cleanly.
        Candidate(
            url=f"https://www.electromate.com/{quote_plus(part_number.strip().lower())}",
            source_type="distributor",
            source_name="Electromate",
        ),
    ]
    # Kyklo-backed distributor storefronts — direct product pages. When the
    # part isn't carried, the store 302s to root and the fetcher treats
    # that as a miss.
    for host, name in _KYKLO_DISTRIBUTORS:
        out.append(
            Candidate(
                url=f"https://{host}/products/{pn}",
                source_type="distributor",
                source_name=name,
            )
        )
    return out


# ── Tier 3: aggregators ─────────────────────────────────────────────
#
# Radwell and PLC Center search endpoints are robots.txt-disallowed
# (verified live 2026-06-11), so there are no direct aggregator search
# candidates anymore. _AGGREGATOR_DOMAINS remains as a classification
# allowlist: SERP-organic product-detail URLs on those domains are
# still fetched (the fetcher robots-checks the final URL) and tagged
# with the aggregator tier.


# ── Tier 4: Serper SERP fallback ────────────────────────────────────

_SERPER_ENDPOINT = "https://google.serper.dev/search"


def _known_domains() -> List[str]:
    return list(_OEM_DOMAINS) + list(_DISTRIBUTOR_DOMAINS) + list(_AGGREGATOR_DOMAINS)


def serp_candidates(
    manufacturer: str,
    part_number: str,
    api_key: Optional[str] = None,
    max_results: int = 5,
    timeout_s: float = 10.0,
) -> List[Candidate]:
    """Query Serper.dev, constrained to our known domains. Returns organic
    results whose netloc lands in a tier — tagged with that tier.

    Falls through quietly with ``[]`` when no key is configured so the
    cascade keeps working without SERP.
    """
    key = api_key or os.environ.get("SERPER_API_KEY")
    if not key:
        logger.debug("SERPER_API_KEY not set — skipping SERP fallback")
        return []

    sites = " OR ".join(f"site:{d}" for d in _known_domains())
    query = f'"{manufacturer}" "{part_number}" price ({sites})'
    try:
        resp = httpx.post(
            _SERPER_ENDPOINT,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        logger.info("Serper query failed for %s %s: %s", manufacturer, part_number, e)
        return []

    results: List[Candidate] = []
    for item in data.get("organic", [])[:max_results]:
        url = item.get("link") or ""
        if not url:
            continue
        host = urlparse(url).netloc
        st = source_type_for_domain(host)
        if st is None:
            # Serper should respect site: filters but belt-and-suspenders.
            continue
        results.append(
            Candidate(
                url=url, source_type="serp", source_name=source_name_for_domain(host)
            )
        )
    return results


# ── Public entrypoint ───────────────────────────────────────────────


def resolve_candidates(
    manufacturer: str,
    part_number: str,
    use_serp: bool = True,
    serper_api_key: Optional[str] = None,
) -> List[Candidate]:
    """Return ordered candidate URLs across all tiers.

    Ordering: OEM direct → distributor search → SERP. (Aggregator search
    endpoints were retired 2026-06-11 — robots.txt-disallowed; aggregator
    pages now arrive only via SERP-organic product URLs.)
    The caller walks in order and stops at the first successful price.
    """
    if not manufacturer or not part_number:
        return []

    out: List[Candidate] = []
    out.extend(_oem_candidates(manufacturer, part_number))
    out.extend(_distributor_candidates(part_number))
    if use_serp:
        out.extend(serp_candidates(manufacturer, part_number, api_key=serper_api_key))

    # De-duplicate URLs while preserving order.
    seen: set[str] = set()
    deduped: List[Candidate] = []
    for c in out:
        if c.url in seen:
            continue
        seen.add(c.url)
        deduped.append(c)
    return deduped


def iter_source_domains() -> Iterable[str]:
    """All domains we accept prices from. Exposed for tests."""
    yield from _OEM_DOMAINS
    yield from _DISTRIBUTOR_DOMAINS
    yield from _AGGREGATOR_DOMAINS
