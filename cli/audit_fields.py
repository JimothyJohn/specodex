"""AUDIT-FIELDS — per-product-type field coverage report.

Scans a stage's DynamoDB products table and tabulates, for every Pydantic
model field, the fraction of rows of that product_type that have a
populated value. Flags fields below ``--threshold`` as removal candidates
(declared in the model but rarely or never populated by the extractor).

Two artifacts per run, mirroring `audit_dedupes`:

- ``outputs/audit_fields_<stage>_<ts>.json`` — full per-type field stats.
- ``outputs/audit_fields_<stage>_<ts>.md`` — human-readable report,
  one section per product_type, fields sorted by hit rate ascending,
  with a "removal candidates" callout per type.

Usage:
    uv run python -m cli.audit_fields --stage dev
    uv run python -m cli.audit_fields --stage prod --threshold 0.10
    uv run python -m cli.audit_fields --rows fixture.json  # offline

Read-only — never writes to DynamoDB.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from specodex.config import SCHEMA_CHOICES

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs"

log = logging.getLogger("audit_fields")

# Bookkeeping fields that aren't part of the spec — excluded from the
# hit-rate report. Mirrors NON_SPEC_FIELDS in audit_dedupes.py but kept
# local since the two audits answer different questions.
BOOKKEEPING_FIELDS = frozenset(
    {
        "PK",
        "SK",
        "id",
        "_id",
        "product_id",
        "type",  # legacy duplicate of product_type
        "created_at",
        "updated_at",
        "ingested_at",
        "extraction_id",
    }
)


def _is_populated(value: Any) -> bool:
    """Treat None / empty containers / empty ValueUnit / empty MinMaxUnit as unpopulated.

    The LLM frequently emits ``{"value": null, "unit": "A"}`` when it sees
    the column header but no row value — that's a unit guess, not a real
    measurement, and it should NOT count as a hit. Same for
    ``{"min": null, "max": null, "unit": "..."}``.
    """
    if value is None:
        return False
    if value == "" or value == [] or value == {}:
        return False
    if isinstance(value, dict):
        # ValueUnit: {value, unit}. MinMaxUnit: {min, max, unit}.
        if "value" in value and "unit" in value and len(value) <= 3:
            return value.get("value") is not None
        if "min" in value and "max" in value and "unit" in value:
            return value.get("min") is not None or value.get("max") is not None
    return True


def _populated_keys(row: dict) -> set[str]:
    return {
        k
        for k, v in row.items()
        if k not in BOOKKEEPING_FIELDS and not k.startswith("_") and _is_populated(v)
    }


def audit(rows: Iterable[dict]) -> dict[str, Any]:
    """Group rows by product_type and compute per-field hit-rate stats.

    Returns a dict keyed by product_type::

        {
          "<type>": {
            "row_count": int,
            "declared_fields": [str, ...],     # from Pydantic model
            "hits": {field: int, ...},         # populated counts
            "rate": {field: float, ...},       # populated / row_count
            "undeclared_fields_present": {field: int, ...},  # drift signal
          },
          ...
        }
    """
    by_type: dict[str, list[dict]] = {}
    for row in rows:
        # Skip non-product rows (INGEST# log entries co-tenant in the same
        # table). They carry their own `product_type` column, which would
        # otherwise contaminate the per-type field-coverage stats.
        pk = row.get("PK") or ""
        if pk and not pk.startswith("PRODUCT#"):
            continue
        ptype = row.get("product_type") or row.get("type")
        if not ptype:
            continue
        by_type.setdefault(str(ptype), []).append(row)

    report: dict[str, Any] = {}
    for ptype, type_rows in sorted(by_type.items()):
        model = SCHEMA_CHOICES.get(ptype)
        declared = (
            sorted(f for f in model.model_fields if f not in BOOKKEEPING_FIELDS)
            if model is not None
            else []
        )

        # Count hits across the union of (declared fields, fields actually
        # seen in rows). A declared field with zero hits is still in the
        # report — that's the whole point.
        all_fields: set[str] = set(declared)
        for r in type_rows:
            all_fields |= _populated_keys(r)

        hits: dict[str, int] = {f: 0 for f in all_fields}
        for r in type_rows:
            for k in _populated_keys(r):
                if k in hits:
                    hits[k] += 1

        n = len(type_rows) or 1
        rate = {f: hits[f] / n for f in all_fields}

        declared_set = set(declared)
        undeclared = {
            f: hits[f] for f in all_fields if f not in declared_set and hits[f] > 0
        }

        report[ptype] = {
            "row_count": len(type_rows),
            "declared_fields": declared,
            "hits": hits,
            "rate": rate,
            "undeclared_fields_present": undeclared,
            "model_known": model is not None,
        }
    return report


def render_md(report: dict[str, Any], threshold: float) -> str:
    """Per-product-type markdown report. Fields sorted by hit rate ascending."""
    lines: list[str] = [
        "# Field-coverage audit",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Threshold for removal-candidate flag: **{threshold:.0%}**",
        "",
    ]
    if not report:
        lines.append("No rows with a `product_type` found.")
        return "\n".join(lines) + "\n"

    # Top-level summary
    lines.append("## Summary")
    lines.append("")
    lines.append(
        "| product_type | rows | declared fields | always-empty | < threshold |"
    )
    lines.append("|---|---:|---:|---:|---:|")
    for ptype, t in report.items():
        n = t["row_count"]
        declared = t["declared_fields"]
        rate = t["rate"]
        always_empty = sum(1 for f in declared if rate.get(f, 0) == 0)
        below = sum(1 for f in declared if 0 < rate.get(f, 0) < threshold)
        marker = "" if t.get("model_known") else " ⚠"
        lines.append(
            f"| `{ptype}`{marker} | {n} | {len(declared)} | {always_empty} | {below} |"
        )
    lines.append("")

    # Per-type detail
    for ptype, t in report.items():
        n = t["row_count"]
        declared = t["declared_fields"]
        hits = t["hits"]
        rate = t["rate"]
        undeclared = t["undeclared_fields_present"]

        marker = "" if t.get("model_known") else " — ⚠ no Pydantic model registered"
        lines.append(f"## `{ptype}`{marker}")
        lines.append("")
        lines.append(f"- Rows: **{n}**")
        lines.append(f"- Declared fields: **{len(declared)}**")
        if not t.get("model_known"):
            lines.append(
                "- Skipping declared-vs-actual diff — no model in SCHEMA_CHOICES."
            )

        if declared:
            removal_candidates = [f for f in declared if rate.get(f, 0) < threshold]
            removal_candidates.sort(key=lambda f: (rate.get(f, 0), f))
            if removal_candidates:
                lines.append("")
                lines.append(f"### Removal candidates (hit rate < {threshold:.0%})")
                lines.append("")
                lines.append("| field | hits | rate |")
                lines.append("|---|---:|---:|")
                for f in removal_candidates:
                    lines.append(f"| `{f}` | {hits.get(f, 0)} | {rate.get(f, 0):.1%} |")
            else:
                lines.append("- All declared fields above threshold ✅")

            lines.append("")
            lines.append("### All declared fields (asc by hit rate)")
            lines.append("")
            lines.append("| field | hits | rate |")
            lines.append("|---|---:|---:|")
            for f in sorted(declared, key=lambda f: (rate.get(f, 0), f)):
                lines.append(f"| `{f}` | {hits.get(f, 0)} | {rate.get(f, 0):.1%} |")

        if undeclared:
            lines.append("")
            lines.append("### Undeclared fields present in DB (model drift)")
            lines.append("")
            lines.append("| field | hits | rate |")
            lines.append("|---|---:|---:|")
            for f in sorted(undeclared, key=lambda f: (-undeclared[f], f)):
                lines.append(f"| `{f}` | {hits.get(f, 0)} | {rate.get(f, 0):.1%} |")

        lines.append("")
    return "\n".join(lines) + "\n"


def fetch_rows_from_dynamo(table_name: str) -> list[dict]:
    """Scan products table, return raw items as dicts (Decimal → native)."""
    import boto3  # type: ignore

    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    items: list[dict] = []
    kwargs: dict[str, Any] = {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    log.info("Scanned %s items from %s", len(items), table_name)
    return [_decimal_to_native(r) for r in items]


def _decimal_to_native(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        as_int = int(obj)
        return as_int if as_int == obj else float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_native(v) for v in obj]
    return obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="audit_fields", description=__doc__)
    parser.add_argument(
        "--stage",
        choices=["dev", "staging", "prod"],
        default="dev",
        help="Stage to scan. Default: dev (most-populated table). Read-only.",
    )
    parser.add_argument(
        "--table", help="Override table name (default: products-<stage>)"
    )
    parser.add_argument(
        "--rows",
        type=Path,
        help="Read rows from a JSON list of dicts instead of DynamoDB.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Hit-rate threshold for removal-candidate flag (default: 0.05 = 5%%).",
    )
    parser.add_argument("--output", type=Path, help="JSON output path.")
    parser.add_argument("--md-output", type=Path, help="Markdown output path.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.rows:
        raw = json.loads(args.rows.read_text())
        if not isinstance(raw, list):
            print("--rows must be a JSON list of dicts", file=sys.stderr)
            return 2
        rows = raw
    else:
        table = args.table or f"products-{args.stage}"
        rows = fetch_rows_from_dynamo(table)

    report = audit(rows)
    log.info(
        "Built report for %s product_types (%s rows total)",
        len(report),
        sum(t["row_count"] for t in report.values()),
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output or OUTPUT_DIR / f"audit_fields_{args.stage}_{ts}.json"
    md_path = args.md_output or OUTPUT_DIR / f"audit_fields_{args.stage}_{ts}.md"

    json_path.write_text(
        json.dumps(
            {
                "stage": args.stage,
                "timestamp": ts,
                "threshold": args.threshold,
                "report": report,
            },
            indent=2,
            default=str,
        )
    )
    md_path.write_text(render_md(report, args.threshold))

    log.info("Wrote: %s", json_path)
    log.info("Wrote: %s", md_path)
    print(md_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
