"""
Benchmark runner for the datasheet ingress pipeline.

Measures speed, cost, redundancy, and data quality for a set of
control datasheets against known ground-truth outputs.

Usage:
    ./Quickstart bench                      Page-finding + cached LLM diff (needs PDFs)
    ./Quickstart bench --live               Full pipeline (calls Gemini, needs PDFs)
    ./Quickstart bench --quality-only       Cache→expected diff only (no PDFs needed)
    ./Quickstart bench --filter j5-filtered Run a single fixture
    ./Quickstart bench --update-cache       Run live + save responses to cache
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
from typing import Any

log = logging.getLogger("bench")

ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_DIR = ROOT / "tests" / "benchmark"
FIXTURE_DIR = BENCHMARK_DIR / "datasheets"
EXPECTED_DIR = BENCHMARK_DIR / "expected"
CACHE_DIR = BENCHMARK_DIR / "cache"
OUTPUT_DIR = ROOT / "outputs" / "benchmarks"
BUDGETS_PATH = BENCHMARK_DIR / "budgets.json"

# A fixture exceeding any budget key by more than this fraction fails
# the run. 0.25 = 25% headroom on top of the recorded ceiling.
BUDGET_OVERSHOOT_TOLERANCE = 0.25

# Gemini token pricing (USD per 1M tokens). Update when models change.
# Numbers from ai.google.dev/gemini-api/docs/pricing.
TOKEN_PRICES: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


def _load_fixtures(filter_slug: str | None = None) -> list[dict[str, Any]]:
    manifest = BENCHMARK_DIR / "fixtures.json"
    with open(manifest) as f:
        fixtures = json.load(f)
    if filter_slug:
        fixtures = [fx for fx in fixtures if fx["slug"] == filter_slug]
        if not fixtures:
            log.error(f"No fixture matching --filter '{filter_slug}'")
            sys.exit(1)
    return fixtures


def _load_expected(filename: str) -> list[dict[str, Any]]:
    path = EXPECTED_DIR / filename
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _cache_path(slug: str, mode: str = "single") -> Path:
    """Cache file for an extraction mode. ``mode='single'`` (default)
    keeps the legacy ``<slug>.json`` path; ``mode='double_tap'`` adds
    a ``.double_tap`` suffix so the A/B harness can keep both."""
    suffix = "" if mode == "single" else f".{mode}"
    return CACHE_DIR / f"{slug}{suffix}.json"


def _load_cached_response(slug: str, mode: str = "single") -> dict[str, Any] | None:
    path = _cache_path(slug, mode)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _save_cached_response(
    slug: str, data: dict[str, Any], mode: str = "single"
) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(slug, mode)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _benchmark_page_finding(pdf_bytes: bytes) -> dict[str, Any]:
    """Run both old (binary) and new (scored) page detection and return metrics."""
    from specodex.page_finder import (
        find_spec_pages_by_text,
        find_spec_pages_scored,
    )

    try:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        doc.close()
    except ImportError:
        total_pages = -1

    t0 = time.perf_counter()
    old_pages = find_spec_pages_by_text(pdf_bytes)
    old_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    scored_pages, page_details = find_spec_pages_scored(pdf_bytes)
    scored_ms = (time.perf_counter() - t1) * 1000

    return {
        "total_pages": total_pages,
        "text_heuristic_pages": old_pages,
        "text_heuristic_count": len(old_pages),
        "scored_pages": scored_pages,
        "scored_count": len(scored_pages),
        "page_find_ms": round(old_ms, 1),
        "scored_find_ms": round(scored_ms, 1),
    }


def _filter_pdf_pages(pdf_bytes: bytes, pages: list[int]) -> bytes:
    """Extract specific pages from a PDF and return as new PDF bytes."""
    try:
        import fitz
    except ImportError:
        return pdf_bytes

    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    dst = fitz.open()
    for p in sorted(pages):
        if p < len(src):
            dst.insert_pdf(src, from_page=p, to_page=p)
    result = dst.tobytes()
    dst.close()
    src.close()
    return result


def _run_extraction(
    pdf_bytes: bytes,
    fixture: dict[str, Any],
    pages: list[int] | None,
    *,
    double_tap: bool = False,
) -> dict[str, Any]:
    """Run Gemini extraction and return metrics + raw response data.

    When ``double_tap=True``, routes through the verifier-loop runner
    so token counts include both passes. Otherwise the legacy single-
    pass call.
    """
    from specodex.config import MODEL, SCHEMA_CHOICES
    from specodex.llm import generate_content
    from specodex.utils import parse_gemini_response, validate_api_key

    api_key = validate_api_key(os.environ.get("GEMINI_API_KEY"))
    product_type = fixture["product_type"]
    context = {
        "product_name": fixture.get("product_name"),
        "manufacturer": fixture.get("manufacturer"),
        "product_family": fixture.get("product_family"),
    }

    if pages:
        sent_bytes = _filter_pdf_pages(pdf_bytes, pages)
    else:
        sent_bytes = pdf_bytes

    t0 = time.perf_counter()

    extra_telemetry: dict[str, Any] = {}
    raw_text = ""
    if double_tap:
        from specodex.double_tap.runner import (
            extract_with_recovery,
            extract_with_recovery_telemetry,
        )

        tokens: dict = {"input": 0, "output": 0}
        result = extract_with_recovery(
            sent_bytes, api_key, product_type, context, "pdf", tokens=tokens
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        input_tokens = tokens["input"]
        output_tokens = tokens["output"]
        extracted = [m.model_dump(mode="json") for m in result.products]
        extra_telemetry = extract_with_recovery_telemetry(result)
    else:
        response = generate_content(sent_bytes, api_key, product_type, context, "pdf")
        elapsed_ms = (time.perf_counter() - t0) * 1000

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
        output_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
        raw_text = response.text if hasattr(response, "text") else ""

        parsed = parse_gemini_response(
            response, SCHEMA_CHOICES[product_type], product_type, context
        )
        extracted = [m.model_dump(mode="json") for m in parsed]

    prices = TOKEN_PRICES.get(MODEL, {"input": 0.0, "output": 0.0})
    cost_usd = (
        input_tokens * prices["input"] / 1_000_000
        + output_tokens * prices["output"] / 1_000_000
    )

    out = {
        "model": MODEL,
        "extraction_ms": round(elapsed_ms, 1),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
        "pdf_bytes_sent": len(sent_bytes),
        "variants_extracted": len(extracted),
        "extracted": extracted,
        "raw_response": raw_text,
    }
    out.update(extra_telemetry)
    return out


def _normalize_value(v: Any) -> Any:
    """Normalize a field value for comparison — handles the ;-separated format."""
    if v is None:
        return None
    if isinstance(v, str) and ";" in v:
        parts = v.split(";", 1)
        try:
            return (float(parts[0]), parts[1].strip().lower())
        except ValueError:
            return v.lower().strip()
    if isinstance(v, dict):
        if "value" in v and "unit" in v:
            return (float(v["value"]), v["unit"].lower())
        if "min" in v and "max" in v and "unit" in v:
            return (float(v["min"]), float(v["max"]), v["unit"].lower())
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return v.lower().strip()
    return v


def _compare_products(
    extracted: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    meta_fields: set[str] | None = None,
) -> dict[str, Any]:
    """Compare extracted vs expected products, returning quality metrics."""
    if meta_fields is None:
        meta_fields = {
            "product_id",
            "product_type",
            "product_name",
            "product_family",
            "manufacturer",
            "PK",
            "SK",
            "datasheet_url",
            "pages",
        }

    if not expected:
        return {
            "status": "no_ground_truth",
            "expected_variants": 0,
            "extracted_variants": len(extracted),
            "fields_checked": 0,
            "fields_match": 0,
            "fields_missing": 0,
            "fields_wrong": 0,
            "fields_extra": 0,
            "precision": None,
            "recall": None,
            "details": [],
        }

    # Match extracted to expected by part_number when possible
    exp_by_pn: dict[str, dict] = {}
    exp_unmatched: list[dict] = []
    for e in expected:
        pn = (e.get("part_number") or "").strip()
        if pn:
            exp_by_pn[pn.lower()] = e
        else:
            exp_unmatched.append(e)

    details: list[dict[str, Any]] = []
    total_checked = 0
    total_match = 0
    total_missing = 0
    total_wrong = 0

    for ext in extracted:
        ext_pn = (ext.get("part_number") or "").strip().lower()
        matched_exp = exp_by_pn.pop(ext_pn, None) if ext_pn else None
        if matched_exp is None and exp_unmatched:
            matched_exp = exp_unmatched.pop(0)

        if matched_exp is None:
            details.append(
                {
                    "part_number": ext.get("part_number"),
                    "status": "extra_variant",
                }
            )
            continue

        field_results: dict[str, str] = {}
        spec_fields = [f for f in matched_exp.keys() if f not in meta_fields]

        for field in spec_fields:
            exp_val = matched_exp.get(field)
            ext_val = ext.get(field)

            if exp_val is None:
                continue

            total_checked += 1
            norm_exp = _normalize_value(exp_val)
            norm_ext = _normalize_value(ext_val)

            if ext_val is None:
                field_results[field] = "missing"
                total_missing += 1
            elif norm_exp == norm_ext:
                field_results[field] = "match"
                total_match += 1
            elif isinstance(norm_exp, tuple) and isinstance(norm_ext, tuple):
                # Numeric with unit — check value within 5% tolerance
                if len(norm_exp) == len(norm_ext) and len(norm_exp) >= 2:
                    vals_close = all(
                        abs(a - b) <= 0.05 * max(abs(a), 1e-9)
                        for a, b in zip(norm_exp[:-1], norm_ext[:-1])
                        if isinstance(a, (int, float)) and isinstance(b, (int, float))
                    )
                    if vals_close:
                        field_results[field] = "match"
                        total_match += 1
                    else:
                        field_results[field] = (
                            f"wrong (got={ext_val}, expected={exp_val})"
                        )
                        total_wrong += 1
                else:
                    field_results[field] = f"wrong (got={ext_val}, expected={exp_val})"
                    total_wrong += 1
            else:
                field_results[field] = f"wrong (got={ext_val}, expected={exp_val})"
                total_wrong += 1

        details.append(
            {
                "part_number": ext.get("part_number"),
                "fields": field_results,
            }
        )

    unmatched_expected = len(exp_by_pn) + len(exp_unmatched)

    precision = (
        total_match / (total_match + total_wrong)
        if (total_match + total_wrong) > 0
        else None
    )
    recall = total_match / total_checked if total_checked > 0 else None

    return {
        "status": "compared",
        "expected_variants": len(expected),
        "extracted_variants": len(extracted),
        "matched_variants": len(extracted)
        - sum(1 for d in details if d.get("status") == "extra_variant"),
        "unmatched_expected": unmatched_expected,
        "fields_checked": total_checked,
        "fields_match": total_match,
        "fields_missing": total_missing,
        "fields_wrong": total_wrong,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "details": details,
    }


def _print_table(results: list[dict[str, Any]]) -> None:
    """Print a compact results table to stderr."""
    header = (
        f"{'Fixture':<28} {'Pages':>6} {'Old':>5} {'New':>5} "
        f"{'Sent KB':>8} {'LLM ms':>8} {'Tokens':>10} {'Cost':>8} {'P/R':>12}"
    )
    print(header, file=sys.stderr)
    print("-" * len(header), file=sys.stderr)

    for r in results:
        slug = r["slug"][:27]
        pf = r.get("page_finding", {})
        total_pages = pf.get("total_pages", "?")
        old_count = pf.get("text_heuristic_count", "?")
        new_count = pf.get("scored_count", "?")

        ext = r.get("extraction", {})
        sent_kb = (
            f"{ext.get('pdf_bytes_sent', 0) / 1024:.0f}"
            if ext.get("pdf_bytes_sent")
            else "-"
        )
        llm_ms = (
            f"{ext.get('extraction_ms', 0):.0f}" if ext.get("extraction_ms") else "-"
        )
        tokens = ext.get("input_tokens", 0) + ext.get("output_tokens", 0)
        tokens_str = f"{tokens:,}" if tokens else "-"
        cost = f"${ext.get('cost_usd', 0):.4f}" if ext.get("cost_usd") else "-"

        q = r.get("quality", {})
        prec = q.get("precision")
        rec = q.get("recall")
        pr_str = (
            f"{prec:.0%}/{rec:.0%}" if prec is not None and rec is not None else "-"
        )

        print(
            f"{slug:<28} {str(total_pages):>6} {str(old_count):>5} {str(new_count):>5} "
            f"{sent_kb:>8} {llm_ms:>8} {tokens_str:>10} {cost:>8} {pr_str:>12}",
            file=sys.stderr,
        )


def run(
    *,
    live: bool = False,
    filter_slug: str | None = None,
    update_cache: bool = False,
    quality_only: bool = False,
    double_tap: bool = False,
) -> list[dict[str, Any]]:
    """Run benchmarks and return results list.

    quality_only=True skips PDF reads and page-finding; only diffs cached
    extraction output against expected. Lets the run work in environments
    where source PDFs aren't available (e.g. fresh remote checkouts), at
    the cost of skipping page-finder and live-extraction signal.

    double_tap=True routes live extraction through the verifier-loop
    runner and reads/writes the ``<slug>.double_tap.json`` cache file
    so the A/B harness can compare both modes.
    """
    if quality_only and live:
        raise ValueError("--quality-only and --live are mutually exclusive")
    cache_mode = "double_tap" if double_tap else "single"

    fixtures = _load_fixtures(filter_slug)
    results: list[dict[str, Any]] = []

    for fixture in fixtures:
        slug = fixture["slug"]
        result: dict[str, Any] = {
            "slug": slug,
            "pdf": fixture["pdf"],
            "product_type": fixture["product_type"],
        }

        if quality_only:
            log.info(f"Quality-only: {slug}")
        else:
            pdf_path = FIXTURE_DIR / fixture["pdf"]
            if not pdf_path.exists():
                log.warning(f"Skipping {slug}: {pdf_path} not found")
                continue

            log.info(f"Benchmarking: {slug}")
            pdf_bytes = pdf_path.read_bytes()
            result["pdf_bytes_total"] = len(pdf_bytes)

            # Phase 1: page finding (always runs, no API call)
            pf = _benchmark_page_finding(pdf_bytes)
            result["page_finding"] = pf
            spec_pages = (
                pf["scored_pages"]
                if pf.get("scored_pages")
                else pf["text_heuristic_pages"]
            )
            redundancy = (
                1.0 - (len(spec_pages) / pf["total_pages"])
                if pf["total_pages"] > 0
                else 0.0
            )
            result["redundancy_ratio"] = round(redundancy, 4)

        # Phase 2: LLM extraction
        extraction: dict[str, Any] = {}
        if live:
            try:
                extraction = _run_extraction(
                    pdf_bytes,
                    fixture,
                    spec_pages or None,
                    double_tap=double_tap,
                )
                if update_cache:
                    cache_payload = {
                        "extracted": extraction["extracted"],
                        "raw_response": extraction.get("raw_response", ""),
                        "input_tokens": extraction["input_tokens"],
                        "output_tokens": extraction["output_tokens"],
                        "model": extraction["model"],
                    }
                    # Carry the double_tap_* telemetry into the cache so
                    # offline --ab runs can re-derive probe / recovery info.
                    for k, v in extraction.items():
                        if k.startswith("double_tap_"):
                            cache_payload[k] = v
                    _save_cached_response(slug, cache_payload, mode=cache_mode)
            except Exception as e:
                log.error(f"Extraction failed for {slug}: {e}")
                extraction = {"error": str(e)}
        else:
            cached = _load_cached_response(slug, mode=cache_mode)
            if cached:
                extraction = {
                    "from_cache": True,
                    "variants_extracted": len(cached.get("extracted", [])),
                    "extracted": cached.get("extracted", []),
                    "input_tokens": cached.get("input_tokens", 0),
                    "output_tokens": cached.get("output_tokens", 0),
                    "model": cached.get("model", "unknown"),
                }
                for k, v in cached.items():
                    if k.startswith("double_tap_"):
                        extraction[k] = v

        result["extraction"] = extraction

        # Phase 3: quality comparison
        expected = _load_expected(fixture.get("expected", f"{slug}.json"))
        extracted = extraction.get("extracted", [])
        if extracted:
            result["quality"] = _compare_products(extracted, expected)
        elif expected:
            result["quality"] = {
                "status": "no_extraction",
                "expected_variants": len(expected),
                "extracted_variants": 0,
            }
        else:
            result["quality"] = {"status": "no_ground_truth"}

        results.append(result)

    return results


def _load_budgets() -> dict[str, dict[str, float]]:
    """Read the per-fixture wall-clock ceilings, ignoring any underscore keys."""
    if not BUDGETS_PATH.exists():
        return {}
    with open(BUDGETS_PATH) as f:
        data = json.load(f)
    return {
        k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)
    }


def _check_budgets(
    results: list[dict[str, Any]],
    budgets: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """Compare each fixture's wall-clock numbers against its budget.

    Returns a list of overshoot records: ``{slug, metric, actual_ms,
    budget_ms, overshoot_pct}``. Empty list = all fixtures within tolerance.

    A metric is checked only when (a) the fixture has a budget for it and
    (b) the run produced an actual value. Offline runs (no ``--live``)
    naturally skip ``llm_extract_ms`` because no extraction ran.
    """
    overshoots: list[dict[str, Any]] = []
    threshold = 1.0 + BUDGET_OVERSHOOT_TOLERANCE

    for result in results:
        slug = result.get("slug")
        budget = budgets.get(slug or "")
        if not budget:
            continue

        actuals = {
            "page_find_ms": result.get("page_finding", {}).get("page_find_ms"),
            "llm_extract_ms": result.get("extraction", {}).get("extraction_ms"),
        }
        for metric, actual in actuals.items():
            ceiling = budget.get(metric)
            if ceiling is None or actual is None:
                continue
            if actual > ceiling * threshold:
                overshoots.append(
                    {
                        "slug": slug,
                        "metric": metric,
                        "actual_ms": actual,
                        "budget_ms": ceiling,
                        "overshoot_pct": round((actual / ceiling - 1.0) * 100, 1),
                    }
                )

    return overshoots


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="bench",
        description="Benchmark the datasheet ingress pipeline.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run live Gemini extraction (requires GEMINI_API_KEY)",
    )
    parser.add_argument(
        "--filter",
        dest="filter_slug",
        default=None,
        help="Run only the fixture matching this slug",
    )
    parser.add_argument(
        "--update-cache",
        action="store_true",
        help="Save live extraction responses to cache for future offline runs",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Write results to a specific JSON file",
    )
    parser.add_argument(
        "--no-enforce-budgets",
        action="store_true",
        help="Skip the wall-clock budget check (still records timings)",
    )
    parser.add_argument(
        "--quality-only",
        action="store_true",
        help="Skip page-finding; diff cached extraction against expected only "
        "(no source PDFs needed; mutually exclusive with --live)",
    )
    parser.add_argument(
        "--double-tap",
        action="store_true",
        help=(
            "Route live extraction through the verifier-loop runner. "
            "Reads/writes the <slug>.double_tap.json cache so the A/B "
            "harness can compare both modes."
        ),
    )
    parser.add_argument(
        "--ab",
        action="store_true",
        help=(
            "Run BOTH single-pass and double-tap modes and emit a per-"
            "fixture comparison table. Uses cached responses when "
            "available; pair with --live to populate the cache. The "
            "table prints to stderr and the comparison list is written "
            "to outputs/benchmarks/ab/<timestamp>.json."
        ),
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.ab:
        # A/B mode: run BOTH single-pass and double-tap, emit comparison.
        from cli.bench_ab import compare_results, format_ab_table

        single_results = run(
            live=args.live,
            filter_slug=args.filter_slug,
            update_cache=args.update_cache,
            quality_only=args.quality_only,
            double_tap=False,
        )
        double_results = run(
            live=args.live,
            filter_slug=args.filter_slug,
            update_cache=args.update_cache,
            quality_only=args.quality_only,
            double_tap=True,
        )

        # Pair fixtures by slug; missing-pair entries get logged and skipped.
        single_by_slug = {r["slug"]: r for r in single_results}
        double_by_slug = {r["slug"]: r for r in double_results}
        common = sorted(set(single_by_slug) & set(double_by_slug))
        comparisons = [
            compare_results(single_by_slug[s], double_by_slug[s]) for s in common
        ]

        print(format_ab_table(comparisons), file=sys.stderr)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ab_dir = OUTPUT_DIR / "ab"
        ab_dir.mkdir(parents=True, exist_ok=True)
        ab_path = args.output or ab_dir / f"{ts}.json"
        ab_payload = {
            "timestamp": ts,
            "comparisons": [
                {
                    "slug": c.slug,
                    "single_recall": c.single_recall,
                    "double_recall": c.double_recall,
                    "single_precision": c.single_precision,
                    "double_precision": c.double_precision,
                    "single_tokens": c.single_tokens,
                    "double_tokens": c.double_tokens,
                    "delta_recall_pp": c.delta_recall_pp,
                    "delta_precision_pp": c.delta_precision_pp,
                    "delta_tokens_pct": c.delta_tokens_pct,
                    "delta_cost_usd": c.delta_cost_usd,
                    "probes_fired": c.probes_fired,
                    "fields_recovered": c.fields_recovered,
                    "fields_corrected": c.fields_corrected,
                    "worth_it": c.worth_it,
                }
                for c in comparisons
            ],
        }
        ab_path.write_text(json.dumps(ab_payload, indent=2, default=str))
        log.info(f"A/B comparison written to {ab_path}")
        return

    results = run(
        live=args.live,
        filter_slug=args.filter_slug,
        update_cache=args.update_cache,
        quality_only=args.quality_only,
        double_tap=args.double_tap,
    )

    _print_table(results)

    # Write output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output or OUTPUT_DIR / f"{ts}.json"
    latest_path = OUTPUT_DIR / "latest.json"

    report = {
        "timestamp": ts,
        "live": args.live,
        "double_tap": args.double_tap,
        "fixtures": results,
    }
    output_path.write_text(json.dumps(report, indent=2, default=str))
    latest_path.write_text(json.dumps(report, indent=2, default=str))

    log.info(f"Results written to {output_path}")
    log.info(f"Latest symlink: {latest_path}")

    # Budget enforcement — surfaces perf regressions immediately rather
    # than letting them slip into a release. Default-on; opt out with
    # --no-enforce-budgets when calibrating new fixtures.
    overshoots: list[dict[str, Any]] = []
    if not args.no_enforce_budgets:
        budgets = _load_budgets()
        if budgets:
            overshoots = _check_budgets(results, budgets)
            for o in overshoots:
                log.error(
                    "BUDGET FAIL: %s/%s = %.0fms (budget %.0fms, +%.1f%%)",
                    o["slug"],
                    o["metric"],
                    o["actual_ms"],
                    o["budget_ms"],
                    o["overshoot_pct"],
                )

    # Exit non-zero if any extraction errored or budget exceeded
    if any(r.get("extraction", {}).get("error") for r in results) or overshoots:
        sys.exit(1)


if __name__ == "__main__":
    main()
