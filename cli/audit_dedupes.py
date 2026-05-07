"""DEDUPE — scan dev DB for prefix-drift duplicates and (optionally) merge.

Phase 1 is read-only: groups rows by (manufacturer, family-aware normalized
core) using the same strip rule as `compute_product_id` (so the audit's
notion of "same product" matches what `compute_product_id` would now write).

Emits two artifacts per run:

- ``outputs/dedupe_audit_<stage>_<ts>.json`` — every group with >= 2 rows.
  For each group, classifies every populated field as identical /
  complementary / conflicting and suggests an action (``merge``,
  ``review``, ``delete-junk``).
- ``outputs/dedupe_review_<stage>_<ts>.md`` — human-review queue: one
  section per ``review`` group with a 3-column table of the disagreeing
  fields, one row per source.

Phase 2 (``--apply --safe-only``) auto-merges the ``merge``-action groups
on a live dev table: writes the merged canonical row under the
family-aware product_id, then deletes the orphans. Per todo/DEDUPE.md.
Refuses to run without ``--stage dev`` and without ``--apply``.

Usage:
    uv run python -m cli.audit_dedupes --stage dev
    uv run python -m cli.audit_dedupes --stage dev --output /tmp/audit.json
    uv run python -m cli.audit_dedupes --rows tests/fixtures/sample.json  # offline
    uv run python -m cli.audit_dedupes --stage dev --apply --safe-only --dry-run
    uv run python -m cli.audit_dedupes --stage dev --apply --safe-only --yes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from specodex.ids import _strip_family_prefix, compute_product_id, normalize_string

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs"

log = logging.getLogger("audit_dedupes")

# Fields that are bookkeeping or identity, not part of the product's spec —
# diffs on these don't drive the merge classification. Three buckets:
#   - storage metadata: PK, SK, product_id, id, type, created_at, updated_at
#   - provenance: pages, datasheet_url, source_url, extraction_id
#   - identity (we GROUP on family-aware core, so prefix-drifted
#     part_number/product_name/product_family are *expected* to differ —
#     that's the whole point of the audit)
NON_SPEC_FIELDS = frozenset(
    {
        "PK",
        "SK",
        "product_id",
        "id",
        "_id",
        "type",
        "created_at",
        "updated_at",
        "ingested_at",
        "pages",
        "datasheet_url",
        "source_url",
        "extraction_id",
        "manufacturer",
        "part_number",
        "product_name",
        "product_family",
        "product_type",
    }
)

# Placeholder/junk part-number patterns — rows that look like extraction
# noise and are candidates for `delete-junk` rather than merge. Kept short
# and conservative; the operator decides per group.
JUNK_PART_NUMBER_PATTERNS = (
    "unknown",
    "n/a",
    "tbd",
    "placeholder",
    "see spec",
)


# ── Pure functions (testable without DynamoDB) ─────────────────────────


def family_aware_core(part_number: str | None, product_family: str | None) -> str:
    """Return the normalized core of a part number after stripping the family.

    Uses the same `_strip_family_prefix` rule that `compute_product_id`
    applies on write — so the audit groups rows the same way the new ID
    function would have collapsed them.
    """
    norm_pn = normalize_string(part_number)
    norm_family = normalize_string(product_family)
    if norm_pn and norm_family:
        return _strip_family_prefix(norm_pn, norm_family)
    return norm_pn


def is_junk_part_number(pn: str | None) -> bool:
    """Match the conservative placeholder patterns — operator-confirmable."""
    if not pn:
        return True
    pn_l = pn.lower().strip()
    return any(token in pn_l for token in JUNK_PART_NUMBER_PATTERNS)


def group_rows(rows: Iterable[dict]) -> dict[tuple[str, str], list[dict]]:
    """Group rows by (manufacturer_norm, family_aware_core).

    Rows missing both manufacturer and a usable part_number are skipped —
    they wouldn't have collapsed under the new ID rule either.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        mfg = normalize_string(row.get("manufacturer"))
        core = family_aware_core(row.get("part_number"), row.get("product_family"))
        if not mfg or not core:
            continue
        groups.setdefault((mfg, core), []).append(row)
    return groups


def _spec_keys(rows: list[dict]) -> set[str]:
    """Union of populated spec-field keys across the group."""
    keys: set[str] = set()
    for row in rows:
        for k, v in row.items():
            if k in NON_SPEC_FIELDS or k.startswith("_"):
                continue
            if v is None or v == "" or v == [] or v == {}:
                continue
            keys.add(k)
    return keys


def _values_for(rows: list[dict], field: str) -> list[Any]:
    """Field values across rows; None for absent/null/empty."""
    out: list[Any] = []
    for row in rows:
        v = row.get(field)
        if v is None or v == "" or v == [] or v == {}:
            out.append(None)
        else:
            out.append(v)
    return out


def classify_field(values: list[Any]) -> str:
    """Return ``identical`` / ``complementary`` / ``conflicting`` for a field.

    - All non-null and equal → ``identical``.
    - Some null, the rest equal (or just one non-null) → ``complementary``.
    - Two or more distinct non-null values → ``conflicting``.
    """
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "identical"  # vacuously safe
    distinct = {json.dumps(v, sort_keys=True, default=str) for v in non_null}
    if len(distinct) > 1:
        return "conflicting"
    if len(non_null) == len(values):
        return "identical"
    return "complementary"


def suggest_action(classifications: dict[str, str], rows: list[dict]) -> str:
    """Pick `merge`, `review`, or `delete-junk` for a group.

    - ``delete-junk`` if every part_number in the group is a placeholder
      pattern AND there's at least one neighbor with a real part number
      under the same core (rare — the placeholder rows are extraction noise).
    - ``review`` if any classification is ``conflicting``.
    - ``merge`` otherwise.
    """
    pns = [r.get("part_number") for r in rows]
    junky = [is_junk_part_number(p) for p in pns]
    if any(junky) and not all(junky):
        return "delete-junk"
    if any(c == "conflicting" for c in classifications.values()):
        return "review"
    return "merge"


def diff_group(rows: list[dict]) -> dict[str, str]:
    """Per-field classification dict for a group of >= 2 rows."""
    return {
        field: classify_field(_values_for(rows, field)) for field in _spec_keys(rows)
    }


def audit(rows: Iterable[dict]) -> list[dict[str, Any]]:
    """Return a list of group reports with diffs + suggested action.

    Only groups with >= 2 rows are included — the singletons are exactly
    what we want (one canonical row per product).
    """
    groups = group_rows(rows)
    reports: list[dict[str, Any]] = []
    for (mfg, core), group_rows_list in sorted(groups.items()):
        if len(group_rows_list) < 2:
            continue
        classifications = diff_group(group_rows_list)
        action = suggest_action(classifications, group_rows_list)

        # Family mismatch is a special signal — same normalized core, two
        # different `product_family` values. The new ID rule would still
        # collapse these (`product_family` only feeds the strip), but the
        # operator needs to know before merging.
        families = sorted(
            {
                str(r.get("product_family") or "")
                for r in group_rows_list
                if r.get("product_family")
            }
        )
        family_mismatch = len(families) > 1
        if family_mismatch and action == "merge":
            action = "review"  # demote to manual

        reports.append(
            {
                "manufacturer": mfg,
                "normalized_core": core,
                "row_count": len(group_rows_list),
                "rows": [
                    {
                        "PK": r.get("PK"),
                        "SK": r.get("SK"),
                        "product_id": r.get("product_id") or r.get("id"),
                        "part_number": r.get("part_number"),
                        "product_family": r.get("product_family"),
                        "datasheet_url": r.get("datasheet_url") or r.get("source_url"),
                    }
                    for r in group_rows_list
                ],
                "field_classifications": classifications,
                "family_mismatch": family_mismatch,
                "suggested_action": action,
            }
        )
    return reports


# ── Rendering ──────────────────────────────────────────────────────────


def render_review_md(reports: list[dict[str, Any]]) -> str:
    """Markdown review queue for the `review` and `delete-junk` groups.

    Reviewer reads top-down, picks per disagreeing field, runs Phase 3
    `--from-review` (not in this PR).
    """
    review = [r for r in reports if r["suggested_action"] != "merge"]
    lines: list[str] = [
        "# DEDUPE review queue",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        f"Total groups needing review: **{len(review)}** "
        f"(out of {len(reports)} groups with >= 2 rows)",
        "",
    ]
    if not review:
        lines.append("✅ Nothing to review. All duplicate groups can auto-merge.")
        return "\n".join(lines) + "\n"
    for i, r in enumerate(review, 1):
        lines.extend(
            [
                f"## {i}. `{r['manufacturer']}` / `{r['normalized_core']}` "
                f"— {r['suggested_action']}",
                "",
                f"- {r['row_count']} rows in this group"
                + (" · ⚠ family mismatch" if r["family_mismatch"] else ""),
                "",
                "### Source rows",
                "",
                "| # | part_number | product_family | datasheet_url |",
                "|---|---|---|---|",
            ]
        )
        for j, row in enumerate(r["rows"], 1):
            url = row.get("datasheet_url") or ""
            url_md = f"[link]({url})" if url else "—"
            lines.append(
                f"| {j} | `{row.get('part_number') or ''}` | "
                f"`{row.get('product_family') or ''}` | {url_md} |"
            )
        conflicting = [
            f for f, c in r["field_classifications"].items() if c == "conflicting"
        ]
        if conflicting:
            lines.extend(
                [
                    "",
                    "### Conflicting fields",
                    "",
                    "| field | "
                    + " | ".join(f"row {i + 1}" for i in range(r["row_count"]))
                    + " |",
                    "|---|" + "|".join(["---"] * r["row_count"]) + "|",
                ]
            )
        lines.append("")
    return "\n".join(lines) + "\n"


# ── Phase 2 — safe-merge logic (pure, testable) ────────────────────────


def _populated_field_count(row: dict) -> int:
    """Count populated (non-empty) keys, excluding bookkeeping fields."""
    n = 0
    for k, v in row.items():
        if k in NON_SPEC_FIELDS or k.startswith("_"):
            continue
        if v is None or v == "" or v == [] or v == {}:
            continue
        n += 1
    return n


def pick_canonical_part_number(rows: list[dict]) -> str | None:
    """Pick the canonical part number form from a group.

    Tie-break order: longest form first (``MPP-1152C`` beats ``MPP1152C``
    beats ``1152C`` — the variant carrying the most punctuation/prefix
    is the most informative one to preserve), then alphabetical for
    determinism.
    """
    pns = [r.get("part_number") for r in rows if r.get("part_number")]
    if not pns:
        return None
    return sorted(pns, key=lambda s: (-len(str(s)), str(s)))[0]


def _pick_canonical_str(rows: list[dict], field: str) -> str | None:
    """Pick the longest non-empty value for a string field (deterministic tie-break)."""
    vals = [r.get(field) for r in rows if r.get(field)]
    if not vals:
        return None
    return sorted(vals, key=lambda s: (-len(str(s)), str(s)))[0]


def _union_pages(rows: list[dict]) -> list[int] | None:
    """Union of all rows' ``pages`` lists, sorted, ints only."""
    seen: set[int] = set()
    for r in rows:
        for p in r.get("pages") or []:
            try:
                seen.add(int(p))
            except (TypeError, ValueError):
                continue
    return sorted(seen) if seen else None


def _pick_datasheet_url(rows: list[dict]) -> str | None:
    """Pick the datasheet_url from the row with the most populated fields."""
    candidates = [r for r in rows if r.get("datasheet_url")]
    if not candidates:
        return None
    candidates.sort(
        key=lambda r: (-_populated_field_count(r), str(r.get("datasheet_url")))
    )
    return candidates[0].get("datasheet_url")


def merge_safe_group(rows: list[dict]) -> dict:
    """Merge a safe (``merge``-action) group into one canonical row dict.

    Per todo/DEDUPE.md Phase 2:
    - Per spec field, take the non-null value. Equal non-null collapses;
      callers must have excluded ``conflicting`` groups before calling.
    - ``part_number`` uses the most-populated form (longest wins).
    - ``pages`` is the union of all rows' page lists.
    - ``datasheet_url`` is taken from the row with the most populated fields.
    - PK / SK / product_id are recomputed from the canonical
      (manufacturer, family-aware part_number) so future ingests upsert.
    """
    if not rows:
        raise ValueError("merge_safe_group requires at least one row")

    canonical_part = pick_canonical_part_number(rows)
    canonical_family = _pick_canonical_str(rows, "product_family")
    canonical_name = _pick_canonical_str(rows, "product_name")
    manufacturer = next(
        (r.get("manufacturer") for r in rows if r.get("manufacturer")), None
    )
    product_type = next(
        (r.get("product_type") for r in rows if r.get("product_type")), None
    )

    merged: dict[str, Any] = {}
    spec_keys: set[str] = set()
    for r in rows:
        for k, v in r.items():
            if k in NON_SPEC_FIELDS or k.startswith("_"):
                continue
            if v is None or v == "" or v == [] or v == {}:
                continue
            spec_keys.add(k)

    for field in spec_keys:
        for r in rows:
            v = r.get(field)
            if v is None or v == "" or v == [] or v == {}:
                continue
            merged[field] = v
            break

    if manufacturer:
        merged["manufacturer"] = manufacturer
    if product_type:
        merged["product_type"] = product_type
    if canonical_part:
        merged["part_number"] = canonical_part
    if canonical_family:
        merged["product_family"] = canonical_family
    if canonical_name:
        merged["product_name"] = canonical_name

    pages = _union_pages(rows)
    if pages is not None:
        merged["pages"] = pages

    ds_url = _pick_datasheet_url(rows)
    if ds_url:
        merged["datasheet_url"] = ds_url

    new_id = compute_product_id(
        manufacturer=manufacturer or "",
        part_number=canonical_part,
        product_name=canonical_name,
        product_family=canonical_family,
    )
    if new_id is None:
        # Fall back to an existing row's id — better to keep one of the
        # source rows than to lose the data because the family-aware ID
        # couldn't be computed (rare; means manufacturer is empty).
        existing_id = next(
            (
                r.get("product_id") or r.get("id")
                for r in rows
                if r.get("product_id") or r.get("id")
            ),
            None,
        )
        if existing_id is None:
            raise ValueError("merge_safe_group: cannot derive product_id for group")
        merged["product_id"] = str(existing_id)
    else:
        merged["product_id"] = str(new_id)

    if product_type:
        merged["PK"] = f"PRODUCT#{product_type.upper()}"
        merged["SK"] = f"PRODUCT#{merged['product_id']}"
    else:
        # Unknown product_type means we can't safely write a PK/SK; let
        # apply_safe_merges skip the group rather than write a malformed row.
        existing_pk = next((r.get("PK") for r in rows if r.get("PK")), None)
        if existing_pk:
            merged["PK"] = existing_pk
            merged["SK"] = f"PRODUCT#{merged['product_id']}"

    return merged


def plan_safe_merges(
    reports: list[dict[str, Any]], rows_by_pksk: dict[tuple[str, str], dict]
) -> list[dict[str, Any]]:
    """Build a write-plan for the ``merge``-action groups.

    Each plan entry has:
    - ``group``: the original audit report entry
    - ``merged``: the canonical row dict that will be written
    - ``deletes``: list of (PK, SK) tuples to delete after the put
      (the orphan rows whose SK differs from the canonical SK)

    Pure function — no DB calls. Tested directly.
    """
    plan: list[dict[str, Any]] = []
    for group in reports:
        if group.get("suggested_action") != "merge":
            continue

        full_rows: list[dict] = []
        for row_ref in group.get("rows", []):
            pk, sk = row_ref.get("PK"), row_ref.get("SK")
            if pk is None or sk is None:
                continue
            full = rows_by_pksk.get((pk, sk))
            if full is not None:
                full_rows.append(full)
        if len(full_rows) < 2:
            # Audit said >=2 but we couldn't resolve them; skip rather than
            # half-merge. The operator sees the count mismatch in --dry-run.
            continue

        merged = merge_safe_group(full_rows)
        canonical_sk = merged.get("SK")
        deletes: list[tuple[str, str]] = []
        for r in full_rows:
            pk, sk = r.get("PK"), r.get("SK")
            if pk is None or sk is None:
                continue
            if sk == canonical_sk:
                continue
            deletes.append((pk, sk))

        plan.append({"group": group, "merged": merged, "deletes": deletes})

    return plan


def apply_plan(
    plan: list[dict[str, Any]],
    put_item: Callable[[dict], None],
    delete_item: Callable[[str, str], None],
) -> dict[str, int]:
    """Execute a write-plan via the supplied put/delete callables.

    Returns a tally for logging. The callables are passed in (rather than
    boto3 used directly) so this layer stays unit-testable without moto.
    """
    tally = {"groups": 0, "puts": 0, "deletes": 0}
    for entry in plan:
        put_item(entry["merged"])
        tally["puts"] += 1
        for pk, sk in entry["deletes"]:
            delete_item(pk, sk)
            tally["deletes"] += 1
        tally["groups"] += 1
    return tally


def render_plan_md(plan: list[dict[str, Any]]) -> str:
    """Human-readable summary of a safe-merge plan (for --dry-run output)."""
    lines: list[str] = [
        "# DEDUPE Phase 2 — safe-merge plan",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        f"Groups to merge: **{len(plan)}**",
        "",
    ]
    if not plan:
        lines.append("Nothing to merge — re-run audit when more dupes appear.")
        return "\n".join(lines) + "\n"
    for i, entry in enumerate(plan, 1):
        g = entry["group"]
        merged = entry["merged"]
        lines.extend(
            [
                f"## {i}. `{g['manufacturer']}` / `{g['normalized_core']}`",
                "",
                f"- Canonical part_number: `{merged.get('part_number') or '—'}`",
                f"- Canonical product_id: `{merged.get('product_id') or '—'}`",
                f"- Canonical SK: `{merged.get('SK') or '—'}`",
                f"- Source rows: {g['row_count']}",
                f"- Orphan deletes: {len(entry['deletes'])}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


# ── DB scan (not unit-tested — boto-bound) ─────────────────────────────


def fetch_rows_from_dynamo(table_name: str) -> list[dict]:
    """Scan the products table and return raw items (dicts).

    Kept as a thin shim around `boto3.resource('dynamodb').Table(...).scan()`
    so the audit logic stays unit-testable on plain dicts. Returns the raw
    DynamoDB items (Decimal-typed numerics) — the audit's classification
    treats them as opaque values.
    """
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
    return items


def _decimal_to_native(obj: Any) -> Any:
    """Recursively convert Decimal → int/float so json.dumps works."""
    from decimal import Decimal

    if isinstance(obj, Decimal):
        as_int = int(obj)
        return as_int if as_int == obj else float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_native(v) for v in obj]
    return obj


# ── CLI entry ──────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="audit_dedupes", description=__doc__)
    parser.add_argument(
        "--stage",
        choices=["dev"],
        default="dev",
        help="Stage to scan. Phase 1 is dev-only; staging/prod refused on purpose.",
    )
    parser.add_argument(
        "--table",
        help="Override DynamoDB table name (default: products-<stage>)",
    )
    parser.add_argument(
        "--rows",
        type=Path,
        help="Read rows from a JSON file (list of dicts) instead of DynamoDB. "
        "Used by tests and dry-runs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the audit JSON here (default: outputs/dedupe_audit_<stage>_<ts>.json)",
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        help="Write the review queue MD here (default: outputs/dedupe_review_<stage>_<ts>.md)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress INFO logging",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Phase 2: write the merged canonical row + delete orphans for "
        "every `merge`-action group. Requires --safe-only and --stage dev. "
        "Refuses without --yes unless --dry-run is also set.",
    )
    parser.add_argument(
        "--safe-only",
        action="store_true",
        help="Limit --apply to groups whose suggested_action is `merge` "
        "(skip `review` and `delete-junk`). Required with --apply in Phase 2.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --apply, print the write-plan without touching DynamoDB.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the write side of --apply (without --dry-run).",
    )
    parser.add_argument(
        "--plan-output",
        type=Path,
        help="With --apply, write the merge plan markdown here "
        "(default: outputs/dedupe_plan_<stage>_<ts>.md)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.rows:
        raw = json.loads(args.rows.read_text())
        if not isinstance(raw, list):
            print(
                f"--rows must point to a JSON list of dicts, got {type(raw).__name__}",
                file=sys.stderr,
            )
            return 2
        rows = raw
    else:
        table = args.table or f"products-{args.stage}"
        rows = [_decimal_to_native(r) for r in fetch_rows_from_dynamo(table)]

    reports = audit(rows)
    log.info(
        "Found %s groups with >= 2 rows (%s merge-safe, %s for review, %s delete-junk)",
        len(reports),
        sum(1 for r in reports if r["suggested_action"] == "merge"),
        sum(1 for r in reports if r["suggested_action"] == "review"),
        sum(1 for r in reports if r["suggested_action"] == "delete-junk"),
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output or OUTPUT_DIR / f"dedupe_audit_{args.stage}_{ts}.json"
    md_path = args.review_output or OUTPUT_DIR / f"dedupe_review_{args.stage}_{ts}.md"

    json_path.write_text(
        json.dumps(
            {
                "stage": args.stage,
                "timestamp": ts,
                "total_groups": len(reports),
                "groups": reports,
            },
            indent=2,
            default=str,
        )
    )
    md_path.write_text(render_review_md(reports))

    log.info("Wrote audit JSON: %s", json_path)
    log.info("Wrote review queue: %s", md_path)

    if not args.apply:
        return 0

    # Phase 2 — safe-merge writes.
    if not args.safe_only:
        print(
            "--apply requires --safe-only in Phase 2 (`review` and "
            "`delete-junk` groups belong to Phase 3 / human review).",
            file=sys.stderr,
        )
        return 2
    if args.rows:
        print(
            "--apply is not supported with --rows (offline mode has no DB "
            "to write to). Drop --apply to inspect the plan from a fixture.",
            file=sys.stderr,
        )
        return 2
    if not args.dry_run and not args.yes:
        print(
            "--apply needs either --dry-run (preview only) or --yes "
            "(write to DynamoDB). Refusing to act ambiguously.",
            file=sys.stderr,
        )
        return 2

    rows_by_pksk: dict[tuple[str, str], dict] = {}
    for r in rows:
        pk, sk = r.get("PK"), r.get("SK")
        if pk is None or sk is None:
            continue
        rows_by_pksk[(pk, sk)] = r

    plan = plan_safe_merges(reports, rows_by_pksk)
    plan_path = args.plan_output or OUTPUT_DIR / f"dedupe_plan_{args.stage}_{ts}.md"
    plan_path.write_text(render_plan_md(plan))
    log.info("Wrote merge plan: %s", plan_path)

    if args.dry_run:
        log.info(
            "Dry-run: %s groups would merge (puts=%s, deletes=%s). "
            "Re-run with --yes (without --dry-run) to apply.",
            len(plan),
            len(plan),
            sum(len(p["deletes"]) for p in plan),
        )
        return 0

    table_name = args.table or f"products-{args.stage}"
    log.warning(
        "Applying %s merges to %s — puts=%s, deletes=%s",
        len(plan),
        table_name,
        len(plan),
        sum(len(p["deletes"]) for p in plan),
    )

    import boto3  # type: ignore

    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def _put(item: dict) -> None:
        table.put_item(Item=item)

    def _delete(pk: str, sk: str) -> None:
        table.delete_item(Key={"PK": pk, "SK": sk})

    tally = apply_plan(plan, _put, _delete)
    log.info(
        "Applied: %s groups merged, %s puts, %s deletes",
        tally["groups"],
        tally["puts"],
        tally["deletes"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
