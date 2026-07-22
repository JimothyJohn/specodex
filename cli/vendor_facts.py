"""Vendor-facts registry tooling (todo/COMMERCIAL.md Phase 4).

Two subcommands:

``validate``
    Load every ``data/vendors/*.json`` profile through the Pydantic
    loader, print per-vendor fact counts, exit 1 on any schema or
    citation violation. This is the gate — a fact without a valid
    citation must never load.

``verify``
    The replicability audit, not a gate. Re-fetch each citation URL
    (SSRF-checked via ``specodex.url_safety.validate_url``, robots.txt
    respected, rate-limited — the same polite-fetch machinery the
    pricing crawler uses) and confirm the recorded verbatim excerpt
    still appears in the page text (whitespace-normalized). Reports
    per fact: OK / STALE (page changed) / UNREACHABLE / MANUAL (PDF
    sources can't be text-diffed here — verify by hand). Exits 0 with
    a summary table; ``--strict`` exits 1 when anything is not OK.

Usage:
    ./Quickstart vendor-facts validate
    ./Quickstart vendor-facts verify [--vendor SLUG] [--no-cache] [--strict]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "outputs" / "vendor_facts_cache"

_OK = "OK"
_STALE = "STALE"
_UNREACHABLE = "UNREACHABLE"
_MANUAL = "MANUAL"


def _normalize_ws(text: str) -> str:
    """Collapse all whitespace (incl. NBSP) to single spaces for comparison."""
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def _html_to_text(html: str) -> str:
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    # Drop script/style noise so excerpts only match rendered text.
    for node in tree.css("script, style, noscript"):
        node.decompose()
    return tree.body.text(separator=" ") if tree.body else tree.text(separator=" ")


def _is_pdf_url(url: str) -> bool:
    from urllib.parse import urlparse

    return urlparse(url).path.lower().endswith(".pdf")


def cmd_validate(args: argparse.Namespace) -> int:
    from specodex.vendors.registry import VendorRegistryError, load_vendor_profiles

    try:
        profiles = load_vendor_profiles()
    except VendorRegistryError as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 1

    if not profiles:
        print("No vendor profiles found under data/vendors/.")
        return 0

    total = 0
    print(f"{'vendor':<24} {'facts':>5}  kinds")
    for slug, profile in sorted(profiles.items()):
        kinds = sorted({f.kind for f in profile.facts})
        total += len(profile.facts)
        print(f"{slug:<24} {len(profile.facts):>5}  {', '.join(kinds) or '-'}")
    print(f"\n{len(profiles)} vendor(s), {total} fact(s) — all citations valid.")
    return 0


def _iter_citations(
    profiles_values: Iterable,
) -> Iterable[Tuple[str, str, str, object]]:
    """Yield (slug, fact_label, url, citation) for every citation."""
    for profile in profiles_values:
        for fact in profile.facts:
            for src in fact.sources:
                yield profile.slug, fact.label, src.url or "", src


def cmd_verify(args: argparse.Namespace) -> int:
    from specodex.pricing.fetch import PriceFetcher
    from specodex.url_safety import UnsafeURLError, validate_url
    from specodex.vendors.registry import VendorRegistryError, load_vendor_profiles

    try:
        profiles = load_vendor_profiles()
    except VendorRegistryError as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 1

    values = [
        p for p in profiles.values() if args.vendor is None or p.slug == args.vendor
    ]
    if not values:
        print("No matching vendor profiles to verify.")
        return 0

    if args.no_cache:
        import shutil

        shutil.rmtree(CACHE_DIR, ignore_errors=True)

    rows: List[Tuple[str, str, str, str]] = []  # slug, label, status, detail
    page_text_cache: dict[str, Optional[str]] = {}

    with PriceFetcher(cache_dir=CACHE_DIR, allow_playwright=False) as fetcher:
        for slug, label, url, src in _iter_citations(values):
            if _is_pdf_url(url):
                rows.append(
                    (slug, label, _MANUAL, f"PDF source — verify by hand: {url}")
                )
                continue
            if url not in page_text_cache:
                try:
                    validate_url(url)
                    result = fetcher.fetch(url)
                except UnsafeURLError as e:
                    result = None
                    page_text_cache[url] = None
                    rows.append((slug, label, _UNREACHABLE, f"unsafe url: {e}"))
                    continue
                page_text_cache[url] = (
                    _normalize_ws(_html_to_text(result.html)) if result else None
                )
            page_text = page_text_cache[url]
            if page_text is None:
                rows.append((slug, label, _UNREACHABLE, url))
                continue
            excerpt = _normalize_ws(src.excerpt or "")
            if excerpt and excerpt in page_text:
                rows.append((slug, label, _OK, url))
            else:
                rows.append((slug, label, _STALE, f"excerpt not found on {url}"))

    print(f"{'vendor':<20} {'status':<12} fact")
    for slug, label, status, detail in rows:
        print(f"{slug:<20} {status:<12} {label}")
        if status != _OK:
            print(f"{'':<20} {'':<12}   ↳ {detail}")

    counts = {
        s: sum(1 for r in rows if r[2] == s)
        for s in (_OK, _STALE, _UNREACHABLE, _MANUAL)
    }
    print(
        f"\n{len(rows)} citation(s): {counts[_OK]} OK, {counts[_STALE]} STALE, "
        f"{counts[_UNREACHABLE]} UNREACHABLE, {counts[_MANUAL]} MANUAL"
    )
    if args.strict and (counts[_STALE] or counts[_UNREACHABLE]):
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vendor-facts",
        description="Validate and audit the citation-required vendor-facts registry.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="Load all profiles; exit 1 on any violation")

    p = sub.add_parser("verify", help="Re-fetch each citation and re-check excerpts")
    p.add_argument("--vendor", help="Only verify this vendor slug")
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Drop the fetch cache first (force live re-fetch)",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any citation is STALE or UNREACHABLE",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args)
    return cmd_verify(args)


if __name__ == "__main__":
    sys.exit(main())
