"""Audit stored motor rows for physically implausible specs.

Read-only. Two findings per row:

1. ``power_balance`` — ``spec_rules.plausibility_flags``: rated torque ×
   rated speed disagrees with rated power by more than the calibrated
   band (the kW↔W / mNm↔Nm slip signature). New ingests are held back on
   this check; rows written before it shipped are what this finds.
2. ``noncanonical_unit`` — a stored ``{value, unit}`` whose unit is not
   the canonical one (``mN-m``, ``RPM``, ``min-1``...). The Pydantic
   validators canonicalise on ingest now, but older rows predate that,
   and the frontend and search API compare raw numbers, so a stored
   ``42 mN-m`` filters as 42 Nm.

Deliberately NOT a range check on rated power alone: the catalog really
does hold 0.37 W Bodine subfractional gearmotors and 5 MW WEG W60s, and
both pass the physics check.

Usage:
    ./Quickstart admin audit-specs                 # scan the configured table
    ./Quickstart admin audit-specs -o out.jsonl    # write findings to a file

Exits 0 when nothing is found, 1 otherwise. Fixing rows is a separate,
reviewed step: every finding carries its datasheet_url so the value can
be checked against the source before anything is rewritten.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable, Iterator

from specodex.models.motor import Motor
from specodex.spec_rules import plausibility_flags, power_balance_ratio

# Canonical unit per audited motor field (what specodex.units emits).
CANONICAL_UNITS: dict[str, str] = {
    "rated_power": "W",
    "rated_torque": "Nm",
    "peak_torque": "Nm",
    "rated_speed": "rpm",
    "max_speed": "rpm",
}


def _noncanonical_units(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for field, canonical in CANONICAL_UNITS.items():
        raw = item.get(field)
        if isinstance(raw, dict):
            unit = raw.get("unit")
            if isinstance(unit, str) and unit != canonical:
                out.append(f"{field}: stored unit {unit!r}, canonical {canonical!r}")
    return out


def audit_items(items: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield one finding dict per raw motor item that has any finding."""
    for item in items:
        findings: list[dict[str, Any]] = []
        for msg in _noncanonical_units(item):
            findings.append({"check": "noncanonical_unit", "detail": msg})
        try:
            model = Motor.model_validate(item, strict=False)
        except Exception as exc:
            model = None
            findings.append(
                {"check": "model_invalid", "detail": str(exc).splitlines()[0][:300]}
            )
        if model is not None:
            for msg in plausibility_flags(model):
                findings.append(
                    {
                        "check": "power_balance",
                        "ratio": power_balance_ratio(model),
                        "detail": msg,
                    }
                )
        if findings:
            yield {
                "product_id": str(item.get("product_id", "")),
                "manufacturer": item.get("manufacturer"),
                "part_number": item.get("part_number"),
                "datasheet_url": item.get("datasheet_url"),
                "findings": findings,
            }


def _iter_motor_items(table: Any) -> Iterator[dict[str, Any]]:
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": "PK = :pk",
        "ExpressionAttributeValues": {":pk": "PRODUCT#MOTOR"},
    }
    while True:
        resp = table.query(**kwargs)
        yield from resp.get("Items", [])
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return
        kwargs["ExclusiveStartKey"] = lek


def audit(table_name: str, region: str, out_path: str | None) -> int:
    import boto3  # type: ignore

    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    counts: dict[str, int] = {}
    rows = 0
    writer = open(out_path, "w", encoding="utf-8") if out_path else None
    try:
        for finding in audit_items(_iter_motor_items(table)):
            rows += 1
            for f in finding["findings"]:
                counts[f["check"]] = counts.get(f["check"], 0) + 1
            line = json.dumps(finding, default=str)
            if writer:
                writer.write(line + "\n")
            else:
                print(line)
    finally:
        if writer:
            writer.close()
    print(
        f"audit-specs: {rows} motor row(s) with findings {counts or '{}'}",
        file=sys.stderr,
    )
    return 1 if rows else 0
