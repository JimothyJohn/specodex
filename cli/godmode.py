#!/usr/bin/env python3
"""GOD mode — data-quality observatory for the product catalog.

Reads the products DynamoDB table once, computes nine data-quality
panels, and writes a self-contained HTML report at
``outputs/godmode/<ts>.html`` (plus a ``latest.html`` symlink and a
``<ts>.json`` machine-readable snapshot used for week-over-week drift).

Scope is exclusively data quality — coverage, oddities, distributions,
commonalities, outliers, mismatches, failure modes, quality scores,
drift. No cost monitoring, no Claude usage, no deploy state, no repo
activity. See ``todo/GODMODE.md`` for rationale.

Usage:

    source .env && uv run python cli/godmode.py --stage dev
    source .env && uv run python cli/godmode.py --stage prod --limit 5000
    source .env && uv run python cli/godmode.py --stage dev --dry-run

``--dry-run`` skips writing files; useful for stdout-only verification.

The report is intended to drive a tight feedback loop:

    dashboard surfaces an oddity → adjust prompt / page_finder / model
    / validators → re-ingest → dashboard shows the fix
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable, Optional

import boto3

from specodex.aws import decimal_to_native
from specodex.config import SCHEMA_CHOICES
from specodex.models.common import (
    MinMaxUnit,
    UnitFamily,
    ValueUnit,
    find_min_max_unit_marker,
    find_value_unit_marker,
)
from specodex.placeholders import is_placeholder
from specodex.quality import score_product, spec_fields_for_model

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / ".logs"
LOG_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR = REPO_ROOT / "outputs" / "godmode"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_DIR / "godmode.log"),
    ],
)
log = logging.getLogger("godmode")


STAGE_TABLE = {
    "dev": "products-dev",
    "staging": "products-staging",
    "prod": "products-prod",
}

# Patterns flagged as oddities. Each maps to (description, predicate).
# Predicate takes a string value, returns True if it matches.
SENTINEL_LITERALS = frozenset(
    {"null", "unknown", "n/a", "na", "-", "--", "tbd", "tba", "?", "none"}
)

# Characters allowed in spec strings: ASCII letters/digits/punctuation plus
# the unit-symbol set we expect to see.
_ALLOWED_NON_ASCII = set("°±Ωμμ²³·μΩ°ΩØ⌀ω") | set("≤≥")
_ASCII_PRINTABLE = set(chr(c) for c in range(32, 127))


def _is_compact_unit_leak(s: str) -> bool:
    """Surviving ``"value;unit"`` strings — should be 0 post-UNITS."""
    if ";" not in s:
        return False
    parts = s.split(";")
    if len(parts) != 2:
        return False
    left, right = parts[0].strip(), parts[1].strip()
    if not left or not right:
        return False
    return not any(c.isspace() for c in left) and not any(c.isspace() for c in right)


def _is_sentinel(s: str) -> bool:
    return s.strip().lower() in SENTINEL_LITERALS


def _has_edge_whitespace(s: str) -> bool:
    return bool(s) and (s != s.strip())


def _has_unexpected_nonascii(s: str) -> bool:
    for ch in s:
        if ch in _ASCII_PRINTABLE or ch in _ALLOWED_NON_ASCII:
            continue
        return True
    return False


ODDITY_PATTERNS: list[tuple[str, str, callable]] = [
    (
        "compact_unit_leak",
        'Surviving "value;unit" string (post-UNITS should be 0)',
        _is_compact_unit_leak,
    ),
    ("sentinel_literal", "Literal null/unknown/N/A/-/TBD as the value", _is_sentinel),
    ("edge_whitespace", "Leading or trailing whitespace", _has_edge_whitespace),
    (
        "unexpected_nonascii",
        "Non-ASCII characters outside expected unit symbols",
        _has_unexpected_nonascii,
    ),
]


# ---------------------------------------------------------------------------
# Snapshot dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Coverage:
    filled: int = 0
    total: int = 0

    @property
    def pct(self) -> float:
        return (self.filled / self.total) if self.total else 0.0


@dataclass
class OddityHit:
    product_type: str
    field_path: str
    pk: str
    sk: str
    raw_value: str


@dataclass
class NumericDist:
    count: int
    p5: float
    p50: float
    p95: float
    histogram: list[tuple[float, float, int]]  # (lo, hi, count)


@dataclass
class CategoricalDist:
    count: int
    distinct: int
    top: list[tuple[str, int]]  # value, count


@dataclass
class ClusterCommonality:
    manufacturer: str
    product_type: str
    cluster_size: int
    common_fields: list[tuple[str, Any]]  # (field, common value)


@dataclass
class RangeOutlier:
    family: str
    product_type: str
    field_path: str
    pk: str
    sk: str
    value: float
    unit: str
    family_median: float
    family_mad: float
    z: float


@dataclass
class UnitMismatch:
    product_type: str
    field_path: str
    field_family: str
    actual_unit: str
    pk: str
    sk: str


@dataclass
class FailureMode:
    field: str
    null_count: int
    total: int

    @property
    def null_pct(self) -> float:
        return (self.null_count / self.total) if self.total else 0.0


@dataclass
class Snapshot:
    timestamp: str
    stage: str
    table: str
    row_count: int
    by_type: dict[str, int] = field(default_factory=dict)
    coverage: dict[str, dict[str, Coverage]] = field(default_factory=dict)
    oddities: dict[str, list[OddityHit]] = field(default_factory=dict)
    numeric_dists: dict[str, dict[str, NumericDist]] = field(default_factory=dict)
    categorical_dists: dict[str, dict[str, CategoricalDist]] = field(
        default_factory=dict
    )
    cluster_commonalities: list[ClusterCommonality] = field(default_factory=list)
    range_outliers: dict[str, list[RangeOutlier]] = field(default_factory=dict)
    unit_mismatches: list[UnitMismatch] = field(default_factory=list)
    failure_modes: dict[str, list[FailureMode]] = field(default_factory=dict)
    quality_scores: dict[str, list[float]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DynamoDB scan
# ---------------------------------------------------------------------------


def scan_products(table: Any, limit: Optional[int] = None) -> list[dict]:
    kwargs: dict[str, Any] = {
        "FilterExpression": "begins_with(PK, :prefix)",
        "ExpressionAttributeValues": {":prefix": "PRODUCT#"},
    }
    rows: list[dict] = []
    while True:
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            rows.append(decimal_to_native(item))
            if limit is not None and len(rows) >= limit:
                return rows
        if "LastEvaluatedKey" not in resp:
            return rows
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


# ---------------------------------------------------------------------------
# Field walk
# ---------------------------------------------------------------------------


def _walk_fields(model_class: type, prefix: str = "") -> Iterable[tuple[str, Any]]:
    """Yield (dotted-field-name, FieldInfo) for every spec field, recursing
    into nested BaseModel types so a ``Controller.power_source`` cell shows
    up alongside top-level fields.
    """
    spec_fields = set(spec_fields_for_model(model_class))
    for name, finfo in model_class.model_fields.items():
        if not prefix and name not in spec_fields:
            continue
        path = f"{prefix}{name}"
        annotation = finfo.annotation
        nested = _nested_basemodel(annotation)
        if nested is not None:
            yield from _walk_fields(nested, prefix=f"{path}.")
        else:
            yield path, finfo


def _nested_basemodel(annotation: Any) -> Optional[type]:
    """Return the inner BaseModel class if annotation is Optional[Foo] or
    similar where Foo is a BaseModel that's NOT ValueUnit/MinMaxUnit
    (those we treat as scalar specs, not nested models)."""
    from pydantic import BaseModel

    args = getattr(annotation, "__args__", None)
    candidates = list(args) if args else [annotation]
    for c in candidates:
        if not isinstance(c, type):
            continue
        if not issubclass(c, BaseModel):
            continue
        if c in (ValueUnit, MinMaxUnit):
            return None
        return c
    return None


def _get_nested(row: dict, path: str) -> Any:
    cur: Any = row
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _histogram(
    values: list[float], buckets: int = 10
) -> list[tuple[float, float, int]]:
    if not values:
        return []
    p1 = _percentile(values, 0.01)
    p99 = _percentile(values, 0.99)
    if p99 == p1:
        return [(p1, p99, len(values))]
    span = p99 - p1
    width = span / buckets
    counts = [0] * buckets
    for v in values:
        # clip so outliers don't blow out the chart; they show up in
        # range_outliers separately.
        clipped = max(p1, min(p99, v))
        idx = min(buckets - 1, int((clipped - p1) / width))
        counts[idx] += 1
    return [(p1 + i * width, p1 + (i + 1) * width, counts[i]) for i in range(buckets)]


def _value_filled(value: Any) -> bool:
    """Mirror ``score_product``'s definition: not None and not a placeholder
    string and (for nested dicts) has at least one informative subfield."""
    if value is None:
        return False
    if isinstance(value, str):
        return not is_placeholder(value)
    if isinstance(value, dict):
        # ValueUnit / MinMaxUnit / nested model — non-empty dict counts.
        return any(v is not None for v in value.values())
    if isinstance(value, list):
        return len(value) > 0
    return True


def _classify_field(finfo: Any) -> tuple[Optional[UnitFamily], str]:
    """Return (family, kind) where kind ∈ {"value_unit", "min_max_unit", "scalar"}.

    ``family`` is the UnitFamily attached via ValueUnitMarker/MinMaxUnitMarker
    metadata, or None for plain scalars.
    """
    metadata = list(getattr(finfo, "metadata", []) or [])
    vmarker = find_value_unit_marker(metadata)
    if vmarker is not None:
        return vmarker.family, "value_unit"
    mmarker = find_min_max_unit_marker(metadata)
    if mmarker is not None:
        return mmarker.family, "min_max_unit"
    return None, "scalar"


def analyse(rows: list[dict]) -> Snapshot:
    """Compute all data-quality panels from raw DynamoDB rows."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap = Snapshot(timestamp=timestamp, stage="", table="", row_count=len(rows))

    # Group by product_type — coverage is per-type, distributions are per-type.
    rows_by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        ptype = r.get("product_type")
        if ptype:
            rows_by_type[ptype].append(r)
    snap.by_type = {t: len(rs) for t, rs in rows_by_type.items()}

    # Per-family value pool for range-outlier detection.
    family_pool: dict[str, list[tuple[float, str, str, str, str, str]]] = defaultdict(
        list
    )

    for ptype, rs in rows_by_type.items():
        model_class = SCHEMA_CHOICES.get(ptype)
        if model_class is None:
            log.warning("Unknown product_type %r — skipping (%d rows)", ptype, len(rs))
            continue

        coverage: dict[str, Coverage] = {}
        numeric_collect: dict[str, list[float]] = defaultdict(list)
        categorical_collect: dict[str, list[str]] = defaultdict(list)
        oddity_collect: dict[str, list[OddityHit]] = defaultdict(list)

        for path, finfo in _walk_fields(model_class):
            family, kind = _classify_field(finfo)
            cov = coverage.setdefault(path, Coverage(total=len(rs)))
            for r in rs:
                value = _get_nested(r, path)
                if _value_filled(value):
                    cov.filled += 1

                # Oddity scan — string values anywhere in the row.
                if isinstance(value, str):
                    for tag, _desc, predicate in ODDITY_PATTERNS:
                        if predicate(value):
                            oddity_collect[tag].append(
                                OddityHit(
                                    product_type=ptype,
                                    field_path=path,
                                    pk=r.get("PK", "?"),
                                    sk=r.get("SK", "?"),
                                    raw_value=value,
                                )
                            )

                # Distribution + family pool collection.
                if kind == "value_unit" and isinstance(value, dict):
                    v = value.get("value")
                    u = value.get("unit")
                    if isinstance(v, (int, float)):
                        numeric_collect[path].append(float(v))
                        if family is not None:
                            family_pool[family.name].append(
                                (
                                    float(v),
                                    str(u or ""),
                                    ptype,
                                    path,
                                    r.get("PK", "?"),
                                    r.get("SK", "?"),
                                )
                            )
                            if u and not family.contains(str(u)):
                                snap.unit_mismatches.append(
                                    UnitMismatch(
                                        product_type=ptype,
                                        field_path=path,
                                        field_family=family.name,
                                        actual_unit=str(u),
                                        pk=r.get("PK", "?"),
                                        sk=r.get("SK", "?"),
                                    )
                                )
                elif kind == "min_max_unit" and isinstance(value, dict):
                    for key in ("min", "max"):
                        v = value.get(key)
                        if isinstance(v, (int, float)):
                            numeric_collect[f"{path}.{key}"].append(float(v))
                    u = value.get("unit")
                    if family is not None and u and not family.contains(str(u)):
                        snap.unit_mismatches.append(
                            UnitMismatch(
                                product_type=ptype,
                                field_path=path,
                                field_family=family.name,
                                actual_unit=str(u),
                                pk=r.get("PK", "?"),
                                sk=r.get("SK", "?"),
                            )
                        )
                elif isinstance(value, str) and not is_placeholder(value):
                    categorical_collect[path].append(value)
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    numeric_collect[path].append(float(value))

        snap.coverage[ptype] = coverage

        # Distributions.
        snap.numeric_dists[ptype] = {}
        for path, vals in numeric_collect.items():
            snap.numeric_dists[ptype][path] = NumericDist(
                count=len(vals),
                p5=_percentile(vals, 0.05),
                p50=_percentile(vals, 0.50),
                p95=_percentile(vals, 0.95),
                histogram=_histogram(vals),
            )
        snap.categorical_dists[ptype] = {}
        for path, vals in categorical_collect.items():
            counts: dict[str, int] = defaultdict(int)
            for v in vals:
                counts[v] += 1
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:20]
            snap.categorical_dists[ptype][path] = CategoricalDist(
                count=len(vals), distinct=len(counts), top=top
            )

        for tag, hits in oddity_collect.items():
            snap.oddities.setdefault(tag, []).extend(hits)

    # Cluster commonalities — same value across every product in a
    # (manufacturer, type) bucket of size ≥ 3.
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        mfr = r.get("manufacturer")
        ptype = r.get("product_type")
        if mfr and ptype:
            buckets[(mfr, ptype)].append(r)
    for (mfr, ptype), brs in buckets.items():
        if len(brs) < 3:
            continue
        model_class = SCHEMA_CHOICES.get(ptype)
        if model_class is None:
            continue
        common: list[tuple[str, Any]] = []
        for path, _finfo in _walk_fields(model_class):
            vals = [_get_nested(r, path) for r in brs]
            if not all(_value_filled(v) for v in vals):
                continue
            try:
                marker = json.dumps(vals[0], sort_keys=True, default=str)
                if all(
                    json.dumps(v, sort_keys=True, default=str) == marker
                    for v in vals[1:]
                ):
                    common.append((path, vals[0]))
            except (TypeError, ValueError):
                continue
        if common:
            snap.cluster_commonalities.append(
                ClusterCommonality(
                    manufacturer=mfr,
                    product_type=ptype,
                    cluster_size=len(brs),
                    common_fields=common,
                )
            )

    # Range outliers per family — > 3 robust z-scores from the family median.
    for family_name, samples in family_pool.items():
        values = [s[0] for s in samples]
        if len(values) < 10:
            continue
        med = statistics.median(values)
        mad = statistics.median([abs(v - med) for v in values]) or 1e-9
        outliers: list[RangeOutlier] = []
        for v, u, ptype, path, pk, sk in samples:
            z = abs(v - med) / (1.4826 * mad)
            if z >= 3.0:
                outliers.append(
                    RangeOutlier(
                        family=family_name,
                        product_type=ptype,
                        field_path=path,
                        pk=pk,
                        sk=sk,
                        value=v,
                        unit=u,
                        family_median=med,
                        family_mad=mad,
                        z=z,
                    )
                )
        if outliers:
            snap.range_outliers[family_name] = sorted(outliers, key=lambda o: -o.z)[:50]

    # Per-manufacturer failure modes — top fields by null_pct, requires ≥ 5
    # products from the manufacturer to avoid noise.
    by_mfr: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        mfr = r.get("manufacturer")
        if mfr:
            by_mfr[mfr].append(r)
    for mfr, mrs in by_mfr.items():
        if len(mrs) < 5:
            continue
        # Compute null counts per (type, field) within this manufacturer's
        # rows. Group fields by their resolved path; report top 10.
        field_total: dict[str, int] = defaultdict(int)
        field_null: dict[str, int] = defaultdict(int)
        for r in mrs:
            ptype = r.get("product_type")
            model_class = SCHEMA_CHOICES.get(ptype)
            if model_class is None:
                continue
            for path, _finfo in _walk_fields(model_class):
                key = f"{ptype}.{path}"
                field_total[key] += 1
                if not _value_filled(_get_nested(r, path)):
                    field_null[key] += 1
        modes = [
            FailureMode(field=k, null_count=field_null[k], total=field_total[k])
            for k in field_total
            if field_null[k] > 0
        ]
        modes.sort(key=lambda m: -m.null_pct)
        snap.failure_modes[mfr] = modes[:10]

    # Quality score distribution — recompute from rows, since the live row
    # might differ from what was scored at ingest time.
    for ptype, rs in rows_by_type.items():
        model_class = SCHEMA_CHOICES.get(ptype)
        if model_class is None:
            continue
        scores: list[float] = []
        for r in rs:
            try:
                instance = model_class.model_validate(r, strict=False)
            except Exception:
                continue
            score, _filled, _total, _missing = score_product(instance)
            scores.append(score)
        if scores:
            snap.quality_scores[ptype] = scores

    return snap


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


@dataclass
class CoverageDrift:
    product_type: str
    field: str
    pct_now: float
    pct_prev: float
    delta_pp: float


@dataclass
class Drift:
    prev_timestamp: str
    coverage_regressions: list[CoverageDrift] = field(default_factory=list)
    coverage_improvements: list[CoverageDrift] = field(default_factory=list)
    new_oddity_patterns: list[str] = field(default_factory=list)
    row_delta: int = 0


def diff(snap: Snapshot, prev: Optional[dict]) -> Optional[Drift]:
    """Diff against a previous snapshot dict (loaded from <ts>.json).

    Threshold: a coverage delta counts as significant if it's ≥ 5 percentage
    points absolute or ≥ 50% relative, whichever is larger.
    """
    if prev is None:
        return None
    drift = Drift(prev_timestamp=prev.get("timestamp", "?"))
    drift.row_delta = snap.row_count - prev.get("row_count", 0)

    prev_cov = prev.get("coverage", {})
    for ptype, fields in snap.coverage.items():
        prev_fields = prev_cov.get(ptype, {})
        for path, cov in fields.items():
            now_pct = cov.pct
            prev_entry = prev_fields.get(path) or {}
            prev_pct = (
                (prev_entry.get("filled", 0) / prev_entry.get("total", 1))
                if prev_entry.get("total")
                else 0.0
            )
            delta = now_pct - prev_pct
            abs_threshold = 0.05
            rel_threshold = 0.5
            if abs(delta) < abs_threshold:
                continue
            if (
                prev_pct > 0
                and abs(delta) / prev_pct < rel_threshold
                and abs(delta) < abs_threshold
            ):
                continue
            entry = CoverageDrift(
                product_type=ptype,
                field=path,
                pct_now=now_pct,
                pct_prev=prev_pct,
                delta_pp=delta * 100,
            )
            (
                drift.coverage_regressions if delta < 0 else drift.coverage_improvements
            ).append(entry)

    drift.coverage_regressions.sort(key=lambda d: d.delta_pp)
    drift.coverage_improvements.sort(key=lambda d: -d.delta_pp)

    prev_patterns = set(prev.get("oddities", {}).keys())
    drift.new_oddity_patterns = sorted(set(snap.oddities) - prev_patterns)

    return drift


def _load_prev_snapshot(current_ts: str) -> Optional[dict]:
    candidates = sorted(
        (p for p in OUTPUTS_DIR.glob("*.json") if p.stem != current_ts),
        reverse=True,
    )
    if not candidates:
        return None
    try:
        return json.loads(candidates[0].read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not read prior snapshot %s: %s", candidates[0], e)
        return None


# ---------------------------------------------------------------------------
# HTML render
# ---------------------------------------------------------------------------


CSS = """
:root {
  --paper: #E8E2C9;
  --ink: #1A1A14;
  --hairline: #5C5C4A;
  --od: #3B4A2A;
  --amber: #9C7A16;
  --stamp: #7A1F1F;
  --ok: #5C5C4A;
}
* { box-sizing: border-box; }
body {
  background: var(--paper);
  color: var(--ink);
  font-family: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  margin: 0;
  padding: 24px;
  font-variant-numeric: tabular-nums;
}
h1 { font-family: 'Oswald', sans-serif; text-transform: uppercase; letter-spacing: 0.18em; border-bottom: 2px solid var(--ink); padding-bottom: 8px; }
h2 { font-family: 'Oswald', sans-serif; text-transform: uppercase; letter-spacing: 0.12em; border-bottom: 1px solid var(--hairline); padding-bottom: 4px; margin-top: 32px; }
h3 { font-family: 'Oswald', sans-serif; text-transform: uppercase; letter-spacing: 0.08em; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; }
th, td { padding: 4px 8px; border-top: 1px solid var(--hairline); border-bottom: 1px solid var(--hairline); text-align: left; vertical-align: top; }
th { font-family: 'Oswald', sans-serif; text-transform: uppercase; font-weight: 600; letter-spacing: 0.06em; }
td.num { text-align: right; }
.muted { color: var(--hairline); }
.danger { color: var(--stamp); font-weight: bold; }
.ok { color: var(--ok); }
.warn { color: var(--amber); font-weight: bold; }
.bar { display: inline-block; height: 10px; background: var(--ink); vertical-align: middle; }
.bar-bg { display: inline-block; height: 10px; width: 100px; background: var(--paper); border: 1px solid var(--hairline); vertical-align: middle; }
.histogram { display: flex; align-items: flex-end; height: 60px; gap: 1px; border-bottom: 1px solid var(--hairline); padding-top: 4px; }
.histogram .col { background: var(--ink); width: 16px; min-height: 1px; }
details { margin: 8px 0; }
summary { cursor: pointer; font-family: 'Oswald', sans-serif; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ink); }
.kv { display: grid; grid-template-columns: 200px 1fr; gap: 4px 16px; margin: 8px 0; }
.kv dt { color: var(--hairline); }
code { background: rgba(0,0,0,0.06); padding: 1px 4px; }
.section-meta { color: var(--hairline); margin-top: -8px; margin-bottom: 8px; font-size: 11px; }
"""


def _coverage_cell(cov: Coverage) -> str:
    pct = cov.pct
    if cov.total == 0:
        return "<td class='muted'>—</td>"
    bar_w = int(pct * 100)
    cls = "ok" if pct >= 0.75 else "warn" if pct >= 0.4 else "danger"
    return (
        f"<td class='num {cls}'>"
        f"<span class='bar-bg'><span class='bar' style='width:{bar_w}px'></span></span> "
        f"{pct * 100:.0f}% <span class='muted'>({cov.filled}/{cov.total})</span>"
        f"</td>"
    )


def _render_coverage(snap: Snapshot) -> str:
    out = ["<h2>1. Coverage matrix</h2>"]
    out.append(
        "<p class='section-meta'>% of products with a non-null, non-placeholder value per (type, field).</p>"
    )
    for ptype, fields in sorted(snap.coverage.items()):
        out.append(
            f"<h3>{escape(ptype)} <span class='muted'>({snap.by_type.get(ptype, 0)} products)</span></h3>"
        )
        out.append(
            "<table><thead><tr><th>Field</th><th>Coverage</th></tr></thead><tbody>"
        )
        for path, cov in sorted(fields.items(), key=lambda kv: kv[1].pct):
            out.append(f"<tr><td>{escape(path)}</td>{_coverage_cell(cov)}</tr>")
        out.append("</tbody></table>")
    return "\n".join(out)


def _render_oddities(snap: Snapshot) -> str:
    out = ["<h2>2. String oddities</h2>"]
    out.append(
        "<p class='section-meta'>Patterns that almost certainly indicate misextraction. Compact-unit leaks must be 0 post-UNITS.</p>"
    )
    if not snap.oddities:
        out.append("<p class='ok'>No oddities detected.</p>")
        return "\n".join(out)
    descriptions = {tag: desc for tag, desc, _ in ODDITY_PATTERNS}
    for tag, hits in snap.oddities.items():
        cls = "danger" if tag == "compact_unit_leak" else "warn"
        out.append(
            f"<details><summary class='{cls}'>{escape(tag)} — {len(hits)} hit(s)</summary>"
        )
        out.append(f"<p class='section-meta'>{escape(descriptions.get(tag, ''))}</p>")
        out.append(
            "<table><thead><tr><th>Type</th><th>Field</th><th>SK</th><th>Raw value</th></tr></thead><tbody>"
        )
        for hit in hits[:50]:
            out.append(
                f"<tr><td>{escape(hit.product_type)}</td>"
                f"<td>{escape(hit.field_path)}</td>"
                f"<td class='muted'>{escape(hit.sk)}</td>"
                f"<td><code>{escape(hit.raw_value)}</code></td></tr>"
            )
        if len(hits) > 50:
            out.append(
                f"<tr><td colspan='4' class='muted'>… {len(hits) - 50} more</td></tr>"
            )
        out.append("</tbody></table></details>")
    return "\n".join(out)


def _render_distributions(snap: Snapshot) -> str:
    out = ["<h2>3. Per-field distributions</h2>"]
    out.append(
        "<p class='section-meta'>Numeric: 10-bucket histogram between p1 and p99, with p5/p50/p95 markers. Categorical: top-20 values.</p>"
    )
    for ptype in sorted(snap.numeric_dists):
        nd = snap.numeric_dists[ptype]
        cd = snap.categorical_dists.get(ptype, {})
        if not nd and not cd:
            continue
        out.append(f"<h3>{escape(ptype)}</h3>")
        if nd:
            out.append(
                "<details><summary>Numeric ({} fields)</summary>".format(len(nd))
            )
            out.append(
                "<table><thead><tr><th>Field</th><th>Count</th><th>p5 / p50 / p95</th><th>Histogram</th></tr></thead><tbody>"
            )
            for path, dist in sorted(nd.items()):
                if dist.histogram:
                    max_c = max(c for _, _, c in dist.histogram) or 1
                    bars = "".join(
                        f"<span class='col' style='height:{int(c / max_c * 60)}px' title='{lo:.3g}–{hi:.3g}: {c}'></span>"
                        for lo, hi, c in dist.histogram
                    )
                    hist_html = f"<div class='histogram'>{bars}</div>"
                else:
                    hist_html = "<span class='muted'>—</span>"
                out.append(
                    f"<tr><td>{escape(path)}</td>"
                    f"<td class='num'>{dist.count}</td>"
                    f"<td class='num'>{dist.p5:.3g} / {dist.p50:.3g} / {dist.p95:.3g}</td>"
                    f"<td>{hist_html}</td></tr>"
                )
            out.append("</tbody></table></details>")
        if cd:
            out.append(
                "<details><summary>Categorical ({} fields)</summary>".format(len(cd))
            )
            for path, dist in sorted(cd.items()):
                out.append(
                    f"<h4>{escape(path)} <span class='muted'>({dist.count} values, {dist.distinct} distinct)</span></h4>"
                )
                out.append(
                    "<table><thead><tr><th>Value</th><th>Count</th></tr></thead><tbody>"
                )
                for value, count in dist.top:
                    out.append(
                        f"<tr><td><code>{escape(value)}</code></td><td class='num'>{count}</td></tr>"
                    )
                out.append("</tbody></table>")
            out.append("</details>")
    return "\n".join(out)


def _render_commonalities(snap: Snapshot) -> str:
    out = ["<h2>4. Cluster commonalities</h2>"]
    out.append(
        "<p class='section-meta'>(manufacturer, type) buckets ≥ 3 products where one or more fields hold the same value across the entire bucket. Suspect: LLM extracted catalog header instead of per-row value.</p>"
    )
    if not snap.cluster_commonalities:
        out.append("<p class='ok'>No cluster commonalities detected.</p>")
        return "\n".join(out)
    for c in sorted(snap.cluster_commonalities, key=lambda c: -c.cluster_size):
        out.append(
            f"<details><summary>{escape(c.manufacturer)} / {escape(c.product_type)} "
            f"<span class='muted'>({c.cluster_size} products, {len(c.common_fields)} common fields)</span></summary>"
        )
        out.append(
            "<table><thead><tr><th>Field</th><th>Common value</th></tr></thead><tbody>"
        )
        for path, val in c.common_fields:
            out.append(
                f"<tr><td>{escape(path)}</td><td><code>{escape(json.dumps(val, default=str))}</code></td></tr>"
            )
        out.append("</tbody></table></details>")
    return "\n".join(out)


def _render_outliers(snap: Snapshot) -> str:
    out = ["<h2>5. Range outliers</h2>"]
    out.append(
        "<p class='section-meta'>Values &gt; 3 robust z-scores from the family median (1.4826 × MAD). Often unit confusion or typos.</p>"
    )
    if not snap.range_outliers:
        out.append("<p class='ok'>No range outliers detected.</p>")
        return "\n".join(out)
    for family, outs in sorted(snap.range_outliers.items()):
        out.append(
            f"<details><summary>{escape(family)} <span class='muted'>({len(outs)} outlier(s); median {outs[0].family_median:.3g}, MAD {outs[0].family_mad:.3g})</span></summary>"
        )
        out.append(
            "<table><thead><tr><th>Type</th><th>Field</th><th>Value</th><th>Unit</th><th>z</th><th>SK</th></tr></thead><tbody>"
        )
        for o in outs:
            out.append(
                f"<tr><td>{escape(o.product_type)}</td>"
                f"<td>{escape(o.field_path)}</td>"
                f"<td class='num'>{o.value:.3g}</td>"
                f"<td>{escape(o.unit)}</td>"
                f"<td class='num warn'>{o.z:.1f}</td>"
                f"<td class='muted'>{escape(o.sk)}</td></tr>"
            )
        out.append("</tbody></table></details>")
    return "\n".join(out)


def _render_unit_mismatches(snap: Snapshot) -> str:
    out = ["<h2>6. Unit-family mismatches</h2>"]
    out.append(
        "<p class='section-meta'>ValueUnit/MinMaxUnit fields whose stored unit isn't in the family's accepted set. Should be near-zero post-UNITS — surviving rows are legacy.</p>"
    )
    if not snap.unit_mismatches:
        out.append("<p class='ok'>No unit-family mismatches detected.</p>")
        return "\n".join(out)
    by_family: dict[str, list[UnitMismatch]] = defaultdict(list)
    for m in snap.unit_mismatches:
        by_family[m.field_family].append(m)
    for family, ms in sorted(by_family.items()):
        out.append(
            f"<details><summary>{escape(family)} <span class='muted'>({len(ms)} mismatch(es))</span></summary>"
        )
        out.append(
            "<table><thead><tr><th>Type</th><th>Field</th><th>Actual unit</th><th>SK</th></tr></thead><tbody>"
        )
        for m in ms[:50]:
            out.append(
                f"<tr><td>{escape(m.product_type)}</td>"
                f"<td>{escape(m.field_path)}</td>"
                f"<td class='warn'><code>{escape(m.actual_unit)}</code></td>"
                f"<td class='muted'>{escape(m.sk)}</td></tr>"
            )
        if len(ms) > 50:
            out.append(
                f"<tr><td colspan='4' class='muted'>… {len(ms) - 50} more</td></tr>"
            )
        out.append("</tbody></table></details>")
    return "\n".join(out)


def _render_failure_modes(snap: Snapshot) -> str:
    out = ["<h2>7. Per-manufacturer failure modes</h2>"]
    out.append(
        "<p class='section-meta'>Top 10 fields with the highest null rate per manufacturer (≥ 5 products). Read this list to prioritise the next prompt-engineering pass.</p>"
    )
    if not snap.failure_modes:
        out.append("<p class='ok'>No manufacturers with ≥ 5 products.</p>")
        return "\n".join(out)
    sorted_mfrs = sorted(
        snap.failure_modes.items(), key=lambda kv: -sum(m.null_pct for m in kv[1])
    )
    for mfr, modes in sorted_mfrs:
        out.append(
            f"<details><summary>{escape(mfr)} <span class='muted'>({len(modes)} field(s))</span></summary>"
        )
        out.append(
            "<table><thead><tr><th>Field</th><th>Null rate</th><th>Count</th></tr></thead><tbody>"
        )
        for m in modes:
            cls = "danger" if m.null_pct > 0.5 else "warn" if m.null_pct > 0.2 else ""
            out.append(
                f"<tr><td>{escape(m.field)}</td>"
                f"<td class='num {cls}'>{m.null_pct * 100:.0f}%</td>"
                f"<td class='num'>{m.null_count}/{m.total}</td></tr>"
            )
        out.append("</tbody></table></details>")
    return "\n".join(out)


def _render_quality(snap: Snapshot) -> str:
    out = ["<h2>8. Quality-score distribution</h2>"]
    out.append(
        "<p class='section-meta'>specodex.quality.score recomputed from live rows. Bottom decile = where focused work moves the most product.</p>"
    )
    if not snap.quality_scores:
        out.append("<p class='muted'>No quality scores computed.</p>")
        return "\n".join(out)
    out.append(
        "<table><thead><tr><th>Type</th><th>Count</th><th>Min</th><th>p10</th><th>p50</th><th>p90</th><th>Max</th></tr></thead><tbody>"
    )
    for ptype, scores in sorted(snap.quality_scores.items()):
        s = sorted(scores)
        out.append(
            f"<tr><td>{escape(ptype)}</td>"
            f"<td class='num'>{len(s)}</td>"
            f"<td class='num'>{s[0]:.2f}</td>"
            f"<td class='num'>{_percentile(s, 0.10):.2f}</td>"
            f"<td class='num'>{_percentile(s, 0.50):.2f}</td>"
            f"<td class='num'>{_percentile(s, 0.90):.2f}</td>"
            f"<td class='num'>{s[-1]:.2f}</td></tr>"
        )
    out.append("</tbody></table>")
    return "\n".join(out)


def _render_drift(drift: Optional[Drift]) -> str:
    if drift is None:
        return "<h2>9. Drift</h2><p class='muted'>No prior snapshot to diff against. Re-run after at least one earlier snapshot exists.</p>"
    out = ["<h2>9. Drift</h2>"]
    out.append(
        f"<p class='section-meta'>Comparing against snapshot <code>{escape(drift.prev_timestamp)}</code>. Threshold: ≥ 5pp absolute or ≥ 50% relative.</p>"
    )
    out.append(f"<p>Row delta: <strong>{drift.row_delta:+d}</strong></p>")
    if drift.coverage_regressions:
        out.append("<h3 class='danger'>Coverage regressions</h3>")
        out.append(
            "<table><thead><tr><th>Type</th><th>Field</th><th>Now</th><th>Prev</th><th>Δpp</th></tr></thead><tbody>"
        )
        for d in drift.coverage_regressions[:30]:
            out.append(
                f"<tr><td>{escape(d.product_type)}</td>"
                f"<td>{escape(d.field)}</td>"
                f"<td class='num'>{d.pct_now * 100:.0f}%</td>"
                f"<td class='num muted'>{d.pct_prev * 100:.0f}%</td>"
                f"<td class='num danger'>{d.delta_pp:+.1f}</td></tr>"
            )
        out.append("</tbody></table>")
    if drift.coverage_improvements:
        out.append("<h3 class='ok'>Coverage improvements</h3>")
        out.append(
            "<table><thead><tr><th>Type</th><th>Field</th><th>Now</th><th>Prev</th><th>Δpp</th></tr></thead><tbody>"
        )
        for d in drift.coverage_improvements[:10]:
            out.append(
                f"<tr><td>{escape(d.product_type)}</td>"
                f"<td>{escape(d.field)}</td>"
                f"<td class='num'>{d.pct_now * 100:.0f}%</td>"
                f"<td class='num muted'>{d.pct_prev * 100:.0f}%</td>"
                f"<td class='num ok'>{d.delta_pp:+.1f}</td></tr>"
            )
        out.append("</tbody></table>")
    if drift.new_oddity_patterns:
        out.append("<h3 class='warn'>New oddity patterns</h3><ul>")
        for p in drift.new_oddity_patterns:
            out.append(f"<li><code>{escape(p)}</code></li>")
        out.append("</ul>")
    return "\n".join(out)


def render(snap: Snapshot, drift: Optional[Drift]) -> str:
    """Self-contained HTML — no external CSS, no JS, no chart libs."""
    sections = [
        _render_drift(drift),  # drift first so regressions are above the fold
        _render_coverage(snap),
        _render_oddities(snap),
        _render_unit_mismatches(snap),
        _render_outliers(snap),
        _render_commonalities(snap),
        _render_failure_modes(snap),
        _render_quality(snap),
        _render_distributions(snap),
    ]
    body = "\n".join(sections)
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<title>GOD mode — {escape(snap.stage)} — {escape(snap.timestamp)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Oswald:wght@500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head><body>
<h1>GOD mode — data-quality observatory</h1>
<dl class="kv">
  <dt>Generated</dt><dd>{escape(snap.timestamp)}</dd>
  <dt>Stage / table</dt><dd>{escape(snap.stage)} / <code>{escape(snap.table)}</code></dd>
  <dt>Rows scanned</dt><dd>{snap.row_count}</dd>
  <dt>Types</dt><dd>{", ".join(f"{escape(t)} ({n})" for t, n in sorted(snap.by_type.items()))}</dd>
</dl>
{body}
</body></html>"""


# ---------------------------------------------------------------------------
# JSON serialization (snapshot persistence)
# ---------------------------------------------------------------------------


def _snapshot_to_json(snap: Snapshot) -> dict:
    """asdict() with the numeric_dists/categorical_dists turned into pure
    JSON, and Coverage objects flattened to {filled, total}."""
    payload = asdict(snap)
    # Coverage: dataclass already serializes; re-emit as {filled, total}
    # so the round-trip in diff() doesn't require re-instantiation.
    payload["coverage"] = {
        ptype: {p: {"filled": c.filled, "total": c.total} for p, c in fs.items()}
        for ptype, fs in snap.coverage.items()
    }
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="GOD mode data-quality observatory")
    parser.add_argument("--stage", choices=list(STAGE_TABLE), default="dev")
    parser.add_argument("--table", help="Override the table name")
    parser.add_argument(
        "--limit", type=int, help="Cap scan at N rows (default: full table)"
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--dry-run", action="store_true", help="Skip writing files")
    args = parser.parse_args(argv)

    table_name = args.table or STAGE_TABLE[args.stage]
    db = boto3.resource("dynamodb", region_name=args.region)
    table = db.Table(table_name)

    log.info(
        "Scanning %s (stage=%s, limit=%s)", table_name, args.stage, args.limit or "∞"
    )
    rows = scan_products(table, limit=args.limit)
    log.info("Scanned %d rows", len(rows))

    snap = analyse(rows)
    snap.stage = args.stage
    snap.table = table_name

    prev = _load_prev_snapshot(snap.timestamp)
    drift_obj = diff(snap, prev)

    json_path = OUTPUTS_DIR / f"{snap.timestamp}.json"
    html_path = OUTPUTS_DIR / f"{snap.timestamp}.html"
    latest_path = OUTPUTS_DIR / "latest.html"

    if not args.dry_run:
        json_path.write_text(json.dumps(_snapshot_to_json(snap), indent=2, default=str))
        html_path.write_text(render(snap, drift_obj))
        if latest_path.is_symlink() or latest_path.exists():
            latest_path.unlink()
        try:
            latest_path.symlink_to(html_path.name)
        except OSError:
            # Symlink may not be supported on all filesystems — fall back to copy.
            latest_path.write_text(html_path.read_text())
        log.info("Wrote %s", html_path)
        log.info("Wrote %s", json_path)
    else:
        log.info("DRY RUN — not writing files")

    summary = {
        "stage": args.stage,
        "table": table_name,
        "rows": snap.row_count,
        "types": snap.by_type,
        "oddity_counts": {tag: len(hits) for tag, hits in snap.oddities.items()},
        "outlier_counts": {f: len(o) for f, o in snap.range_outliers.items()},
        "unit_mismatches": len(snap.unit_mismatches),
        "common_clusters": len(snap.cluster_commonalities),
        "manufacturers_with_failures": len(snap.failure_modes),
        "report": str(html_path) if not args.dry_run else None,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
