"""Ingest-log record shape and key helpers.

One log record per call to ``scraper.process_datasheet``. Keyed in the
products table by ``PK = INGEST#<sha256(url)[:16]>`` with an ISO-timestamp
SK so re-runs append instead of overwrite. The scraper uses the latest
record to short-circuit already-processed URLs; the ``ingest-report``
CLI groups quality-fails by manufacturer for vendor outreach.

See ``todo/ENRICH.md`` for the design.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 1

# Status values. Keep in sync with the status table in ENRICH.md.
STATUS_SUCCESS = "success"
STATUS_QUALITY_FAIL = "quality_fail"
STATUS_EXTRACT_FAIL = "extract_fail"
STATUS_SKIPPED_DUP = "skipped_dup"

VALID_STATUSES = frozenset(
    {STATUS_SUCCESS, STATUS_QUALITY_FAIL, STATUS_EXTRACT_FAIL, STATUS_SKIPPED_DUP}
)

# A quality_fail whose average fill ratio is at least this high isn't
# worth re-spending tokens on — the manufacturer genuinely doesn't
# publish the missing specs. Below this, a retry after a schema or
# prompt fix might do better. Starts at the quality gate floor.
MIN_RETRY_THRESHOLD = 0.25


def url_hash(url: str) -> str:
    """Deterministic 16-char hex key fragment from a URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def pk_for_url(url: str) -> str:
    return f"INGEST#{url_hash(url)}"


def sk_now() -> str:
    # ISO-8601 UTC with trailing Z; DynamoDB sorts these lexicographically
    # in the same order as chronologically, which is what we need for
    # `ScanIndexForward=False, Limit=1` to return the newest attempt.
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"INGEST#{ts}"


def build_record(
    *,
    url: str,
    manufacturer: str,
    product_type: str,
    status: str,
    product_name_hint: Optional[str] = None,
    product_family_hint: Optional[str] = None,
    products_extracted: int = 0,
    products_written: int = 0,
    fields_total: int = 0,
    fields_filled_avg: float = 0.0,
    fields_missing: Optional[Iterable[str]] = None,
    pages_detected: int = 0,
    pages_used: Optional[Iterable[int]] = None,
    page_finder_method: Optional[str] = None,
    extracted_part_numbers: Optional[Iterable[str]] = None,
    gemini_input_tokens: Optional[int] = None,
    gemini_output_tokens: Optional[int] = None,
    error_message: Optional[str] = None,
    sk: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the DynamoDB item for a single ingest attempt.

    Only required fields are positional-required; the rest default to
    sensible zeros/None so the extract_fail path (which knows very
    little) can write a minimal record.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")

    record: dict[str, Any] = {
        "PK": pk_for_url(url),
        "SK": sk or sk_now(),
        "url": url,
        "manufacturer": manufacturer,
        "product_type": product_type,
        "status": status,
        "products_extracted": int(products_extracted),
        "products_written": int(products_written),
        "fields_total": int(fields_total),
        "fields_filled_avg": float(fields_filled_avg),
        "fields_missing": sorted(set(fields_missing or [])),
        "pages_detected": int(pages_detected),
        "pages_used": list(pages_used or []),
        "extracted_part_numbers": list(extracted_part_numbers or []),
        "schema_version": SCHEMA_VERSION,
    }
    if product_name_hint is not None:
        record["product_name_hint"] = product_name_hint
    if product_family_hint is not None:
        record["product_family_hint"] = product_family_hint
    if page_finder_method is not None:
        record["page_finder_method"] = page_finder_method
    if gemini_input_tokens is not None:
        record["gemini_input_tokens"] = int(gemini_input_tokens)
    if gemini_output_tokens is not None:
        record["gemini_output_tokens"] = int(gemini_output_tokens)
    if error_message is not None:
        record["error_message"] = error_message
    return record


def should_skip(last: Optional[dict[str, Any]]) -> bool:
    """Return True if a prior attempt means we shouldn't re-run.

    Skip when:
    - last attempt succeeded, OR
    - last attempt was a quality_fail whose fill ratio is at or above
      MIN_RETRY_THRESHOLD (re-running won't help without a schema fix)
    """
    if not last:
        return False
    status = last.get("status")
    if status == STATUS_SUCCESS:
        return True
    if status == STATUS_QUALITY_FAIL:
        try:
            total = int(last.get("fields_total", 0) or 0)
            filled = float(last.get("fields_filled_avg", 0) or 0)
        except (TypeError, ValueError):
            # Malformed prior record (non-numeric string in either
            # numeric slot) — fall through to retry rather than crash
            # the scraper on the first read of the bad row.
            return False
        if total <= 0:
            return False
        return (filled / total) >= MIN_RETRY_THRESHOLD
    # extract_fail, skipped_dup, or unknown → worth a re-attempt.
    return False
