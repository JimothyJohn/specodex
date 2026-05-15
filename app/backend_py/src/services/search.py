"""Search service — text scoring, where-clause filtering, sort,
limit, plus a per-type summary view.

Direct port of ``app/backend/src/services/search.ts``. Same field
weights, same operator set, same null-last sort, same default limit
(20) and same hard upper bound (100).

Keep pure-function shape — every helper is testable on its own,
which is how the Express tests cover the layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from specodex.models.product import ProductBase


# ---------------------------------------------------------------------------
# Static tables (mirrored from search.ts)
# ---------------------------------------------------------------------------


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
        "rated_torque",
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
}


# (field, exact_score, contains_score)
SEARCHABLE_FIELDS: list[tuple[str, int, int]] = [
    ("part_number", 100, 80),
    ("product_name", 90, 70),
    ("manufacturer", 85, 60),
    ("series", 50, 40),
    ("product_family", 50, 40),
    ("type", 30, 20),
]


# Operator precedence matters — longer ops come first so the
# substring scan doesn't pick the wrong split (``>=`` before ``>``).
_OPERATORS = (">=", "<=", "!=", ">", "<", "=")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_field(product: ProductBase, name: str) -> Any:
    """Safe attribute access on a Pydantic instance."""

    return getattr(product, name, None)


def extract_numeric(value: Any) -> Optional[float]:
    """Pull a comparable float from a Pydantic ValueUnit/MinMaxUnit
    (or a serialised dict), a plain number, a numeric string, or
    return ``None``.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        # Bools are a subclass of int — `isinstance(True, int)` is
        # True, but `True * 2 == 2` is rarely what callers want here.
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    # ValueUnit / MinMaxUnit Pydantic models or their dict form.
    if hasattr(value, "value") and isinstance(getattr(value, "value"), (int, float)):
        return float(value.value)
    if hasattr(value, "min") and isinstance(getattr(value, "min"), (int, float)):
        return float(value.min)
    if isinstance(value, dict):
        if isinstance(value.get("value"), (int, float)):
            return float(value["value"])
        if isinstance(value.get("min"), (int, float)):
            return float(value["min"])
    return None


def text_score(product: ProductBase, query: str) -> int:
    """Higher = better match. Zero = no match against any searchable
    field. Mirrors the score table in ``search.ts``.
    """

    q = query.lower()
    score = 0
    for field, exact_bonus, contains_bonus in SEARCHABLE_FIELDS:
        val = get_field(product, field)
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


@dataclass(frozen=True)
class WhereClause:
    field: str
    op: str
    value: str


def parse_where(expr: str) -> WhereClause:
    """``rated_power>=100`` → WhereClause(field, op, value)."""

    for op in _OPERATORS:
        idx = expr.find(op)
        if idx > 0:
            return WhereClause(
                field=expr[:idx].strip(),
                op=op,
                value=expr[idx + len(op) :].strip(),
            )
    raise ValueError(f"Cannot parse filter: '{expr}'. Use format: field>value")


@dataclass(frozen=True)
class SortKey:
    field: str
    reverse: bool


def parse_sort(expr: str) -> SortKey:
    """``rated_power:desc`` → SortKey(field, reverse=True)."""

    if ":" in expr:
        field, direction = expr.split(":", 1)
        return SortKey(
            field=field.strip(),
            reverse=direction.strip().lower().startswith("d"),
        )
    return SortKey(field=expr.strip(), reverse=False)


def apply_where(product: ProductBase, field: str, op: str, value: str) -> bool:
    """Pass-or-fail test against a single where clause."""

    product_val = get_field(product, field)
    if product_val is None:
        return False

    num_product = extract_numeric(product_val)
    try:
        num_filter = float(value)
        is_numeric = True
    except ValueError:
        num_filter = 0.0
        is_numeric = False

    if num_product is not None and is_numeric:
        if op == ">":
            return num_product > num_filter
        if op == "<":
            return num_product < num_filter
        if op == ">=":
            return num_product >= num_filter
        if op == "<=":
            return num_product <= num_filter
        if op == "=":
            return num_product == num_filter
        if op == "!=":
            return num_product != num_filter

    # Fallback: case-insensitive substring on the str() form.
    s_product = str(product_val).lower()
    s_filter = value.lower()
    if op == "=":
        return s_filter in s_product
    if op == "!=":
        return s_filter not in s_product
    return False


def sort_products(
    products: Sequence[ProductBase], sort_keys: Sequence[str]
) -> list[ProductBase]:
    """Multi-level sort. Nulls always sort last regardless of
    direction (matches the Express behaviour: ``aVal == null`` →
    return 1, ``bVal == null`` → return -1)."""

    if not sort_keys:
        return list(products)

    parsed = [parse_sort(s) for s in sort_keys]

    # We can't express null-last + direction in plain `sorted(key=...)`
    # cleanly across multiple keys, so build a comparator and use
    # functools.cmp_to_key.
    from functools import cmp_to_key

    def cmp(a: ProductBase, b: ProductBase) -> int:
        for key in parsed:
            a_val = get_field(a, key.field)
            b_val = get_field(b, key.field)
            if a_val is None and b_val is None:
                continue
            if a_val is None:
                return 1
            if b_val is None:
                return -1
            a_num = extract_numeric(a_val)
            b_num = extract_numeric(b_val)
            if a_num is not None and b_num is not None:
                delta = a_num - b_num
                cmp_val = 0 if delta == 0 else (-1 if delta < 0 else 1)
            else:
                a_s = str(a_val).lower()
                b_s = str(b_val).lower()
                cmp_val = -1 if a_s < b_s else (1 if a_s > b_s else 0)
            if cmp_val != 0:
                return -cmp_val if key.reverse else cmp_val
        return 0

    return sorted(products, key=cmp_to_key(cmp))


def product_summary(
    product: ProductBase, relevance: Optional[int] = None
) -> dict[str, Any]:
    """Compact dict with identity + per-type key specs."""

    ptype = str(get_field(product, "product_type") or "")
    summary: dict[str, Any] = {
        "product_id": str(
            get_field(product, "product_id") or get_field(product, "datasheet_id") or ""
        ),
        "product_type": ptype,
        "manufacturer": str(get_field(product, "manufacturer") or ""),
        "product_name": get_field(product, "product_name"),
        "part_number": get_field(product, "part_number"),
    }
    if relevance is not None and relevance > 0:
        summary["relevance"] = relevance

    for key in SUMMARY_SPECS.get(ptype, []):
        val = get_field(product, key)
        if val is not None:
            # ValueUnit/MinMaxUnit → serialise to dict form so the
            # JSON response carries the same shape as Express did.
            if hasattr(val, "model_dump"):
                summary[key] = val.model_dump(mode="json")
            else:
                summary[key] = val
    return summary


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class SearchParams:
    products: list[ProductBase]
    query: Optional[str] = None
    manufacturer: Optional[str] = None
    where: Optional[list[str]] = None
    sort: Optional[list[str]] = None
    limit: int = 20


@dataclass
class SearchResult:
    count: int
    products: list[dict[str, Any]]


def search_products(params: SearchParams) -> SearchResult:
    """Filter → score → sort → limit. Pure; no DB access here."""

    products = list(params.products)

    if params.manufacturer:
        mfg = params.manufacturer.lower()
        products = [
            p
            for p in products
            if (val := get_field(p, "manufacturer")) and mfg in str(val).lower()
        ]

    if params.where:
        for expr in params.where:
            clause = parse_where(expr)
            products = [
                p
                for p in products
                if apply_where(p, clause.field, clause.op, clause.value)
            ]

    # Text-scoring: keep zero-score rows when there's no query (the
    # caller still expects the products back), drop them when a
    # query is supplied.
    scored: list[tuple[ProductBase, int]]
    if params.query:
        scored = [
            (p, s)
            for p, s in ((p, text_score(p, params.query)) for p in products)
            if s > 0
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
    else:
        scored = [(p, 0) for p in products]

    if params.sort:
        sorted_products = sort_products([p for p, _ in scored], params.sort)
        # Preserve relevance values across the re-sort.
        score_by_id = {id(p): s for p, s in scored}
        scored = [(p, score_by_id.get(id(p), 0)) for p in sorted_products]

    clamped = max(1, min(int(params.limit), 100))
    limited = scored[:clamped]

    return SearchResult(
        count=len(limited),
        products=[product_summary(p, s if params.query else None) for p, s in limited],
    )
