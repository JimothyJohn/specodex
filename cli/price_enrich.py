"""Backfill MSRP on existing products by crawling OEM + distributor pages.

For each product of the given type that has ``msrp is None`` and a valid
``part_number``, walks the resolver cascade (OEM → distributor → aggregator →
SERP) until it finds a price in the $10-$100K band, writes the USD value plus
the source URL and fetch timestamp back to DynamoDB.

Usage:

    ./Quickstart price-enrich --product-type drive --dry-run --limit 10
    ./Quickstart price-enrich --product-type motor --limit 50
    ./Quickstart price-enrich --product-type drive --filter "Mitsubishi"

Budgets:
    --max-llm-calls 20   hard-cap on LLM fallback invocations per run
    --max-serp-calls 100 hard-cap on SERP queries per run

Cost guardrails: LLM fallback uses gemini-2.5-flash; each call is cheap
but a 500-product run could be expensive at scale. The default caps are
tuned for a single-digit-dollar overnight backfill.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Type

from specodex.config import SCHEMA_CHOICES
from specodex.db.dynamo import DynamoDBClient
from specodex.models.product import ProductBase
from specodex.pricing.extract import classify_url, extract_price
from specodex.pricing.fetch import PriceFetcher
from specodex.pricing.resolver import (
    Candidate,
    resolve_candidates,
    serp_candidates,
)
from specodex.pricing.shopping import shopping_price

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "outputs" / "price_cache"

# Every concrete product model is enrichable — auto-discovered, same
# registry the extraction pipeline uses (was a hand-list of drive+motor,
# which left gearhead/robot_arm/electric_cylinder/linear_actuator at 0%).
PRODUCT_CLASSES: dict[str, Type[ProductBase]] = dict(SCHEMA_CHOICES)

# Part numbers with option placeholders ("21G11*F960JNONNNNN") are
# catalog templates, not orderable SKUs — no store will ever match them.
_TEMPLATED_PN_CHARS = frozenset("*?#")


def is_templated_part_number(pn: str) -> bool:
    """True when the part number is a catalog option-template, not a SKU."""
    return any(ch in _TEMPLATED_PN_CHARS for ch in pn)


@dataclass
class RunStats:
    scanned: int = 0
    skipped_no_pn: int = 0
    skipped_templated_pn: int = 0
    skipped_has_price: int = 0
    resolved: int = 0
    fetched: int = 0
    extracted: int = 0
    written: int = 0
    llm_calls: int = 0
    serp_calls: int = 0
    shopping_calls: int = 0
    shopping_hits: int = 0
    failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"scanned={self.scanned} priced_already={self.skipped_has_price} "
            f"no_pn={self.skipped_no_pn} templated_pn={self.skipped_templated_pn} "
            f"extracted={self.extracted} "
            f"written={self.written} llm={self.llm_calls} serp={self.serp_calls} "
            f"shopping={self.shopping_hits}/{self.shopping_calls} "
            f"failures={len(self.failures)}"
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _iter_candidates(
    manufacturer: str,
    part_number: str,
    use_serp: bool,
    serp_budget_remaining: int,
    stats: RunStats,
    tiers: Optional[frozenset[str]] = None,
) -> List[Candidate]:
    """Resolve candidate URLs, deferring SERP queries until after the
    deterministic tiers so we don't burn SERP budget on products that the
    distributor URLs already cover.

    ``tiers`` restricts which source tiers produce candidates (e.g.
    ``{"oem"}`` for a vendor whose official store answers definitively —
    the 2026-06-12 Mitsubishi pilot showed distributor fallbacks never
    rescue an OEM-store miss but cost ~40s per product walking dead
    candidates). ``None`` means all tiers.
    """
    direct = resolve_candidates(manufacturer, part_number, use_serp=False)
    if tiers is not None:
        direct = [c for c in direct if c.source_type in tiers]
    if not use_serp or serp_budget_remaining <= 0:
        return direct
    if tiers is not None and "serp" not in tiers:
        return direct
    if not os.environ.get("SERPER_API_KEY"):
        # serp_candidates no-ops without a key — don't burn the budget
        # counter on queries that never happened.
        return direct
    stats.serp_calls += 1
    serp = serp_candidates(manufacturer, part_number)
    return direct + serp


def _try_candidates(
    product: ProductBase,
    fetcher: PriceFetcher,
    candidates: List[Candidate],
    allow_llm: bool,
    llm_budget_remaining: int,
    stats: RunStats,
) -> Optional[tuple[Decimal, Candidate, str]]:
    """Walk candidates in order. Return first successful price along with
    the candidate + extractor name.
    """
    mfg = product.manufacturer
    pn = product.part_number or ""
    used_llm = 0
    for candidate in candidates:
        result = fetcher.fetch(candidate.url)
        if result is None:
            continue
        stats.fetched += 1

        can_llm = allow_llm and (used_llm + stats.llm_calls) < llm_budget_remaining
        extracted = extract_price(
            html=result.html,
            url=result.url,
            manufacturer=mfg,
            part_number=pn,
            allow_llm=can_llm,
        )
        if extracted is None:
            continue
        price, extractor = extracted
        if extractor == "llm":
            used_llm += 1
            stats.llm_calls += 1

        # If we followed a redirect, re-classify by the final URL's domain.
        resolved_tier = classify_url(result.url)
        effective = candidate
        if resolved_tier is not None and resolved_tier != candidate.source_type:
            # Serper-sourced candidates inherit the domain's tier.
            if candidate.source_type == "serp":
                effective = Candidate(
                    url=result.url,
                    source_type=resolved_tier,
                    source_name=candidate.source_name,
                )
            else:
                effective = Candidate(
                    url=result.url,
                    source_type=candidate.source_type,
                    source_name=candidate.source_name,
                )

        return price, effective, extractor
    return None


def _process_product(
    product: ProductBase,
    fetcher: PriceFetcher,
    client: DynamoDBClient,
    dry_run: bool,
    use_serp: bool,
    allow_llm: bool,
    llm_budget: int,
    serp_budget: int,
    stats: RunStats,
    tiers: Optional[frozenset[str]] = None,
    shopping_budget: int = 0,
) -> None:
    pn = product.part_number
    if not pn:
        stats.skipped_no_pn += 1
        logger.info(
            "skip %s/%s — no part_number", product.manufacturer, product.product_name
        )
        return
    if is_templated_part_number(pn):
        stats.skipped_templated_pn += 1
        logger.info(
            "skip %s/%s — templated part number (option placeholder)",
            product.manufacturer,
            pn,
        )
        return

    candidates = _iter_candidates(
        manufacturer=product.manufacturer,
        part_number=pn,
        use_serp=use_serp,
        serp_budget_remaining=serp_budget - stats.serp_calls,
        stats=stats,
        tiers=tiers,
    )
    if not candidates and shopping_budget <= 0:
        stats.failures.append(f"no-candidates:{product.manufacturer}:{pn}")
        return
    stats.resolved += 1

    found = (
        _try_candidates(
            product=product,
            fetcher=fetcher,
            candidates=candidates,
            allow_llm=allow_llm,
            llm_budget_remaining=llm_budget,
            stats=stats,
        )
        if candidates
        else None
    )
    if found is None and stats.shopping_calls < shopping_budget:
        # Shopping tier: one structured query, no fetching. Street
        # price, median over trusted offers (specodex.pricing.shopping).
        stats.shopping_calls += 1
        shopped = shopping_price(product.manufacturer, pn)
        if shopped is not None:
            stats.shopping_hits += 1
            found = (
                shopped.price_usd,
                Candidate(
                    url=shopped.source_url,
                    source_type="serp",
                    source_name=f"Google Shopping ({shopped.source_name})",
                ),
                "shopping",
            )
    if found is None:
        logger.info(
            "miss %s / %s (%s candidates tried)",
            product.manufacturer,
            pn,
            len(candidates),
        )
        return

    price, candidate, extractor = found
    stats.extracted += 1

    logger.info(
        "hit  %s / %s → $%s via %s (%s, %s)",
        product.manufacturer,
        pn,
        price,
        candidate.source_name,
        candidate.source_type,
        extractor,
    )

    if dry_run:
        return

    # Enrich: only fill when msrp is still None. specodex.web_scraper._merge_products
    # semantics — do not overwrite populated fields.
    if product.msrp is not None:
        logger.info("skip write — product %s already has msrp", product.product_id)
        return

    product.msrp = f"{price};USD"
    product.msrp_source_url = candidate.url
    product.msrp_fetched_at = _now_iso()

    ok = client.update(product)
    if ok:
        stats.written += 1
    else:
        stats.failures.append(f"write-failed:{product.product_id}")


def run(
    product_type: str,
    limit: Optional[int],
    filter_str: Optional[str],
    dry_run: bool,
    use_serp: bool,
    allow_llm: bool,
    max_llm_calls: int,
    max_serp_calls: int,
    tiers: Optional[frozenset[str]] = None,
    max_shopping_calls: int = 0,
    rate_limit_s: float = 1.0,
) -> int:
    model_cls = PRODUCT_CLASSES.get(product_type)
    if model_cls is None:
        raise ValueError(
            f"product_type {product_type!r} not supported for price enrichment "
            f"(v1 covers {sorted(PRODUCT_CLASSES)})"
        )

    client = DynamoDBClient()
    products = client.list(model_cls, limit=None)
    stats = RunStats()

    # Filter: only those missing msrp + optional substring match on manufacturer/name.
    filtered: List[ProductBase] = []
    for p in products:
        stats.scanned += 1
        if p.msrp is not None:
            stats.skipped_has_price += 1
            continue
        if filter_str:
            hay = " ".join(
                [p.manufacturer or "", p.product_name or "", p.part_number or ""]
            ).lower()
            if filter_str.lower() not in hay:
                continue
        filtered.append(p)

    if limit is not None:
        filtered = filtered[:limit]

    logger.info(
        "Scanning %d %ss; %d need msrp%s",
        stats.scanned,
        product_type,
        len(filtered),
        f" (filter: {filter_str!r})" if filter_str else "",
    )

    started = time.monotonic()
    with PriceFetcher(cache_dir=CACHE_DIR, rate_limit_s=rate_limit_s) as fetcher:
        for p in filtered:
            _process_product(
                product=p,
                fetcher=fetcher,
                client=client,
                dry_run=dry_run,
                use_serp=use_serp,
                allow_llm=allow_llm,
                llm_budget=max_llm_calls,
                serp_budget=max_serp_calls,
                stats=stats,
                tiers=tiers,
                shopping_budget=max_shopping_calls,
            )

    elapsed = time.monotonic() - started
    logger.info("Done in %.1fs — %s", elapsed, stats.summary())
    if stats.failures:
        logger.warning("Failures (%d): %s", len(stats.failures), stats.failures[:10])
    return 0 if not stats.failures else 1


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="price-enrich",
        description="Backfill MSRP prices on existing products via web scraping.",
    )
    parser.add_argument(
        "--product-type",
        default="drive",
        choices=sorted(PRODUCT_CLASSES),
        help="Product type to enrich (default: drive)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max products to process (default: all with missing msrp)",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Case-insensitive substring filter on manufacturer/name/part_number",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve + fetch + extract but do not write to DynamoDB",
    )
    parser.add_argument(
        "--no-serp",
        action="store_true",
        help="Disable Serper SERP fallback (direct-URL only)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM last-resort extraction",
    )
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=20,
        help="Hard cap on LLM fallback invocations per run (default 20)",
    )
    parser.add_argument(
        "--max-serp-calls",
        type=int,
        default=100,
        help="Hard cap on Serper SERP queries per run (default 100)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=1.0,
        help=(
            "Seconds between requests per domain (default 1.0). Raise it "
            "when a store 429s — shop1.us.mitsubishielectric.com needs "
            "~2.5s (observed 2026-06-12)."
        ),
    )
    parser.add_argument(
        "--max-shopping-calls",
        type=int,
        default=0,
        help=(
            "Hard cap on Serper /shopping queries per run (default 0 = "
            "disabled). Tried only after all direct candidates miss. "
            "Costs real money per query; returns a street-price median "
            "over trusted offers."
        ),
    )
    parser.add_argument(
        "--tiers",
        default=None,
        help=(
            "Comma-separated source tiers to try (oem,distributor,serp). "
            "Default: all. Use --tiers oem for vendors whose official "
            "store answers definitively — misses stop ~20x faster."
        ),
    )

    args = parser.parse_args()

    tiers: Optional[frozenset[str]] = None
    if args.tiers:
        tiers = frozenset(t.strip() for t in args.tiers.split(",") if t.strip())
        valid = {"oem", "distributor", "aggregator", "serp"}
        if not tiers <= valid:
            parser.error(f"unknown tier(s): {sorted(tiers - valid)}")

    rc = run(
        product_type=args.product_type,
        limit=args.limit,
        filter_str=args.filter,
        dry_run=args.dry_run,
        use_serp=not args.no_serp,
        allow_llm=not args.no_llm,
        max_llm_calls=args.max_llm_calls,
        max_serp_calls=args.max_serp_calls,
        tiers=tiers,
        max_shopping_calls=args.max_shopping_calls,
        rate_limit_s=args.rate_limit,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
