"""Audit DynamoDB for malformed value-unit strings.

Two corruption patterns are detected:

1. Multi-semicolon strings like "1;2;V" — `_parse_compact_units` in
   specodex/db/dynamo.py uses a greedy `(.*)` capture for the unit
   portion, so these read back as {value=1, unit="2;V"} instead of falling
   through to the passthrough path.

2. Non-numeric stem strings like "G-Series-230;V" or "IP65;kgcm²" — these
   have exactly one semicolon so the reader regex rejects them cleanly and
   returns the raw string, but the UI then renders the raw string verbatim.
   Root cause is LLM field-shuffling (series or IP rating dumped into a
   numeric ValueUnit/MinMaxUnit field).

The writer-side `validate_value_unit_str` / `validate_min_max_unit_str`
invariants in models/common.py reject pattern #1 at validation time but
still accept pattern #2 (range part only has to be non-empty). Existing
rows predating the invariants need to be audited.

Usage:
    ./Quickstart admin audit-units                 # scan the configured table
    ./Quickstart admin audit-units -o out.jsonl    # write findings to a file
    ./Quickstart admin audit-units --table foo     # override table name

Read-only. Exits 0 when nothing dirty is found, non-zero otherwise.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import Any, Iterator

import boto3  # type: ignore

from specodex.config import REGION, TABLE_NAME


# A signed decimal (optionally with scientific-notation exponent) or a
# range of two such decimals. The audit's false-positive floor: values like
# "1.98E-6" and "2.59e-4" must pass, while "G-Series-230" and "IP65" must not.
# Note: _parse_compact_units's own regex is stricter (no scientific notation),
# but scientific notation values *are* produced by the writer path —
# `float(0.00000198)` stringifies as "1.98e-06". Flagging them would drown
# the real corruption in noise.
_NUM = r"-?(?:\d+\.?\d*|\.\d+)(?:[eE]-?\d+)?"
_NUMERIC_STEM_RE = re.compile(rf"^{_NUM}(?:-{_NUM})?$")


def _iter_all_items(table: Any) -> Iterator[dict[str, Any]]:
    """Yield every item from a DynamoDB table via paginated scan."""
    scan_kwargs: dict[str, Any] = {}
    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            yield item
        lek = response.get("LastEvaluatedKey")
        if not lek:
            return
        scan_kwargs["ExclusiveStartKey"] = lek


def _classify_unit_string(s: str) -> str | None:
    """Return a reason string if `s` is a malformed compact-unit value, else None.

    - "1;2;V"            -> "multi-semicolon" (greedy-regex misparse)
    - "G-Series-230;V"   -> "non-numeric-stem" (field-shuffled)
    - "230;V"            -> None (clean)
    - "plain string"     -> None (no semicolon — not a compact-unit value)
    """
    if ";" not in s:
        return None
    if s.count(";") >= 2:
        return "multi-semicolon"
    stem, unit = s.split(";", 1)
    stem = stem.strip().replace(" to ", "-")
    if not stem or not unit:
        return "empty-part"
    if not _NUMERIC_STEM_RE.match(stem):
        return "non-numeric-stem"
    return None


def _find_dirty_strings(
    item: dict[str, Any], prefix: str = ""
) -> list[tuple[str, str, str]]:
    """Walk the item recursively and return (path, value, reason) for any
    malformed compact-unit string. `reason` is one of the _classify_unit_string
    return values."""
    found: list[tuple[str, str, str]] = []
    if isinstance(item, dict):
        for k, v in item.items():
            path = f"{prefix}.{k}" if prefix else k
            found.extend(_find_dirty_strings(v, path))
    elif isinstance(item, list):
        for i, v in enumerate(item):
            found.extend(_find_dirty_strings(v, f"{prefix}[{i}]"))
    elif isinstance(item, str):
        reason = _classify_unit_string(item)
        if reason is not None:
            found.append((prefix, item, reason))
    return found


def audit(table_name: str, region: str, out_path: str | None) -> int:
    """Scan the table and report any fields whose value has more than one ';'.

    Returns the process exit code: 0 if clean, 1 if dirty rows were found.
    """
    ddb = boto3.resource("dynamodb", region_name=region)
    table = ddb.Table(table_name)

    dirty_count = 0
    scanned = 0
    reason_counts: dict[str, int] = {}
    writer = open(out_path, "w") if out_path else None

    try:
        for item in _iter_all_items(table):
            scanned += 1
            findings = _find_dirty_strings(item)
            if not findings:
                continue
            dirty_count += 1
            pk = item.get("PK", "<no PK>")
            sk = item.get("SK", "<no SK>")
            report = {
                "PK": pk,
                "SK": sk,
                "fields": [
                    {"path": p, "value": v, "reason": r} for p, v, r in findings
                ],
            }
            for _, _, r in findings:
                reason_counts[r] = reason_counts.get(r, 0) + 1
            line = json.dumps(report, default=str)
            if writer:
                writer.write(line + "\n")
            else:
                print(line)
    finally:
        if writer:
            writer.close()

    breakdown = (
        ", ".join(f"{r}={n}" for r, n in sorted(reason_counts.items())) or "none"
    )
    print(
        f"audit-units: scanned {scanned} items, {dirty_count} dirty ({breakdown})",
        file=sys.stderr,
    )
    return 0 if dirty_count == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit-units",
        description=(
            "Scan DynamoDB for 'value;unit' strings with more than one ';' — "
            "the shape that _parse_compact_units misparses."
        ),
    )
    parser.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", TABLE_NAME),
        help="DynamoDB table to scan (default: $DYNAMODB_TABLE_NAME or config)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", REGION),
        help="AWS region (default: $AWS_REGION or config)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Write findings as JSONL to this path (default: stdout)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    return audit(args.table, args.region, args.output)


if __name__ == "__main__":
    sys.exit(main())
