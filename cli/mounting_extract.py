"""Extract motor mounting-flange dimensions from dimensional-drawing pages
and backfill onto existing ``Motor`` rows.

Every motor's own ``datasheet_url`` is the source PDF — no fuzzy
cross-catalog matching is needed, since the target row(s) are already
known. The mounting-flange dimensions (bolt circle diameter, pilot
diameter, shaft length/modification, ...) live on DIFFERENT pages than
the spec-table pages already cached for that motor, so this re-scans the
FULL PDF with ``specodex.page_finder.find_drawing_pages_scored`` rather
than filtering a prior page list.

Where several ``Motor`` rows share one ``datasheet_url`` (a catalog page
covering multiple frame sizes at once), each extracted variant is
matched back to exactly one candidate row within that URL's group by
``frame_designator`` vs. ``frame_size`` / ``motor_mount_pattern`` — an
ambiguous or absent match is reported, never guessed.

Enrich-only and idempotent: a motor that already carries
``mounting_flange`` is skipped (same "don't overwrite a value the
operator may have hand-edited" convention as
``specodex/admin/motor_mount_backfill.py``). Dry-run by default;
``--apply`` writes, and refuses ``--stage prod`` outright — this is a
Gemini-driven enrichment pass, not the deliberate promote-to-prod path.

Usage:

    # Dry-run (default) — prints the join table, writes nothing:
    ./Quickstart mounting-extract --manufacturer nidec

    # Apply, against dev (default stage):
    ./Quickstart mounting-extract --manufacturer nidec --apply

    # Target specific rows:
    ./Quickstart mounting-extract --product-id <uuid> --product-id <uuid> --apply

    # Against staging:
    ./Quickstart mounting-extract --manufacturer nidec --stage staging --apply
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from specodex.admin.operations import make_client
from specodex.db.dynamo import DynamoDBClient
from specodex.models.motor import Motor
from specodex.mounting.extract import (
    MotorMountingExtraction,
    extract_mounting_dimensions,
)
from specodex.page_finder import find_drawing_pages_scored, pdf_pages_to_images
from specodex.utils import get_document

logger = logging.getLogger(__name__)

STAGES = ("dev", "staging", "prod")


def _matching_motors(
    client: DynamoDBClient,
    manufacturer: Optional[str],
    product_ids: Optional[List[str]],
) -> List[Motor]:
    motors = client.list(Motor)
    if product_ids:
        wanted = set(product_ids)
        motors = [m for m in motors if str(m.product_id) in wanted]
    if manufacturer:
        needle = manufacturer.lower()
        motors = [m for m in motors if needle in (m.manufacturer or "").lower()]
    return motors


def _group_by_datasheet_url(motors: List[Motor]) -> Dict[str, List[Motor]]:
    groups: Dict[str, List[Motor]] = defaultdict(list)
    for m in motors:
        if m.datasheet_url:
            groups[m.datasheet_url].append(m)
    return groups


def _match_extraction_to_motor(
    extraction: MotorMountingExtraction, candidates: List[Motor]
) -> Optional[Motor]:
    """Match one extracted variant to exactly one candidate ``Motor``.

    Candidates are already scoped to motors sharing this extraction's
    source ``datasheet_url``, so this only needs to disambiguate within
    that (typically small) set — not fuzzy-match across a whole catalog.
    Returns ``None`` on no match OR an ambiguous match; never guesses.
    """
    if not extraction.frame_designator:
        return None
    needle = extraction.frame_designator.strip().lower()
    if not needle:
        return None

    hits = [
        m
        for m in candidates
        if needle == (m.frame_size or "").strip().lower()
        or needle == (m.motor_mount_pattern or "").strip().lower()
        or (
            (m.frame_size or "").strip().lower()
            and (m.frame_size or "").strip().lower() in needle
        )
        or (
            (m.motor_mount_pattern or "").strip().lower()
            and (m.motor_mount_pattern or "").strip().lower() in needle
        )
    ]
    # De-dupe (a motor can satisfy more than one clause above).
    unique = list({id(m): m for m in hits}.values())
    if len(unique) == 1:
        return unique[0]
    return None


def _print_report(
    matched: List[Tuple[Motor, MotorMountingExtraction]],
    unmatched: List[Tuple[str, MotorMountingExtraction]],
    fetch_failures: List[Tuple[str, str]],
) -> None:
    print(f"\n{'motor':<32} {'frame':<14} bcd            shaft_dia      mods")
    print("-" * 100)
    for m, ex in matched:
        bcd = ex.mounting_flange.bolt_circle_diameter if ex.mounting_flange else None
        print(
            f"{(m.product_name or '')[:32]:<32} "
            f"{(ex.frame_designator or '')[:14]:<14} "
            f"{str(bcd):<14} "
            f"{str(ex.shaft_diameter):<14} "
            f"{ex.shaft_modification or []}"
        )
    print("-" * 100)
    print(
        f"matched={len(matched)} unmatched={len(unmatched)} fetch_failures={len(fetch_failures)}"
    )

    if unmatched:
        print("\nUNMATCHED extractions (no single candidate motor — review manually):")
        for url, ex in unmatched:
            print(f"  {url}  frame={ex.frame_designator!r}  page={ex.page_number}")

    if fetch_failures:
        print("\nFETCH FAILURES:")
        for url, reason in fetch_failures:
            print(f"  {url}: {reason}")


def run(
    manufacturer: Optional[str],
    product_ids: Optional[List[str]],
    stage: str,
    apply: bool,
    max_pages: Optional[int],
) -> int:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not set")
        return 1
    if apply and stage == "prod":
        logger.error(
            "refusing --apply against --stage prod — this is a Gemini-driven "
            "enrichment pass, not the deliberate promote-to-prod path"
        )
        return 1

    client = make_client(stage)
    motors = _matching_motors(client, manufacturer, product_ids)
    already_set = [m for m in motors if m.mounting_flange is not None]
    candidates = [m for m in motors if m.mounting_flange is None and m.datasheet_url]

    logger.info(
        "motors matched=%d | already have mounting_flange=%d (skipped) | "
        "no datasheet_url=%d | remaining candidates=%d",
        len(motors),
        len(already_set),
        len(motors) - len(already_set) - len(candidates),
        len(candidates),
    )
    if not candidates:
        logger.info("nothing to extract — no candidates")
        return 0

    groups = _group_by_datasheet_url(candidates)
    logger.info(
        "candidates=%d across %d distinct datasheet URLs", len(candidates), len(groups)
    )

    matched: List[Tuple[Motor, MotorMountingExtraction]] = []
    unmatched: List[Tuple[str, MotorMountingExtraction]] = []
    fetch_failures: List[Tuple[str, str]] = []

    for url, group in groups.items():
        try:
            pdf_bytes = get_document(url)
        except Exception as e:  # noqa: BLE001 — network/parse failures vary widely
            fetch_failures.append((url, str(e)))
            continue
        if not pdf_bytes:
            fetch_failures.append((url, "empty or failed download"))
            continue

        pages, _ = find_drawing_pages_scored(pdf_bytes, max_pages=max_pages)
        if not pages:
            logger.info("no candidate drawing pages found for %s", url)
            continue

        all_images = pdf_pages_to_images(pdf_bytes)
        page_numbers = [p for p in pages if p < len(all_images)]
        images = [all_images[p] for p in page_numbers]
        if not images:
            continue

        try:
            extractions = extract_mounting_dimensions(images, page_numbers, api_key)
        except Exception as e:  # noqa: BLE001 — Gemini failures vary widely
            logger.warning("extraction failed for %s: %s", url, e)
            continue

        for extraction in extractions:
            motor = _match_extraction_to_motor(extraction, group)
            if motor is None:
                unmatched.append((url, extraction))
            else:
                matched.append((motor, extraction))

    _print_report(matched, unmatched, fetch_failures)

    if not apply:
        logger.info("dry-run — no writes (pass --apply to write)")
        return 0

    written = failed = 0
    for motor, extraction in matched:
        if extraction.mounting_flange is not None:
            motor.mounting_flange = extraction.mounting_flange
        if motor.shaft_diameter is None and extraction.shaft_diameter is not None:
            motor.shaft_diameter = extraction.shaft_diameter
        if extraction.shaft_length is not None:
            motor.shaft_length = extraction.shaft_length
        if extraction.shaft_modification:
            motor.shaft_modification = extraction.shaft_modification
        if client.update(motor):
            written += 1
        else:
            failed += 1
            logger.warning("write failed for %s", motor.product_id)
    logger.info("written=%d failed=%d", written, failed)
    return 0 if failed == 0 else 1


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="mounting-extract",
        description="Extract motor mounting-flange dimensions from dimensional-drawing pages.",
    )
    parser.add_argument(
        "--manufacturer",
        default=None,
        help="Case-insensitive substring filter on the DB manufacturer field",
    )
    parser.add_argument(
        "--product-id",
        action="append",
        dest="product_ids",
        default=None,
        help="Restrict to specific product_id(s) — repeatable",
    )
    parser.add_argument("--stage", default="dev", choices=STAGES)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write matched extractions to the DB (default: dry-run, print only)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Cap on candidate drawing pages sent to Gemini per PDF (default: adaptive)",
    )
    args = parser.parse_args()

    if not args.manufacturer and not args.product_ids:
        parser.error("pass --manufacturer and/or --product-id to scope the run")

    rc = run(
        manufacturer=args.manufacturer,
        product_ids=args.product_ids,
        stage=args.stage,
        apply=args.apply,
        max_pages=args.max_pages,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
