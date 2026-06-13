"""Backfill stock ``availability`` on existing products from distributor JSON-LD.

This is the honest answer to "populate lead time": there is no public
numeric per-part lead time (verified 2026-06-12 — 0 of 577 crawled
pages carried one; Electromate's "lead time" label is the boilerplate
"call us for exact delivery times"). What distributor pages DO carry,
in ~23% of cases, is schema.org ItemAvailability (InStock / BackOrder /
…) in the same JSON-LD offer the price comes from.

So this populates ``availability`` (+ source URL + timestamp) — a
per-seller, point-in-time stock snapshot, stored honestly as what it
is rather than laundered into a fake "N days" lead time.

It reuses the price crawl's fetch cache (``outputs/price_cache/``), so
running it right after ``price-enrich`` mostly hits cache instead of
re-fetching. Only the OEM + distributor tiers are tried — those are the
ones whose product pages carry JSON-LD offers; SERP/shopping don't.

Usage:

    ./Quickstart availability-enrich --product-type drive --dry-run --limit 20
    ./Quickstart availability-enrich --product-type motor --filter Mitsubishi
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Type

from specodex.config import SCHEMA_CHOICES
from specodex.db.dynamo import DynamoDBClient
from specodex.models.product import ProductBase
from specodex.pricing.extract import extract_availability
from specodex.pricing.fetch import PriceFetcher
from specodex.pricing.resolver import resolve_candidates

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "outputs" / "price_cache"

PRODUCT_CLASSES: dict[str, Type[ProductBase]] = dict(SCHEMA_CHOICES)

# Only these tiers serve product pages with JSON-LD offers.
_AVAILABILITY_TIERS = frozenset({"oem", "distributor", "aggregator"})

_TEMPLATED_PN_CHARS = frozenset("*?#")


def _is_templated(pn: str) -> bool:
    return any(c in _TEMPLATED_PN_CHARS for c in pn)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class RunStats:
    scanned: int = 0
    skipped_no_pn: int = 0
    skipped_templated: int = 0
    skipped_has_availability: int = 0
    extracted: int = 0
    written: int = 0
    failures: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"scanned={self.scanned} had_availability={self.skipped_has_availability} "
            f"no_pn={self.skipped_no_pn} templated={self.skipped_templated} "
            f"extracted={self.extracted} written={self.written} "
            f"failures={len(self.failures)}"
        )


def _process(
    product: ProductBase,
    fetcher: PriceFetcher,
    client: DynamoDBClient,
    dry_run: bool,
    stats: RunStats,
) -> None:
    pn = product.part_number
    if not pn:
        stats.skipped_no_pn += 1
        return
    if _is_templated(pn):
        stats.skipped_templated += 1
        return

    candidates = [
        c
        for c in resolve_candidates(product.manufacturer, pn, use_serp=False)
        if c.source_type in _AVAILABILITY_TIERS
    ]
    for candidate in candidates:
        result = fetcher.fetch(candidate.url)
        if result is None:
            continue
        status = extract_availability(result.html, result.url, pn)
        if status is None:
            continue
        stats.extracted += 1
        logger.info(
            "hit  %s / %s → %s via %s",
            product.manufacturer,
            pn,
            status,
            candidate.source_name,
        )
        if dry_run:
            return
        product.availability = status
        product.availability_source_url = candidate.url
        product.availability_fetched_at = _now_iso()
        if client.update(product):
            stats.written += 1
        else:
            stats.failures.append(f"write-failed:{product.product_id}")
        return
    logger.debug(
        "miss %s / %s (%d candidates)", product.manufacturer, pn, len(candidates)
    )


def run(
    product_type: str,
    limit: Optional[int],
    filter_str: Optional[str],
    dry_run: bool,
    refresh: bool,
    rate_limit_s: float,
) -> int:
    model_cls = PRODUCT_CLASSES.get(product_type)
    if model_cls is None:
        raise ValueError(f"unknown product_type {product_type!r}")

    client = DynamoDBClient()
    stats = RunStats()
    filtered: List[ProductBase] = []
    for p in client.list(model_cls, limit=None):
        stats.scanned += 1
        # ``refresh`` re-checks products that already have a status
        # (availability goes stale); default only fills empties.
        if not refresh and p.availability is not None:
            stats.skipped_has_availability += 1
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
        "Scanning %d %ss; %d to check%s",
        stats.scanned,
        product_type,
        len(filtered),
        f" (filter: {filter_str!r})" if filter_str else "",
    )

    started = time.monotonic()
    with PriceFetcher(cache_dir=CACHE_DIR, rate_limit_s=rate_limit_s) as fetcher:
        for p in filtered:
            _process(p, fetcher, client, dry_run, stats)

    logger.info("Done in %.1fs — %s", time.monotonic() - started, stats.summary())
    if stats.failures:
        logger.warning("Failures (%d): %s", len(stats.failures), stats.failures[:10])
    return 0 if not stats.failures else 1


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="availability-enrich",
        description="Backfill schema.org stock availability from distributor pages.",
    )
    parser.add_argument(
        "--product-type", default="drive", choices=sorted(PRODUCT_CLASSES)
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--filter", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-check products that already have an availability status "
        "(it goes stale); default only fills empties.",
    )
    parser.add_argument("--rate-limit", type=float, default=1.0)
    args = parser.parse_args()

    sys.exit(
        run(
            product_type=args.product_type,
            limit=args.limit,
            filter_str=args.filter,
            dry_run=args.dry_run,
            refresh=args.refresh,
            rate_limit_s=args.rate_limit,
        )
    )


if __name__ == "__main__":
    main()
