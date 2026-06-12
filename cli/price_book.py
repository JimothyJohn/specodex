"""Ingest a public price book (XLSX or PDF) and backfill ``msrp``.

One price book covers hundreds-to-thousands of part numbers in a single
run — the bulk path of todo/PRICING.md Phase 1. The join is enrich-only:
products that already carry an ``msrp`` are never touched.

Usage:

    # Dry-run first — prints the join table, writes nothing:
    ./Quickstart price-book https://.../501_Index.xlsx \\
        --manufacturer baldor --dry-run

    # Apply:
    ./Quickstart price-book outputs/pricebooks/501_Index.xlsx \\
        --manufacturer baldor --source-url "https://search.abb.com/..."

    # PDF book, explicit columns for a quirky XLSX:
    ./Quickstart price-book weg-price-book.pdf --manufacturer weg
    ./Quickstart price-book book.xlsx --manufacturer dart \\
        --pn-column "Model" --price-column "List"

``--manufacturer`` is a case-insensitive substring filter on the DB
``manufacturer`` field (same semantics as price-enrich ``--filter``) —
it narrows the join so identical part numbers across vendors can't
cross-pollinate. The join key itself is the normalized part number.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import httpx

from specodex.db.dynamo import DynamoDBClient
from specodex.models.product import ProductBase
from specodex.pricing.pricebook import (
    JoinMatch,
    PricePair,
    join_pairs,
    pairs_from_pdf,
    pairs_from_xlsx,
)

logger = logging.getLogger(__name__)

USER_AGENT = "Specodex/1.0 (+https://www.specodex.com; contact: nick@advin.io)"


def _load_source(source: str) -> tuple[bytes, str]:
    """Return (bytes, source_url). Local paths record an empty URL unless
    overridden with --source-url."""
    if source.startswith(("http://", "https://")):
        resp = httpx.get(
            source,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.content, str(resp.url)
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"price book not found: {source}")
    return path.read_bytes(), ""


def _detect_format(data: bytes) -> str:
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:2] == b"PK":
        return "xlsx"
    raise ValueError("unrecognized price-book format — expected XLSX or PDF")


def _matching_products(
    client: DynamoDBClient, manufacturer_filter: str
) -> List[ProductBase]:
    needle = manufacturer_filter.lower()
    return [
        p
        for p in client.list_all(limit=None)
        if needle in (p.manufacturer or "").lower()
    ]


def _print_join_table(matches: List[JoinMatch]) -> None:
    print(f"\n{'part_number':<28} {'price':>10}  {'type':<12} product")
    print("-" * 78)
    for m in matches:
        print(
            f"{(m.product.part_number or ''):<28} "
            f"${m.pair.price_usd:>9} "
            f" {m.product.product_type:<12} {m.product.product_name[:30]}"
        )
    print("-" * 78)


def run(
    source: str,
    manufacturer: str,
    dry_run: bool,
    source_url_override: str | None,
    pn_column: str | None,
    price_column: str | None,
    max_llm_pages: int,
) -> int:
    data, source_url = _load_source(source)
    if source_url_override:
        source_url = source_url_override
    fmt = _detect_format(data)
    logger.info("loaded %s price book (%d bytes)", fmt, len(data))

    pairs: List[PricePair]
    if fmt == "xlsx":
        pairs = pairs_from_xlsx(data, pn_header=pn_column, price_header=price_column)
    else:
        pairs = pairs_from_pdf(
            data,
            api_key=os.environ.get("GEMINI_API_KEY"),
            max_pages=max_llm_pages,
        )
    if not pairs:
        logger.error("no price pairs extracted from the book — nothing to join")
        return 1

    client = DynamoDBClient()
    products = _matching_products(client, manufacturer)
    unpriced = [p for p in products if p.msrp is None and p.part_number]
    matches = join_pairs(pairs, products)

    logger.info(
        "book pairs=%d | DB '%s' products=%d (unpriced w/ part_number=%d) | "
        "matched=%d (%.1f%% of unpriced)",
        len(pairs),
        manufacturer,
        len(products),
        len(unpriced),
        len(matches),
        100 * len(matches) / len(unpriced) if unpriced else 0.0,
    )
    if not matches:
        return 1
    _print_join_table(matches)

    if dry_run:
        logger.info("dry-run — no writes")
        return 0

    if not source_url:
        logger.error(
            "refusing to write without a source URL — pass --source-url when "
            "ingesting a local file (msrp_source_url is part of the contract)"
        )
        return 1

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    written = failed = 0
    for m in matches:
        m.product.msrp = f"{m.pair.price_usd};USD"
        m.product.msrp_source_url = source_url
        m.product.msrp_fetched_at = fetched_at
        if client.update(m.product):
            written += 1
        else:
            failed += 1
            logger.warning("write failed for %s", m.product.product_id)
    logger.info("written=%d failed=%d", written, failed)
    return 0 if failed == 0 else 1


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="price-book",
        description="Backfill msrp from a public price book (XLSX or PDF).",
    )
    parser.add_argument("source", help="Path or URL of the price book")
    parser.add_argument(
        "--manufacturer",
        required=True,
        help="Case-insensitive substring filter on the DB manufacturer field",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract + join + print the table, write nothing",
    )
    parser.add_argument(
        "--source-url",
        default=None,
        help="URL recorded as msrp_source_url (required for local files on write)",
    )
    parser.add_argument(
        "--pn-column",
        default=None,
        help="Exact header of the part-number column (XLSX; default: auto-detect)",
    )
    parser.add_argument(
        "--price-column",
        default=None,
        help="Exact header of the price column (XLSX; default: auto-detect)",
    )
    parser.add_argument(
        "--max-llm-pages",
        type=int,
        default=40,
        help="Cap on per-page Gemini calls for PDF books (default 40)",
    )
    args = parser.parse_args()

    rc = run(
        source=args.source,
        manufacturer=args.manufacturer,
        dry_run=args.dry_run,
        source_url_override=args.source_url,
        pn_column=args.pn_column,
        price_column=args.price_column,
        max_llm_pages=args.max_llm_pages,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
