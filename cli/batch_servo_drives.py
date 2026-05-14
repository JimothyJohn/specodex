"""
Batch ingest of servo / stepper drive datasheets into DynamoDB.

Drives a list of (manufacturer, product_family, product_name, url) tuples
through `specodex.scraper.process_datasheet` — the same path the S3 queue
processor uses, but fed from a checked-in JSON targets file instead of
S3. Writes to whichever DynamoDB table `DYNAMODB_TABLE_NAME` points at
(defaults to dev via `.env`).

Targets file shape:

    {
      "manufacturer": "Copley Controls",
      "targets": [
        {
          "slug": "accelnet-plus-panel-bpl",
          "product_family": "Accelnet Plus Panel",
          "product_name": "BPL",
          "url": "https://.../assets/<uuid>",
          "pages": [3, 4, 5]            # optional; omit for auto page_finder
        },
        ...
      ]
    }

`manufacturer` at the top level applies to every entry unless an entry
overrides it. Any extra keys per entry (e.g. `bus`, `notes`) are kept in
the per-catalog sidecar but ignored by the extractor.

Usage:

    ./Quickstart batch-drives                                  Run the default Copley list
    ./Quickstart batch-drives --targets cli/data/foo.json      Different vendor list
    ./Quickstart batch-drives --limit 3                        Pilot first 3
    ./Quickstart batch-drives --only slug1,slug2               Specific slugs
    ./Quickstart batch-drives --manufacturer "Copley Controls" Filter mixed lists by mfg
    ./Quickstart batch-drives --dry-run                        Print plan, no LLM/DB calls
    ./Quickstart batch-drives --force                          Re-extract even if product exists

Per-catalog JSON sidecar lands in `outputs/drives/<slug>.json`. Per-run
report (slug → status, duration_s, error) lands in
`outputs/batch_servo_drives_report.json`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Imported under TYPE_CHECKING so the runtime cost of pulling in
    # boto3 + the Gemini client only happens inside _run_one when an
    # actual run starts. The string forward-ref at the function
    # signature picks up this import for type-checkers + ruff.
    from specodex.db.dynamo import DynamoDBClient

log = logging.getLogger("batch_servo_drives")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGETS = ROOT / "cli" / "data" / "copley_drives.json"
OUTPUT_DIR = ROOT / "outputs" / "drives"
REPORT_PATH = ROOT / "outputs" / "batch_servo_drives_report.json"


def _load_targets(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    """Parse a targets JSON file.

    Returns (default_manufacturer, targets_list). The default mfg is the
    top-level `manufacturer` key (or None); each target may override it.
    """
    with path.open() as fh:
        data = json.load(fh)
    default_mfg = data.get("manufacturer")
    targets = list(data.get("targets") or [])
    return default_mfg, targets


def _filter(
    targets: list[dict[str, Any]],
    *,
    default_mfg: str | None,
    only: list[str] | None,
    manufacturer: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    out = []
    for t in targets:
        mfg = t.get("manufacturer") or default_mfg
        if manufacturer and (mfg or "").lower() != manufacturer.lower():
            continue
        if only and t.get("slug") not in only:
            continue
        out.append({**t, "_manufacturer": mfg})
    if limit is not None:
        out = out[:limit]
    return out


def _run_one(
    client: DynamoDBClient,
    api_key: str,
    target: dict[str, Any],
    *,
    force: bool,
) -> dict[str, Any]:
    from specodex.scraper import process_datasheet  # local import for speed

    slug = target["slug"]
    started = time.monotonic()
    output_path = OUTPUT_DIR / f"{slug}.json"
    record = {
        "slug": slug,
        "manufacturer": target["_manufacturer"],
        "product_family": target.get("product_family"),
        "product_name": target.get("product_name"),
        "url": target["url"],
        "status": "unknown",
        "duration_s": 0.0,
        "error": None,
    }
    try:
        status = process_datasheet(
            client=client,
            api_key=api_key,
            product_type="drive",
            manufacturer=target["_manufacturer"],
            product_name=target.get("product_name")
            or target.get("product_family")
            or slug,
            product_family=target.get("product_family") or "",
            url=target["url"],
            pages=target.get("pages"),
            output_path=output_path,
            force=force,
        )
        record["status"] = status
    except Exception as exc:  # noqa: BLE001 — we want every failure to land in the report
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        log.exception("Extraction failed for %s", slug)
    finally:
        record["duration_s"] = round(time.monotonic() - started, 2)
    return record


def _ensure_api_key() -> str:
    """Read GEMINI_API_KEY from env or exit with an error.

    Kept separate from _get_table_name so CodeQL's flow analysis
    doesn't taint the table name as sensitive-data-from-env-key. A
    prior shared `_ensure_env() -> tuple[str, str]` raised 3 false-
    positive 'clear-text logging of sensitive information' alerts on
    code that only logged the table name.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print(
            "error: GEMINI_API_KEY is not set. Put it in .env at the repo root.",
            file=sys.stderr,
        )
        sys.exit(2)
    return api_key


def _get_table_name() -> str:
    """Return the configured DynamoDB table. Non-sensitive."""
    from specodex.config import TABLE_NAME  # local — config reads .env on import

    return TABLE_NAME


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="batch-drives",
        description="Batch-ingest servo drive datasheets into DynamoDB.",
    )
    parser.add_argument(
        "--targets",
        type=Path,
        default=DEFAULT_TARGETS,
        help=f"Path to JSON targets file (default: {DEFAULT_TARGETS.relative_to(ROOT)})",
    )
    parser.add_argument(
        "--manufacturer",
        help="Only run targets matching this manufacturer (case-insensitive)",
    )
    parser.add_argument(
        "--only",
        help="Comma-separated slug list to run (other entries skipped)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Stop after this many targets (after --only/--manufacturer filter)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even if a row with the same product_id exists in DynamoDB",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the filtered target list and exit without extracting",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="Python logging level (default: INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.targets.exists():
        print(f"error: targets file not found: {args.targets}", file=sys.stderr)
        return 2

    default_mfg, raw_targets = _load_targets(args.targets)
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    targets = _filter(
        raw_targets,
        default_mfg=default_mfg,
        only=only,
        manufacturer=args.manufacturer,
        limit=args.limit,
    )

    if not targets:
        print("error: no targets matched the filter", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"Would extract {len(targets)} datasheet(s):")
        for t in targets:
            print(f"  {t['slug']:40s} {t['_manufacturer']:20s} {t['url']}")
        return 0

    api_key = _ensure_api_key()
    table = _get_table_name()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Import lazily so --dry-run + --help don't trigger the boto3 + Gemini
    # client setup (which is slow and prints a banner on stderr).
    from specodex.db.dynamo import DynamoDBClient

    client = DynamoDBClient(table_name=table)

    started_at = datetime.now(timezone.utc).isoformat()
    log.info(
        "Batch run start: %d targets, table=%s, force=%s",
        len(targets),
        table,
        args.force,
    )

    records: list[dict[str, Any]] = []
    for i, t in enumerate(targets, 1):
        log.info("[%d/%d] %s — %s", i, len(targets), t["slug"], t["url"])
        rec = _run_one(client, api_key, t, force=args.force)
        records.append(rec)
        log.info(
            "[%d/%d] %s -> %s (%.1fs)%s",
            i,
            len(targets),
            t["slug"],
            rec["status"],
            rec["duration_s"],
            f" — {rec['error']}" if rec["error"] else "",
        )

    finished_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "started_at": started_at,
        "finished_at": finished_at,
        "table": table,
        "targets_file": str(args.targets.relative_to(ROOT)),
        "total": len(records),
        "by_status": {},
        "results": records,
    }
    for r in records:
        summary["by_status"][r["status"]] = summary["by_status"].get(r["status"], 0) + 1
    REPORT_PATH.write_text(json.dumps(summary, indent=2) + "\n")

    log.info(
        "Batch run done: %s — report at %s",
        ", ".join(f"{k}={v}" for k, v in summary["by_status"].items()),
        REPORT_PATH.relative_to(ROOT),
    )

    # Exit non-zero if anything failed so CI / pre-deploy hooks can react.
    return 0 if all(r["status"] != "failed" for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
