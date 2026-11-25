"""Migrate stored product envelopes to the typed shape (2026-11-24).

Before: ``dimensions = {"width": 60, "length": 120, "unit": "in"}`` (bare
numbers, one shared unnormalised unit — 40% of dev rows were inches) and
``weight`` in whatever unit the datasheet printed (kg/lb/g/oz).

After: ``dimensions = {"length": {"value", "unit": "mm"}, ...}`` per axis,
``weight`` in kg, and a derived ``max_dimension`` ({value, unit: "mm"}) the
API can sort and filter on.

Only those three attributes are touched, by a conditional ``UpdateItem``
per row. Going through ``DynamoDBClient.update`` would round-trip every
field of the row through today's validators and rewrite them all — a
much bigger blast radius than this migration needs. The condition pins
the old values, so a row a concurrent writer changed between the scan and
the update is skipped and counted, never clobbered.

The transform is ``ProductBase``'s own coercion, so a migrated row is
exactly what a fresh ingest would have written. Idempotent: a second run
plans zero updates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError
from pydantic import TypeAdapter, ValidationError

from specodex.models.product import (
    Dimensions,
    ProductWeight,
    _coerce_dimensions_container,
)

_ATTRS = ("dimensions", "weight", "max_dimension")
_WEIGHT = TypeAdapter(ProductWeight)


def canonical_dimensions(raw: Any) -> Optional[Dict[str, Any]]:
    container = _coerce_dimensions_container(raw)
    if container is None:
        return None
    try:
        dims = Dimensions.model_validate(container)
    except ValidationError:
        return None
    if dims.is_empty():
        return None
    return dims.model_dump(mode="json", exclude_none=True)


def canonical_weight(raw: Any) -> Optional[Dict[str, Any]]:
    try:
        w = _WEIGHT.validate_python(raw)
    except ValidationError:
        return None
    return None if w is None else w.model_dump(mode="json")


def to_dynamo(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, dict):
        return {k: to_dynamo(x) for k, x in v.items()}
    return v


def _from_dynamo(v: Any) -> Any:
    """Decimal → float so stored and planned values compare by value."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _from_dynamo(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_from_dynamo(x) for x in v]
    return v


def plan_envelope_update(
    item: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """Return ``(set_attrs, remove_attrs)`` to bring one raw row current.

    Both empty means the row is already canonical. Pure: no I/O.
    """
    dims = canonical_dimensions(item.get("dimensions"))
    largest = None
    if dims:
        largest = max(axis["value"] for axis in dims.values())
    target: Dict[str, Any] = {
        "dimensions": dims,
        "weight": canonical_weight(item.get("weight")),
        "max_dimension": None if largest is None else {"value": largest, "unit": "mm"},
    }
    to_set: Dict[str, Any] = {}
    to_remove: List[str] = []
    for attr in _ATTRS:
        stored = _from_dynamo(item.get(attr))
        want = target[attr]
        if want is None:
            if attr in item:
                to_remove.append(attr)
        elif stored != want:
            to_set[attr] = want
    return to_set, to_remove


@dataclass
class EnvelopeMigrationResult:
    scanned: int = 0
    already_canonical: int = 0
    planned: int = 0
    written: int = 0
    conflicts: int = 0
    dims_dropped: int = 0  # stored dimensions carried no usable axis
    weight_dropped: int = 0
    applied: bool = False
    samples: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _condition_for(item: Dict[str, Any]) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    parts = ["attribute_exists(PK)"]
    names: Dict[str, str] = {}
    values: Dict[str, Any] = {}
    for attr in _ATTRS:
        names[f"#c_{attr}"] = attr
        if attr in item:
            values[f":c_{attr}"] = item[attr]
            parts.append(f"#c_{attr} = :c_{attr}")
        else:
            parts.append(f"attribute_not_exists(#c_{attr})")
    return " AND ".join(parts), names, values


def migrate_envelopes(table: Any, *, apply: bool = False) -> EnvelopeMigrationResult:
    """Scan ``table`` (a boto3 Table) and canonicalise every product row.

    Read-only unless ``apply`` is True.
    """
    result = EnvelopeMigrationResult(applied=apply)
    kwargs: Dict[str, Any] = {
        "ProjectionExpression": "PK, SK, #d, #w, #m",
        "ExpressionAttributeNames": {
            "#d": "dimensions",
            "#w": "weight",
            "#m": "max_dimension",
        },
    }
    while True:
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            if not str(item.get("PK", "")).startswith("PRODUCT#"):
                continue
            result.scanned += 1
            to_set, to_remove = plan_envelope_update(item)
            if not to_set and not to_remove:
                result.already_canonical += 1
                continue
            result.planned += 1
            if "dimensions" in to_remove:
                result.dims_dropped += 1
            if "weight" in to_remove:
                result.weight_dropped += 1
            if len(result.samples) < 10:
                result.samples.append(
                    {
                        "SK": item["SK"],
                        "before": {a: _from_dynamo(item.get(a)) for a in _ATTRS},
                        "set": to_set,
                        "remove": to_remove,
                    }
                )
            if not apply:
                continue
            cond, names, values = _condition_for(item)
            clauses = []
            for attr, val in to_set.items():
                names[f"#s_{attr}"] = attr
                values[f":s_{attr}"] = to_dynamo(val)
                clauses.append(f"#s_{attr} = :s_{attr}")
            expr = ""
            if clauses:
                expr += "SET " + ", ".join(clauses)
            if to_remove:
                for attr in to_remove:
                    names[f"#r_{attr}"] = attr
                expr += (
                    (" " if expr else "")
                    + "REMOVE "
                    + ", ".join(f"#r_{a}" for a in to_remove)
                )
            update: Dict[str, Any] = {
                "Key": {"PK": item["PK"], "SK": item["SK"]},
                "UpdateExpression": expr,
                "ConditionExpression": cond,
                "ExpressionAttributeNames": names,
            }
            if values:
                update["ExpressionAttributeValues"] = values
            try:
                table.update_item(**update)
                result.written += 1
            except ClientError as e:
                if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    result.conflicts += 1
                else:
                    raise
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return result
