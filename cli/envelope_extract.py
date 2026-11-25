"""Backfill product envelopes (L×W×H + weight) from datasheet pages.

For every product row missing ``dimensions`` or ``weight``, re-open its
own ``datasheet_url``, send the rows' spec pages plus nearby outline-drawing
pages to Gemini (``specodex.envelope.extract``), and match each extracted
variant back to rows by designator (``specodex.envelope.match``: exact,
then token-boundary prefix; ties between different envelopes are skipped).

Two independent readings. Every datasheet is read twice (``PASSES``: the
same pages, two differently-worded prompts). Weight is filled from the
first reading that has one (weights read correctly in the 2026-11-24 audit),
unless both readings carry a weight and they differ by more than 10%.
Every value must also be printed on its page in line with the designator
(same table row or column, ``match.value_in_line``) — two readings can
share a misread, the PDF text layer cannot. Dimensions are filled only when
both readings matched the row, carry all three axes, and agree within 10%
on every axis (``match.agreed_dims``);
anything else leaves them empty (Nick's policy, TODO.md
``envelope-dims-accuracy-policy``, option A).

Enrich-only: a field the row already carries is never overwritten. Each
write is a conditional ``UpdateItem`` (``attribute_not_exists`` on the
field being filled), so a concurrent writer always wins. Values go through
the same canonicalisation as ``ProductBase`` (axes → mm, weight → kg,
``max_dimension`` derived).

Resumable: each datasheet's two readings are cached under
``outputs/envelope_extract/<sha1(url)>.json`` (``CACHE_VERSION``; an
older single-reading record is re-extracted); reruns read the cache
instead of calling Gemini (``--refresh`` ignores it). Token usage is
recorded per datasheet and summed into the run's cost estimate.

Usage:

    # Plan only — no downloads, no Gemini; counts rows / datasheets:
    ./Quickstart envelope-extract --plan

    # Dry run on 5 drive datasheets (calls Gemini, writes nothing):
    ./Quickstart envelope-extract --type drive --limit 5

    # Apply on dev:
    ./Quickstart envelope-extract --type drive --apply

``--apply`` refuses ``--stage prod``: this is a Gemini enrichment pass, and
prod data moves only via ``./Quickstart admin promote``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from specodex.admin.envelope_migration import (
    canonical_dimensions,
    canonical_weight,
    to_dynamo,
)
from specodex.admin.operations import make_client
from specodex.envelope.extract import (
    PASSES,
    PDF_LOCK,
    VERIFIED_FIELDS,
    VERIFIER_VERSION,
    EnvelopeExtraction,
    extract_envelopes,
    reverify,
)
from specodex.envelope.match import agreed_dims, match_rows, select_envelope_pages
from specodex.page_finder import find_drawing_pages_scored
from specodex.utils import get_document

logger = logging.getLogger(__name__)

STAGES = ("dev", "staging", "prod")
CACHE_DIR = Path("outputs/envelope_extract")
CACHE_VERSION = 3
WEIGHT_CONFLICT_TOL = 0.10
FULL_ENVELOPE = ("length", "width", "height")
# Gemini 2.5 Flash list price, USD per 1M tokens (thinking billed as output).
# Used only for the printed estimate.
USD_PER_M_INPUT = 0.30
USD_PER_M_OUTPUT = 2.50
_PROJECTION = (
    "PK, SK, product_type, manufacturer, part_number, product_name, "
    "frame_size, datasheet_url, #pages, dimensions, weight"
)


def _needs(item: Dict[str, Any]) -> Tuple[bool, bool]:
    """(needs_dimensions, needs_weight) for one raw row."""
    return (
        canonical_dimensions(item.get("dimensions")) is None,
        canonical_weight(item.get("weight")) is None,
    )


def scan_candidates(
    table: Any, product_type: Optional[str], manufacturer: Optional[str]
) -> List[Dict[str, Any]]:
    kwargs: Dict[str, Any] = {
        "ProjectionExpression": _PROJECTION,
        "ExpressionAttributeNames": {"#pages": "pages"},
    }
    needle = manufacturer.lower() if manufacturer else None
    out: List[Dict[str, Any]] = []
    while True:
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            if not str(item.get("PK", "")).startswith("PRODUCT#"):
                continue
            if product_type and item.get("product_type") != product_type:
                continue
            if needle and needle not in str(item.get("manufacturer", "")).lower():
                continue
            if not item.get("datasheet_url"):
                continue
            if any(_needs(item)):
                out.append(item)
        if "LastEvaluatedKey" not in page:
            return out
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _spec_pages(rows: List[Dict[str, Any]]) -> List[int]:
    """Stored ``pages`` are 1-indexed; return 0-indexed, de-duplicated."""
    pages = set()
    for r in rows:
        for p in r.get("pages") or []:
            if isinstance(p, (int, Decimal)) and not isinstance(p, bool) and p >= 1:
                pages.add(int(p) - 1)
    return sorted(pages)


def drawing_page_scores(pdf: bytes) -> List[Dict[str, Any]]:
    """Per-page drawing scores for every page (PyMuPDF, under PDF_LOCK)."""
    with PDF_LOCK:
        _, scores = find_drawing_pages_scored(pdf, max_pages=10_000)
    return scores


def document_url(url: str) -> str:
    """The PDF a datasheet URL points at: ``#page=N`` fragments name pages of
    one document (ingest stores one URL per spec page), so rows from every
    fragment share one download, one page selection and one cache entry."""
    return url.split("#", 1)[0]


def _cache_path(url: str) -> Path:
    return CACHE_DIR / f"{hashlib.sha1(url.encode()).hexdigest()}.json"


def _read_cache(url: str) -> Optional[Dict[str, Any]]:
    """The current-version cache record for ``url``, else None."""
    try:
        rec = json.loads(_cache_path(url).read_text())
    except (OSError, ValueError):
        return None
    if isinstance(rec, dict) and rec.get("version") == CACHE_VERSION:
        return rec
    return None


def _is_cached(url: str) -> bool:
    return _read_cache(url) is not None


def extract_for_datasheet(
    url: str, rows: List[Dict[str, Any]], api_key: str, max_pages: int, refresh: bool
) -> Dict[str, Any]:
    """Return ``{"version", "url", "pages", "passes": {name: [...]}, "usage",
    "error", "cached"}``; read from / written to the cache."""
    path = _cache_path(url)
    cached = None if refresh else _read_cache(url)
    if cached is not None and cached.get("verifier") == VERIFIER_VERSION:
        return {**cached, "cached": True}
    if cached is not None:
        # Readings are still good; the text-layer check changed. Re-run it
        # against the PDF (no Gemini call).
        try:
            pdf = get_document(url)
            if not pdf:
                raise RuntimeError("empty or failed download")
            cached["passes"] = {
                name: reverify(pdf, cached.get("passes", {}).get(name, []))
                for name in PASSES
            }
            cached["verifier"] = VERIFIER_VERSION
            path.write_text(json.dumps(cached))
            return {**cached, "cached": True}
        except Exception as e:  # noqa: BLE001
            return {
                **cached,
                "passes": {},
                "error": f"reverify: {type(e).__name__}: {e}"[:500],
                "cached": True,
            }
    record: Dict[str, Any] = {
        "version": CACHE_VERSION,
        "url": url,
        "pages": [],
        "passes": {name: [] for name in PASSES},
        "usage": {"input": 0, "output": 0},
        "verifier": VERIFIER_VERSION,
        "error": None,
    }
    try:
        pdf = get_document(url)
        if not pdf:
            raise RuntimeError("empty or failed download")
        scores = drawing_page_scores(pdf)
        pages = select_envelope_pages(
            scores, _spec_pages(rows), max_pages, total_pages=len(scores)
        )
        record["pages"] = pages
        for name in PASSES if pages else ():
            found = extract_envelopes(
                pdf, pages, api_key, variant=name, usage=record["usage"]
            )
            record["passes"][name] = found
    except Exception as e:  # noqa: BLE001 — network / PDF / Gemini failures vary
        record["error"] = f"{type(e).__name__}: {e}"[:500]
    # Cache only clean outcomes (an empty extraction is clean). Download,
    # PDF and Gemini errors are retried on the next run.
    if record["error"] is None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record))
    return {**record, "cached": False}


def _parse_pass(
    extractions: Any,
) -> Tuple[List[Tuple[Any, str]], List[Tuple[Any, str]]]:
    """One reading → (dims entries, weight entries), each ``(designator,
    payload_json)`` with only verified values. Dims and weight are matched
    to rows separately: an outline drawing often labels a frame range (dims
    only) while the weight sits in a spec-table row for the exact model."""
    dims_out: List[Tuple[Any, str]] = []
    weight_out: List[Tuple[Any, str]] = []
    for raw in extractions if isinstance(extractions, list) else []:
        try:
            e = EnvelopeExtraction.model_validate(raw)
        except Exception:  # noqa: BLE001 — cached JSON from an older schema
            continue
        # Fail closed: a record without the check counts as all-unverified.
        unverified = raw.get("unverified")
        if not isinstance(unverified, list):
            unverified = list(VERIFIED_FIELDS)
        axes = {"length": e.length, "width": e.width, "height": e.height}
        # One unattributable axis discredits the reading's whole envelope
        # (it read some other column, or computed a value).
        if not any(v is not None and k in unverified for k, v in axes.items()):
            dims = canonical_dimensions(axes)
            if dims:
                dims_out.append((e.designator, json.dumps(dims, sort_keys=True)))
        if "weight" not in unverified:
            weight = canonical_weight(e.weight)
            if weight:
                weight_out.append((e.designator, json.dumps(weight, sort_keys=True)))
    return dims_out, weight_out


def _dims_size(payload: str) -> float:
    return max((a["value"] for a in json.loads(payload).values()), default=0.0)


def _weight_size(payload: str) -> float:
    return json.loads(payload)["value"]


def _dims_near(a: str, b: str) -> bool:
    return agreed_dims(json.loads(a), json.loads(b)) is not None


def _weight_near(a: str, b: str) -> bool:
    x, y = _weight_size(a), _weight_size(b)
    return abs(x - y) <= WEIGHT_CONFLICT_TOL * max(x, y)


def _match_pass(
    extractions: Any, rows: List[Dict[str, Any]]
) -> Dict[int, Tuple[Optional[dict], Optional[dict]]]:
    """Row index → (dims, weight) this reading assigns to it."""
    dims_entries, weight_entries = _parse_pass(extractions)
    out: Dict[int, Tuple[Optional[dict], Optional[dict]]] = {}
    for ri, ei in match_rows(
        dims_entries, rows, size_key=_dims_size, near=_dims_near
    ).items():
        out[ri] = (json.loads(dims_entries[ei][1]), None)
    for ri, ei in match_rows(
        weight_entries, rows, size_key=_weight_size, near=_weight_near
    ).items():
        out[ri] = (out.get(ri, (None, None))[0], json.loads(weight_entries[ei][1]))
    return out


def _pick_weight(weights: List[dict]) -> Optional[dict]:
    """First reading's weight, unless two readings conflict by > 10%."""
    if not weights:
        return None
    vals = [w["value"] for w in weights]
    if max(vals) - min(vals) > WEIGHT_CONFLICT_TOL * max(vals):
        return None
    return weights[0]


def plan_updates(
    rows: List[Dict[str, Any]], passes: List[Any]
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return ``(row, set_attrs)`` for every row the readings can enrich.

    ``passes`` is the independent readings in order (each a list of
    extraction dicts). Weight needs one reading; dimensions need two that
    agree (module docstring).
    """
    matched = [_match_pass(p, rows) for p in passes]
    updates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for ri, row in enumerate(rows):
        need_dims, need_weight = _needs(row)
        hits = [m[ri] for m in matched if ri in m]
        to_set: Dict[str, Any] = {}
        if need_dims and len(matched) >= 2 and ri in matched[0] and ri in matched[1]:
            dims = agreed_dims(matched[0][ri][0], matched[1][ri][0])
            # A partial envelope's max_dimension would understate the
            # product (a cylinder's stroke-dependent length left out).
            if dims and set(dims) == set(FULL_ENVELOPE):
                to_set["dimensions"] = dims
                to_set["max_dimension"] = {
                    "value": max(a["value"] for a in dims.values()),
                    "unit": "mm",
                }
        if need_weight:
            weight = _pick_weight([w for _, w in hits if w])
            if weight:
                to_set["weight"] = weight
        if to_set:
            updates.append((row, to_set))
    return updates


def apply_update(table: Any, row: Dict[str, Any], to_set: Dict[str, Any]) -> str:
    """Conditional enrich-only write. Returns "written" or "conflict"."""
    names: Dict[str, str] = {}
    values: Dict[str, Any] = {}
    sets: List[str] = []
    conds: List[str] = ["attribute_exists(PK)"]
    for attr, val in to_set.items():
        names[f"#{attr}"] = attr
        values[f":{attr}"] = to_dynamo(val)
        sets.append(f"#{attr} = :{attr}")
    # Guard the fields we fill: present-but-unusable legacy values (e.g. a
    # bare {"unit": "mm"}) are pinned to what we read; absent ones must
    # still be absent.
    for attr in ("dimensions", "weight"):
        if attr not in to_set:
            continue
        if attr in row:
            values[f":old_{attr}"] = row[attr]
            conds.append(f"#{attr} = :old_{attr}")
        else:
            conds.append(f"attribute_not_exists(#{attr})")
    try:
        table.update_item(
            Key={"PK": row["PK"], "SK": row["SK"]},
            UpdateExpression="SET " + ", ".join(sets),
            ConditionExpression=" AND ".join(conds),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        return "written"
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "conflict"
        raise


def run(args: argparse.Namespace) -> int:
    if args.apply and args.stage == "prod":
        logger.error(
            "refusing --apply against prod; prod data moves via `admin promote`"
        )
        return 1
    table = make_client(args.stage).table
    rows = scan_candidates(table, args.type, args.manufacturer)
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[document_url(r["datasheet_url"])].append(r)
    by_type = Counter(r.get("product_type") for r in rows)
    print(
        f"candidates: {len(rows)} rows across {len(groups)} datasheet PDFs "
        f"({', '.join(f'{k}={v}' for k, v in by_type.most_common())})"
    )
    urls = sorted(groups, key=lambda u: -len(groups[u]))
    if args.limit is not None:
        urls = urls[: max(args.limit, 0)]
    cached = sum(1 for u in urls if _is_cached(u))
    print(
        f"this run: {len(urls)} datasheets, {cached} cached, up to "
        f"{(len(urls) - cached) * -(-args.max_pages // 4) * len(PASSES)} Gemini calls "
        f"(max {args.max_pages} pages each, 4 per call, {len(PASSES)} readings)"
    )
    if args.plan:
        return 0

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not set")
        return 1

    stats: Counter = Counter()
    lock = threading.Lock()
    samples: List[str] = []
    report = []

    def work(url: str) -> None:
        rec = extract_for_datasheet(
            url, groups[url], api_key, args.max_pages, args.refresh
        )
        passes = [rec.get("passes", {}).get(name, []) for name in PASSES]
        updates = plan_updates(groups[url], passes)
        results = []
        for row, to_set in updates:
            outcome = apply_update(table, row, to_set) if args.apply else "planned"
            results.append(outcome)
        with lock:
            stats["datasheets"] += 1
            stats["errors"] += rec["error"] is not None
            stats["extractions"] += sum(len(p) for p in passes)
            if not rec.get("cached"):
                stats["tokens_in"] += rec["usage"]["input"]
                stats["tokens_out"] += rec["usage"]["output"]
            stats["rows_matched"] += len(updates)
            stats["dims_filled"] += sum("dimensions" in u[1] for u in updates)
            stats["weight_filled"] += sum("weight" in u[1] for u in updates)
            for o in results:
                stats[o] += 1
            report.append(
                {
                    "url": url,
                    "rows": len(groups[url]),
                    "pages": rec["pages"],
                    "extractions": [len(p) for p in passes],
                    "matched": len(updates),
                    "error": rec["error"],
                }
            )
            for row, to_set in updates[:1]:
                if len(samples) < 12:
                    samples.append(
                        f"  {row.get('manufacturer')} {row.get('part_number') or row.get('product_name')}: {to_set}"
                    )
            if stats["datasheets"] % 25 == 0:
                print(
                    f"  … {stats['datasheets']}/{len(urls)} datasheets, {stats['rows_matched']} rows matched",
                    flush=True,
                )

    with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as pool:
        futures = [pool.submit(work, u) for u in urls]
        for f in as_completed(futures):
            f.result()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    report_path = CACHE_DIR / f"report_{args.stage}.jsonl"
    report_path.write_text("".join(json.dumps(r) + "\n" for r in report))
    print("\n".join(samples))
    print(
        f"\ndatasheets={stats['datasheets']} errors={stats['errors']} "
        f"extractions={stats['extractions']} rows_matched={stats['rows_matched']} "
        f"dims_filled={stats['dims_filled']} weight_filled={stats['weight_filled']}"
    )
    cost = (
        stats["tokens_in"] * USD_PER_M_INPUT + stats["tokens_out"] * USD_PER_M_OUTPUT
    ) / 1e6
    print(
        f"gemini this run: {stats['tokens_in']:,} in + {stats['tokens_out']:,} out "
        f"tokens ≈ ${cost:.2f}"
    )
    if args.apply:
        print(f"written={stats['written']} skipped_concurrent_edit={stats['conflict']}")
    else:
        print("(dry run — re-run with --apply to write; extractions are cached)")
    print(f"per-datasheet report: {report_path}")
    return 0


def main(argv: Optional[List[str]] = None) -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "WARNING"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    p = argparse.ArgumentParser(
        prog="envelope-extract",
        description="Backfill product dimensions + weight from datasheet pages.",
    )
    p.add_argument("--stage", default="dev", choices=STAGES)
    p.add_argument("--type", default=None, help="Restrict to one product_type")
    p.add_argument("--manufacturer", default=None, help="Case-insensitive substring")
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max datasheets this run (largest groups first)",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=12,
        help="Pages sent per datasheet (default 12)",
    )
    p.add_argument("--workers", type=int, default=4)
    p.add_argument(
        "--plan", action="store_true", help="Count only: no downloads, no Gemini"
    )
    p.add_argument("--refresh", action="store_true", help="Ignore cached extractions")
    p.add_argument("--apply", action="store_true", help="Write (dev/staging only)")
    sys.exit(run(p.parse_args(argv)))


if __name__ == "__main__":
    main()
