#!/usr/bin/env python3
"""Query CLI for the product database.

Agent-friendly interface to search, filter, and inspect products.
Structured JSON to stdout, logs to stderr.

Exit codes:
    0 — results found
    1 — error
    2 — no results

Usage:
    uv run dsm search "EC-45"                                    # text search
    uv run dsm search "Maxon" --type motor                       # scoped search
    uv run dsm list --type motor                                 # list all motors
    uv run dsm list --type motor --manufacturer Maxon             # filter by mfg
    uv run dsm list --type motor --sort rated_power:desc          # list sorted
    uv run dsm get <product_id> --type motor                     # full details
    uv run dsm filter --type motor --where "rated_power>100"     # spec filter
    uv run dsm filter --type motor --where "rated_voltage>=24" --sort "rated_torque:desc"
    uv run dsm find --type motor --where "rated_voltage>=24" --where "rated_power>=100" --sort "rated_torque:desc"
    uv run dsm find --type motor "EC" --manufacturer Maxon --sort "rated_power:desc"
    uv run dsm types                                             # type summary
    uv run dsm manufacturers --type motor                        # list manufacturers
    uv run dsm fields --type motor                               # field definitions
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging — stderr + file so stdout stays clean JSON
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).resolve().parent.parent / ".logs"
LOG_DIR.mkdir(exist_ok=True)

_fmt = logging.Formatter(
    "%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
)
_sh = logging.StreamHandler(sys.stderr)
_sh.setFormatter(_fmt)
_fh = logging.FileHandler(LOG_DIR / "query_cli.log")
_fh.setFormatter(_fmt)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"), handlers=[_sh, _fh])
log = logging.getLogger("dsm")

# Product types included in search/list (skip metadata-only types)
QUERYABLE_TYPES = {"motor", "drive", "gearhead", "robot_arm", "electric_cylinder"}

# Key specs shown in summary views per product type
SUMMARY_SPECS: dict[str, list[str]] = {
    "motor": [
        "type",
        "rated_power",
        "rated_voltage",
        "rated_current",
        "rated_speed",
        "rated_torque",
        "peak_torque",
    ],
    "drive": [
        "type",
        "rated_power",
        "input_voltage",
        "rated_current",
        "peak_current",
        "fieldbus",
    ],
    "gearhead": [
        "gear_ratio",
        "gear_type",
        "stages",
        "max_continuous_torque",
        "backlash",
        "efficiency",
    ],
    "robot_arm": [
        "payload",
        "reach",
        "degrees_of_freedom",
        "max_tcp_speed",
        "pose_repeatability",
    ],
    "electric_cylinder": [
        "type",
        "stroke",
        "max_push_force",
        "max_pull_force",
        "continuous_force",
        "max_linear_speed",
        "rated_voltage",
        "rated_power",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_out(data: Any, *, exit_code: int = 0) -> None:
    """Write compact JSON to stdout and exit."""
    json.dump(data, sys.stdout, separators=(",", ":"), default=str)
    sys.stdout.write("\n")
    sys.exit(exit_code)


def _get_db():
    from specodex.db.dynamo import DynamoDBClient

    table = os.environ.get("DYNAMODB_TABLE_NAME", "products")
    return DynamoDBClient(table_name=table)


def _get_model_class(product_type: str):
    from specodex.config import SCHEMA_CHOICES

    if product_type not in SCHEMA_CHOICES:
        valid = sorted(SCHEMA_CHOICES.keys())
        _json_out(
            {"error": f"Unknown type '{product_type}'", "valid_types": valid},
            exit_code=1,
        )
    return SCHEMA_CHOICES[product_type]


def _fetch_products(product_type: str | None = None, limit: int | None = None) -> list:
    """Fetch products from DynamoDB, optionally scoped to a type."""
    db = _get_db()
    if product_type:
        cls = _get_model_class(product_type)
        return db.list(cls, limit=limit)

    from specodex.config import SCHEMA_CHOICES

    all_products: list = []
    for ptype in sorted(QUERYABLE_TYPES & set(SCHEMA_CHOICES)):
        cls = SCHEMA_CHOICES[ptype]
        all_products.extend(db.list(cls, limit=limit))
    return all_products


def product_summary(product: Any, *, omit_type: bool = False) -> dict:
    """Compact flat representation with identification + key specs."""
    ptype = product.product_type
    summary: dict[str, Any] = {
        "id": str(product.product_id)[:8],
        "manufacturer": product.manufacturer,
        "product_name": product.product_name,
        "part_number": product.part_number,
    }
    if not omit_type:
        summary["product_type"] = ptype
    for key in SUMMARY_SPECS.get(ptype, []):
        val = getattr(product, key, None)
        if val is not None:
            summary[key] = val
    return summary


def extract_numeric(value: Any) -> float | None:
    """Pull a numeric value from ValueUnit, MinMaxUnit, int, float, or Decimal."""
    from specodex.models.common import MinMaxUnit, ValueUnit

    if value is None:
        return None
    if isinstance(value, ValueUnit):
        return float(value.value)
    if isinstance(value, MinMaxUnit):
        scalar = value.min if value.min is not None else value.max
        return float(scalar) if scalar is not None else None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str) and ";" in value:
        range_part = value.split(";")[0]
        match = re.match(r"^(-?[\d.]+)", range_part)
        if match:
            return float(match.group(1))
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def text_score(product: Any, query: str) -> int:
    """Score a product against a text query. Higher = better. 0 = no match."""
    q = query.lower()
    score = 0

    # (field_name, exact_match_score, contains_score)
    searchable = [
        ("part_number", 100, 80),
        ("product_name", 90, 70),
        ("manufacturer", 85, 60),
        ("series", 50, 40),
        ("product_family", 50, 40),
        ("type", 30, 20),
    ]

    for field, exact_bonus, contains_bonus in searchable:
        val = getattr(product, field, None)
        if not val:
            continue
        val_lower = str(val).lower()
        if val_lower == q:
            score = max(score, exact_bonus)
        elif q in val_lower:
            score = max(score, contains_bonus)
        elif val_lower in q:
            score = max(score, contains_bonus - 10)

    return score


def parse_where(expr: str) -> tuple[str, str, str]:
    """Parse 'field>=value' into (field, operator, value_str)."""
    for op in (">=", "<=", "!=", ">", "<", "="):
        idx = expr.find(op)
        if idx > 0:
            field = expr[:idx].strip()
            value = expr[idx + len(op) :].strip()
            return field, op, value
    raise ValueError(f"Cannot parse filter: '{expr}'. Use format: field>value")


def parse_sort(expr: str) -> tuple[str, bool]:
    """Parse 'field:desc' into (field, reverse). Default direction is ascending."""
    if ":" in expr:
        field, direction = expr.rsplit(":", 1)
        return field.strip(), direction.strip().lower().startswith("d")
    return expr.strip(), False


def sort_products(products: list, sort_keys: list[str]) -> list:
    """Multi-level sort by spec values. Each key is 'field:asc' or 'field:desc'."""
    if not sort_keys:
        return products

    import functools

    parsed = [parse_sort(k) for k in sort_keys]
    log.info("Sorting by %s", [(f, "desc" if r else "asc") for f, r in parsed])

    def compare(a: Any, b: Any) -> int:
        for field, reverse in parsed:
            a_val = getattr(a, field, None)
            b_val = getattr(b, field, None)
            a_num = extract_numeric(a_val)
            b_num = extract_numeric(b_val)

            # None always sorts last
            if a_val is None and b_val is None:
                continue
            if a_val is None:
                return 1
            if b_val is None:
                return -1

            cmp = 0
            if a_num is not None and b_num is not None:
                cmp = (a_num > b_num) - (a_num < b_num)
            else:
                a_str = str(a_val).lower()
                b_str = str(b_val).lower()
                cmp = (a_str > b_str) - (a_str < b_str)

            if cmp != 0:
                return -cmp if reverse else cmp
        return 0

    return sorted(products, key=functools.cmp_to_key(compare))


def apply_where(product: Any, field: str, op: str, value: str) -> bool:
    """Check if a product passes a single where clause."""
    product_val = getattr(product, field, None)
    if product_val is None:
        return False

    # Numeric comparison
    num_product = extract_numeric(product_val)
    num_filter: float | None = None
    try:
        num_filter = float(value)
        is_numeric = True
    except ValueError:
        is_numeric = False

    if num_product is not None and is_numeric:
        if op == ">":
            return num_product > num_filter  # type: ignore[operator]
        if op == "<":
            return num_product < num_filter  # type: ignore[operator]
        if op == ">=":
            return num_product >= num_filter  # type: ignore[operator]
        if op == "<=":
            return num_product <= num_filter  # type: ignore[operator]
        if op == "=":
            return num_product == num_filter  # type: ignore[operator]
        if op == "!=":
            return num_product != num_filter  # type: ignore[operator]

    # String comparison (case-insensitive substring)
    str_product = str(product_val).lower()
    str_filter = value.lower()
    if op == "=":
        return str_filter in str_product
    if op == "!=":
        return str_filter not in str_product

    return False


_VALUE_UNIT_ALIAS_NAMES = frozenset(
    {
        "ValueUnit",
        "Voltage",
        "Current",
        "Power",
        "Torque",
        "Speed",
        "Force",
        "Length",
        "Mass",
        "Temperature",
        "Frequency",
        "Inertia",
        "Resistance",
        "Inductance",
    }
)

_MIN_MAX_UNIT_ALIAS_NAMES = frozenset(
    {
        "MinMaxUnit",
        "VoltageRange",
        "CurrentRange",
        "TemperatureRange",
        "FrequencyRange",
        "ForceRange",
    }
)


def _field_type_from_annotation(ann_str: str) -> tuple[str, str]:
    """Derive field type and hint from annotation string.

    Matches any per-quantity narrowed alias (Voltage, Current, ...) as
    well as the bare ValueUnit / MinMaxUnit names.
    """
    if any(name in ann_str for name in _MIN_MAX_UNIT_ALIAS_NAMES):
        return "range", "min-max;unit (e.g. '20-40;V')"
    if any(name in ann_str for name in _VALUE_UNIT_ALIAS_NAMES):
        return "numeric", "value;unit (e.g. '24;V')"
    if "List[" in ann_str or "list[" in ann_str:
        return "list", "list of values"
    if "Optional[int]" in ann_str or ann_str == "int":
        return "int", "integer"
    if "Optional[float]" in ann_str or ann_str == "float":
        return "float", "decimal number"
    return "string", "text"


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_search(args: argparse.Namespace) -> None:
    """Text search across products."""
    query = args.query
    log.info("Searching for '%s'", query)

    products = _fetch_products(product_type=getattr(args, "type", None))

    scored = []
    for p in products:
        s = text_score(p, query)
        if s > 0:
            scored.append((s, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    limit = args.limit or 5
    results = scored[:limit]
    omit = bool(getattr(args, "type", None))

    if not results:
        _json_out({"count": 0, "products": []}, exit_code=2)

    _json_out(
        {
            "count": len(results),
            "products": [
                {**product_summary(p, omit_type=omit), "relevance": s}
                for s, p in results
            ],
        }
    )


def cmd_list(args: argparse.Namespace) -> None:
    """List products with optional filters."""
    products = _fetch_products(product_type=getattr(args, "type", None))

    if args.manufacturer:
        mfg = args.manufacturer.lower()
        products = [p for p in products if mfg in p.manufacturer.lower()]

    if args.family:
        fam = args.family.lower()
        products = [
            p
            for p in products
            if getattr(p, "product_family", None) and fam in p.product_family.lower()
        ]

    if getattr(args, "sort", None):
        products = sort_products(products, args.sort)

    limit = args.limit or 10
    products = products[:limit]
    omit = bool(getattr(args, "type", None))

    _json_out(
        {
            "count": len(products),
            "products": [product_summary(p, omit_type=omit) for p in products],
        },
        exit_code=0 if products else 2,
    )


def cmd_get(args: argparse.Namespace) -> None:
    """Get full product details by ID."""
    db = _get_db()
    cls = _get_model_class(args.type)
    product = db.read(args.product_id, cls)

    if not product:
        _json_out(
            {"error": "not found", "product_id": args.product_id, "type": args.type},
            exit_code=2,
        )

    _json_out(product.model_dump(mode="json", exclude_none=True))


def cmd_filter(args: argparse.Namespace) -> None:
    """Filter products by spec values."""
    if not args.type:
        _json_out({"error": "--type is required for filter"}, exit_code=1)

    products = _fetch_products(product_type=args.type)

    if args.manufacturer:
        mfg = args.manufacturer.lower()
        products = [p for p in products if mfg in p.manufacturer.lower()]

    for expr in args.where or []:
        try:
            field, op, value = parse_where(expr)
        except ValueError as e:
            _json_out({"error": str(e)}, exit_code=1)
        products = [p for p in products if apply_where(p, field, op, value)]

    if getattr(args, "sort", None):
        products = sort_products(products, args.sort)

    limit = args.limit or 10
    products = products[:limit]

    _json_out(
        {
            "count": len(products),
            "products": [product_summary(p, omit_type=True) for p in products],
        },
        exit_code=0 if products else 2,
    )


def cmd_find(args: argparse.Namespace) -> None:
    """Find ideal products by combining filters, sorting, and optional text search."""
    if not args.type:
        _json_out({"error": "--type is required for find"}, exit_code=1)

    log.info("Finding %s products", args.type)
    products = _fetch_products(product_type=args.type)

    # Apply manufacturer filter
    if args.manufacturer:
        mfg = args.manufacturer.lower()
        products = [p for p in products if mfg in p.manufacturer.lower()]

    # Apply where filters
    for expr in args.where or []:
        try:
            field, op, value = parse_where(expr)
        except ValueError as e:
            _json_out({"error": str(e)}, exit_code=1)
        products = [p for p in products if apply_where(p, field, op, value)]

    # Apply text query for relevance scoring (optional)
    if args.query:
        scored = [(text_score(p, args.query), p) for p in products]
        # Keep all products but boost matched ones
        scored.sort(key=lambda x: x[0], reverse=True)
        products = [p for _, p in scored]

    # Apply multi-level sorting
    if args.sort:
        products = sort_products(products, args.sort)

    limit = args.limit or 10
    products = products[:limit]

    out: list[dict] = []
    for p in products:
        entry = product_summary(p, omit_type=True)
        if args.query:
            entry["relevance"] = text_score(p, args.query)
        out.append(entry)

    _json_out(
        {"count": len(out), "type": args.type, "products": out},
        exit_code=0 if out else 2,
    )


def cmd_types(_args: argparse.Namespace) -> None:
    """Show product type counts."""
    from specodex.config import SCHEMA_CHOICES

    db = _get_db()
    counts: dict[str, int] = {}
    for ptype in sorted(SCHEMA_CHOICES):
        if ptype in QUERYABLE_TYPES:
            cls = SCHEMA_CHOICES[ptype]
            counts[ptype] = len(db.list(cls))

    _json_out(counts)


def cmd_manufacturers(args: argparse.Namespace) -> None:
    """List unique manufacturers."""
    products = _fetch_products(product_type=getattr(args, "type", None))

    mfg_counts: dict[str, int] = {}
    for p in products:
        mfg_counts[p.manufacturer] = mfg_counts.get(p.manufacturer, 0) + 1

    sorted_mfg = sorted(mfg_counts.items(), key=lambda x: x[1], reverse=True)

    _json_out([{"name": name, "products": count} for name, count in sorted_mfg])


def cmd_fields(args: argparse.Namespace) -> None:
    """Show available fields for a product type."""
    if not args.type:
        _json_out({"error": "--type is required for fields"}, exit_code=1)

    cls = _get_model_class(args.type)
    from specodex.quality import spec_fields_for_model

    spec_names = spec_fields_for_model(cls)
    raw_annotations = cls.__annotations__

    verbose = getattr(args, "verbose", False)
    fields: list[dict[str, str]] = []
    for name in spec_names:
        ann_str = raw_annotations.get(name, "")
        ftype, hint = _field_type_from_annotation(ann_str)
        entry: dict[str, str] = {"name": name, "type": ftype}
        if verbose:
            entry["hint"] = hint
            field_info = cls.model_fields.get(name)
            if field_info and field_info.description:
                entry["description"] = field_info.description
        fields.append(entry)

    _json_out({"product_type": args.type, "fields": fields})


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dsm",
        description="Query the product database.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # search
    p = sub.add_parser("search", help="Text search across products")
    p.add_argument("query", help="Search text (part number, name, manufacturer)")
    p.add_argument("-t", "--type", default=None, help="Limit to product type")
    p.add_argument(
        "-n", "--limit", type=int, default=5, help="Max results (default: 5)"
    )

    # list
    p = sub.add_parser("list", help="List products with optional filters")
    p.add_argument("-t", "--type", default=None, help="Product type")
    p.add_argument(
        "-m", "--manufacturer", default=None, help="Filter by manufacturer (substring)"
    )
    p.add_argument(
        "-f", "--family", default=None, help="Filter by product family (substring)"
    )
    p.add_argument(
        "-s",
        "--sort",
        action="append",
        help="Sort by spec (e.g. 'rated_power:desc')",
    )
    p.add_argument(
        "-n", "--limit", type=int, default=10, help="Max results (default: 10)"
    )

    # get
    p = sub.add_parser("get", help="Get full product details by ID")
    p.add_argument("product_id", help="Product UUID")
    p.add_argument("-t", "--type", required=True, help="Product type")

    # filter
    p = sub.add_parser("filter", help="Filter products by spec values")
    p.add_argument("-t", "--type", required=True, help="Product type")
    p.add_argument("-m", "--manufacturer", default=None, help="Filter by manufacturer")
    p.add_argument(
        "-w",
        "--where",
        action="append",
        help="Spec filter (e.g. 'rated_power>100')",
    )
    p.add_argument(
        "-s",
        "--sort",
        action="append",
        help="Sort by spec (e.g. 'rated_power:desc')",
    )
    p.add_argument(
        "-n", "--limit", type=int, default=10, help="Max results (default: 10)"
    )

    # find — combined filter + sort + search for agent workflows
    p = sub.add_parser(
        "find",
        help="Find ideal products by combining filters, sorting, and text search",
    )
    p.add_argument("query", nargs="?", default=None, help="Optional text query")
    p.add_argument("-t", "--type", required=True, help="Product type")
    p.add_argument("-m", "--manufacturer", default=None, help="Filter by manufacturer")
    p.add_argument(
        "-w",
        "--where",
        action="append",
        help="Spec filter (e.g. 'rated_power>100')",
    )
    p.add_argument(
        "-s",
        "--sort",
        action="append",
        help="Sort by spec (e.g. 'rated_torque:desc', 'weight:asc')",
    )
    p.add_argument(
        "-n", "--limit", type=int, default=10, help="Max results (default: 10)"
    )

    # types
    sub.add_parser("types", help="Show product type counts")

    # manufacturers
    p = sub.add_parser("manufacturers", help="List unique manufacturers")
    p.add_argument("-t", "--type", default=None, help="Limit to product type")

    # fields
    p = sub.add_parser("fields", help="Show available fields for a product type")
    p.add_argument("-t", "--type", required=True, help="Product type")
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Include hints and descriptions"
    )

    return parser


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "search": cmd_search,
        "list": cmd_list,
        "get": cmd_get,
        "filter": cmd_filter,
        "find": cmd_find,
        "types": cmd_types,
        "manufacturers": cmd_manufacturers,
        "fields": cmd_fields,
    }

    try:
        commands[args.command](args)
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(130)
    except Exception as e:
        log.error("Fatal: %s", e, exc_info=True)
        _json_out({"error": str(e)}, exit_code=1)


if __name__ == "__main__":
    main()
