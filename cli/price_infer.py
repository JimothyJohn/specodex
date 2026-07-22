"""Infer price estimates for unpriced products from DB comparables.

todo/COMMERCIAL.md Phase 2. For each product of the given type(s) that
has no listed ``msrp`` and no existing ``price_estimate``, runs the
deterministic price-comps-v1 engine (``specodex.pricing.inference``)
against the priced rows of the same type and prints a per-estimate
table (part number, estimate, p25-p75 range, method, comparable count)
so an operator can eyeball the reasoning before writing anything.

Dry-run is the default; ``--apply`` writes the resulting
``price_estimate`` (a SourcedFigure with its comparable citations)
back onto the row. The listed ``msrp`` is never touched — an estimate
must never masquerade as a listed price.

Stages: dev (default) and staging only. Writes to prod are out of
scope for this tool, deliberately — the stage flag resolves the table
name as ``products-<stage>`` unless ``DYNAMODB_TABLE_NAME`` overrides.

Usage:

    ./Quickstart price-infer                          # dry-run, dev, all types
    ./Quickstart price-infer --type motor --limit 20
    ./Quickstart price-infer --type drive --apply
    ./Quickstart price-infer --stage staging --type motor --apply
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Type

from specodex.config import SCHEMA_CHOICES
from specodex.db.dynamo import DynamoDBClient
from specodex.models.product import ProductBase
from specodex.pricing.inference import estimate_price

logger = logging.getLogger(__name__)

PRODUCT_CLASSES: dict[str, Type[ProductBase]] = dict(SCHEMA_CHOICES)

# Writes to prod are out of scope — dev/staging only, on purpose.
ALLOWED_STAGES = ("dev", "staging")


def resolve_table_name(stage: str) -> str:
    """DYNAMODB_TABLE_NAME env wins; otherwise the stage convention.

    Mirrors cli/quickstart.py's deploy resolution (``products-<stage>``).
    """
    return os.environ.get("DYNAMODB_TABLE_NAME") or f"products-{stage}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class RunStats:
    scanned: int = 0
    priced_pool: int = 0
    skipped_has_estimate: int = 0
    targets: int = 0
    estimated: int = 0
    written: int = 0
    failures: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"scanned={self.scanned} priced_pool={self.priced_pool} "
            f"had_estimate={self.skipped_has_estimate} targets={self.targets} "
            f"estimated={self.estimated} written={self.written} "
            f"failures={len(self.failures)}"
        )


_HEADER = (
    f"{'part_number':<28} {'estimate':>12} {'p25–p75':>23} {'method':<30} {'comps':>5}"
)


def _print_estimate_row(product: ProductBase, figure) -> None:
    label = product.part_number or product.product_name or str(product.product_id)
    citation = figure.sources[0]
    rng = "—"
    if figure.observed_range is not None:
        rng = f"${figure.observed_range.min:,.2f}–${figure.observed_range.max:,.2f}"
    print(
        f"{label:<28} ~${figure.value.value:>10,.2f} {rng:>23} "
        f"{citation.method_version:<30} {len(citation.comparable_ids):>5}"
    )


def run(
    *,
    product_types: Sequence[str],
    apply: bool,
    limit: Optional[int] = None,
    refresh: bool = False,
    client: DynamoDBClient,
    retrieved_at: Optional[str] = None,
) -> RunStats:
    """Compute (and optionally write) price estimates. Returns stats.

    ``retrieved_at`` is stamped into every citation; one timestamp per
    run, taken once up front, so a run is internally consistent and a
    test can inject a fixed value.
    """
    retrieved_at = retrieved_at or _now_iso()
    stats = RunStats()
    printed_header = False

    for product_type in product_types:
        model_cls = PRODUCT_CLASSES.get(product_type)
        if model_cls is None:
            raise ValueError(f"unknown product_type {product_type!r}")

        rows = client.list(model_cls, limit=None)
        stats.scanned += len(rows)
        stats.priced_pool += sum(1 for r in rows if r.msrp is not None)

        # Targets: unpriced rows without an existing estimate (--refresh
        # recomputes rows that already carry one). Sorted by product_id
        # so the printed table order is deterministic too.
        targets: List[ProductBase] = []
        for row in rows:
            if row.msrp is not None:
                continue
            if row.price_estimate is not None and not refresh:
                stats.skipped_has_estimate += 1
                continue
            targets.append(row)
        targets.sort(key=lambda r: str(r.product_id))

        for target in targets:
            if limit is not None and stats.targets >= limit:
                break
            stats.targets += 1
            figure = estimate_price(target, rows, retrieved_at=retrieved_at)
            if figure is None:
                logger.debug(
                    "no estimate for %s / %s (gates)",
                    target.manufacturer,
                    target.part_number,
                )
                continue
            stats.estimated += 1
            if not printed_header:
                print(_HEADER)
                print("-" * len(_HEADER))
                printed_header = True
            _print_estimate_row(target, figure)
            if not apply:
                continue
            target.price_estimate = figure
            if client.update(target):
                stats.written += 1
            else:
                stats.failures.append(f"write-failed:{target.product_id}")

    mode = "APPLY" if apply else "DRY-RUN"
    logger.info("[%s] %s", mode, stats.summary())
    if stats.failures:
        logger.warning("Failures (%d): %s", len(stats.failures), stats.failures[:10])
    return stats


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="price-infer",
        description=(
            "Deterministic price estimates from DB comparables "
            "(price-comps-v1: same-family log-log ladder, KNN fallback). "
            "Dry-run by default."
        ),
    )
    parser.add_argument(
        "--stage",
        default="dev",
        choices=ALLOWED_STAGES,
        help=(
            "Target stage (default: dev). Resolves the table as "
            "products-<stage> unless DYNAMODB_TABLE_NAME is set. "
            "prod is deliberately not accepted — estimate writes to "
            "prod are out of scope for this tool."
        ),
    )
    parser.add_argument(
        "--type",
        dest="product_type",
        default=None,
        choices=sorted(PRODUCT_CLASSES),
        help="Product type to process (default: all types)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max targets to process across the run (default: all)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute + print estimates without writing (the default)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Write price_estimate onto each estimated row",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Recompute rows that already carry a price_estimate "
        "(default only fills empties)",
    )
    args = parser.parse_args()

    product_types = (
        [args.product_type] if args.product_type else sorted(PRODUCT_CLASSES)
    )
    client = DynamoDBClient(table_name=resolve_table_name(args.stage))
    stats = run(
        product_types=product_types,
        apply=args.apply,
        limit=args.limit,
        refresh=args.refresh,
        client=client,
    )
    sys.exit(0 if not stats.failures else 1)


if __name__ == "__main__":
    main()
