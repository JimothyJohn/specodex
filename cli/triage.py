"""
Triage low-quality products — find products with significant N/A in their
spec fields and copy the corresponding datasheets to triage/ in S3.

Usage:
    python -m cli.triage [--threshold 0.5] [--bucket BUCKET] [--dry-run]
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from specodex.aws import s3_client, upload_bucket
from specodex.config import SCHEMA_CHOICES
from specodex.models.product import ProductBase
from specodex.quality import score_product, spec_fields_for_model

log = logging.getLogger("triage")


def _resolve_bucket(bucket: str | None = None) -> str:
    return upload_bucket(bucket)


_get_s3 = s3_client


def _get_dynamo():
    from specodex.db.dynamo import DynamoDBClient

    return DynamoDBClient()


def _s3_key_from_url(url: str) -> tuple[str, str] | None:
    """Parse an s3:// URL into (bucket, key). Returns None for non-S3 URLs."""
    if not url.startswith("s3://"):
        return None
    parsed = urlparse(url)
    return parsed.netloc, parsed.path.lstrip("/")


def _copy_to_triage(s3, source_bucket: str, source_key: str, dest_bucket: str) -> str:
    """Copy an S3 object to the triage/ prefix. Returns the destination key."""
    filename = source_key.rsplit("/", 1)[-1]
    dest_key = f"triage/{filename}"
    s3.copy_object(
        Bucket=dest_bucket,
        CopySource={"Bucket": source_bucket, "Key": source_key},
        Key=dest_key,
    )
    return dest_key


def find_triage_candidates(
    threshold: float = 0.5,
) -> list[tuple[ProductBase, float, int, int, list[str]]]:
    """Scan all products and return those with quality score below threshold.

    Args:
        threshold: Products with quality score below this are flagged.
                   0.5 means more than half the spec fields are N/A.

    Returns:
        List of (product, score, filled, total, missing_fields) tuples.
    """
    dynamo = _get_dynamo()
    candidates: list[tuple[ProductBase, float, int, int, list[str]]] = []

    for product_type, model_class in SCHEMA_CHOICES.items():
        spec_count = len(spec_fields_for_model(model_class))
        if spec_count == 0:
            continue

        log.info("Scanning %s products (%d spec fields)...", product_type, spec_count)
        products = dynamo.list(model_class)

        for product in products:
            score, filled, total, missing = score_product(product)
            if score < threshold:
                candidates.append((product, score, filled, total, missing))
                log.info(
                    "Triage candidate: %s (%s) — %d/%d fields (%.0f%%)",
                    product.product_name,
                    product.manufacturer,
                    filled,
                    total,
                    score * 100,
                )

    log.info(
        "Found %d triage candidates below %.0f%% threshold",
        len(candidates),
        threshold * 100,
    )
    return candidates


def triage_datasheets(
    threshold: float = 0.5,
    bucket: str | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Find low-quality products and copy their datasheets to triage/.

    Args:
        threshold: Quality score below which a product is triaged (default 0.5).
        bucket: S3 bucket override. Resolved from env if not provided.
        dry_run: If True, log what would happen without copying.

    Returns:
        List of dicts with product info and triage results.
    """
    dest_bucket = _resolve_bucket(bucket)
    s3 = _get_s3()
    candidates = find_triage_candidates(threshold)
    results: list[dict] = []

    for product, score, filled, total, missing in candidates:
        entry = {
            "product_name": product.product_name,
            "manufacturer": product.manufacturer,
            "product_type": product.product_type,
            "score": score,
            "filled": filled,
            "total": total,
            "missing_fields": missing,
            "datasheet_url": product.datasheet_url,
            "triaged": False,
        }

        if not product.datasheet_url:
            log.warning(
                "No datasheet_url for %s (%s) — cannot triage",
                product.product_name,
                product.manufacturer,
            )
            entry["reason"] = "no_datasheet_url"
            results.append(entry)
            continue

        parsed = _s3_key_from_url(product.datasheet_url)
        if parsed:
            source_bucket, source_key = parsed
            if dry_run:
                log.info(
                    "[DRY RUN] Would copy s3://%s/%s -> s3://%s/triage/",
                    source_bucket,
                    source_key,
                    dest_bucket,
                )
                entry["triaged"] = False
                entry["reason"] = "dry_run"
            else:
                try:
                    dest_key = _copy_to_triage(
                        s3, source_bucket, source_key, dest_bucket
                    )
                    log.info("Copied %s -> %s", source_key, dest_key)
                    entry["triaged"] = True
                    entry["dest_key"] = dest_key
                except Exception as e:
                    log.error("Failed to copy %s: %s", source_key, e)
                    entry["reason"] = f"copy_failed: {e}"
        else:
            # External URL — try to find a matching PDF in done/ or raw_pdfs/
            matched_key = _find_pdf_by_url(s3, dest_bucket, product.datasheet_url)
            if matched_key:
                if dry_run:
                    log.info(
                        "[DRY RUN] Would copy s3://%s/%s -> s3://%s/triage/",
                        dest_bucket,
                        matched_key,
                        dest_bucket,
                    )
                    entry["reason"] = "dry_run"
                else:
                    try:
                        dest_key = _copy_to_triage(
                            s3, dest_bucket, matched_key, dest_bucket
                        )
                        log.info("Copied %s -> %s", matched_key, dest_key)
                        entry["triaged"] = True
                        entry["dest_key"] = dest_key
                    except Exception as e:
                        log.error("Failed to copy %s: %s", matched_key, e)
                        entry["reason"] = f"copy_failed: {e}"
            else:
                log.warning(
                    "External URL for %s (%s): %s — no matching S3 object found",
                    product.product_name,
                    product.manufacturer,
                    product.datasheet_url,
                )
                entry["reason"] = "external_url_no_s3_match"

        results.append(entry)

    triaged_count = sum(1 for r in results if r["triaged"])
    log.info(
        "Triage complete: %d/%d datasheets copied to s3://%s/triage/",
        triaged_count,
        len(results),
        dest_bucket,
    )
    return results


def _find_pdf_by_url(s3, bucket: str, url: str) -> str | None:
    """Try to match an external datasheet URL to a PDF already in done/ or raw_pdfs/.

    Heuristic: extract the filename from the URL and search S3 prefixes.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    filename = path.rsplit("/", 1)[-1] if "/" in path else path
    if not filename.lower().endswith(".pdf"):
        return None

    # Normalize: strip query params, lowercase for matching
    filename_lower = filename.lower()

    for prefix in ("done/", "raw_pdfs/"):
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                key_filename = key.rsplit("/", 1)[-1].lower()
                if key_filename == filename_lower:
                    return key

    return None


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Triage low-quality product datasheets"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Quality score threshold (0.0-1.0). Products below this are triaged. Default: 0.5",
    )
    parser.add_argument("--bucket", help="S3 bucket override")
    parser.add_argument(
        "--dry-run", action="store_true", help="Log actions without copying"
    )
    args = parser.parse_args()

    results = triage_datasheets(
        threshold=args.threshold,
        bucket=args.bucket,
        dry_run=args.dry_run,
    )

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Triage Summary (threshold: {args.threshold:.0%})")
    print(f"{'=' * 60}")
    for r in results:
        status = "TRIAGED" if r["triaged"] else r.get("reason", "skipped")
        print(
            f"  [{status:>30}] {r['manufacturer']} / {r['product_name']} — {r['score']:.0%}"
        )
    print(f"{'=' * 60}")
    print(
        f"Total: {len(results)} candidates, {sum(1 for r in results if r['triaged'])} triaged"
    )


if __name__ == "__main__":
    main()
