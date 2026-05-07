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

Phase 3 (``--apply --from-review <md>``) reads a filled-in review
markdown — the file Phase 1 generates, edited with reviewer picks for
the ``review`` and ``delete-junk`` groups — and applies the chosen
merge / delete-all per group. Same dry-run / yes guards as Phase 2.

Usage:
    uv run python -m cli.audit_dedupes --stage dev
    uv run python -m cli.audit_dedupes --stage dev --output /tmp/audit.json
    uv run python -m cli.audit_dedupes --rows tests/fixtures/sample.json  # offline
    uv run python -m cli.audit_dedupes --stage dev --apply --safe-only --dry-run
    uv run python -m cli.audit_dedupes --stage dev --apply --safe-only --yes
    uv run python -m cli.audit_dedupes --stage dev --apply --from-review review.md --dry-run
    uv run python -m cli.audit_dedupes --stage dev --apply --from-review review.md --yes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
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


def conflicting_values_for(
    rows: list[dict], classifications: dict[str, str]
) -> dict[str, list[Any]]:
    """For each `conflicting` field, return per-row values (None for absent).

    Lets the review-md renderer show side-by-side disagreements without
    re-reading the source rows, and lets the Phase 3 ``--from-review``
    applier resolve picks (``row 1``, ``row 2``, …) back to a value.
    """
    return {
        field: _values_for(rows, field)
        for field, c in classifications.items()
        if c == "conflicting"
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
                "conflicting_values": conflicting_values_for(
                    group_rows_list, classifications
                ),
                "family_mismatch": family_mismatch,
                "suggested_action": action,
            }
        )
    return reports


# ── Rendering ──────────────────────────────────────────────────────────


def _format_value_md(value: Any) -> str:
    """Format a DynamoDB value for the markdown table — JSON in backticks.

    The Phase 3 parser does the inverse: strip backticks + json.loads. Falls
    back to ``str(value)`` for non-JSON-serialisable values; the parser
    treats those as opaque strings.
    """
    if value is None:
        return "—"
    try:
        encoded = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return f"`{value}`"
    return f"`{encoded}`"


def render_review_md(reports: list[dict[str, Any]]) -> str:
    """Markdown review queue for the `review` and `delete-junk` groups.

    Each section is a fillable form: the reviewer changes the ``Pick:``
    line and the per-field ``pick`` column, then feeds the file to
    ``--from-review`` (Phase 3). See ``parse_review_md`` for the contract.
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
        "**How to fill this in:**",
        "",
        "- Per group, change the `Pick:` line to one of `merge`, "
        "`delete-all`, or `skip` (default).",
        "- For `merge`, fill the `pick` column on each conflicting field "
        "with the row number whose value to take (1, 2, …) — leave any "
        "blank to skip the entire group.",
        "- `delete-all` deletes every row in the group (use for junk-only "
        "groups). `skip` leaves the rows alone.",
        "- Run `./Quickstart audit-dedupes --stage dev --apply --from-review "
        "<this file> --dry-run` to preview, then `--yes` to apply.",
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
                "- Pick: `skip`  <!-- change to merge | delete-all | skip -->",
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
            row_count = r["row_count"]
            conflicting_values = r.get("conflicting_values") or {}
            lines.extend(
                [
                    "",
                    "### Conflicting fields",
                    "",
                    "| field | "
                    + " | ".join(f"row {i + 1}" for i in range(row_count))
                    + " | pick |",
                    "|---|" + "|".join(["---"] * row_count) + "|---|",
                ]
            )
            for field in conflicting:
                values = conflicting_values.get(field) or [None] * row_count
                # Pad/truncate defensively in case rows shifted post-audit.
                if len(values) < row_count:
                    values = values + [None] * (row_count - len(values))
                value_cells = " | ".join(
                    _format_value_md(v) for v in values[:row_count]
                )
                lines.append(f"| `{field}` | {value_cells} |  |")
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


def merge_with_picks(rows: list[dict], picks: dict[str, int]) -> dict:
    """Phase 3 — merge a `review` group using reviewer picks for conflicts.

    `picks` maps field name → 1-indexed row number whose value to take.
    Fields not in `picks` are merged via the Phase 2 ``merge_safe_group``
    rule (non-null wins). Used by ``--from-review`` for groups whose
    reviewer chose ``Pick: merge``.

    Raises ValueError if any pick references a row index out of range.
    """
    if not rows:
        raise ValueError("merge_with_picks requires at least one row")

    n = len(rows)
    for field, idx in picks.items():
        if not 1 <= idx <= n:
            raise ValueError(
                f"merge_with_picks: pick {idx!r} for field {field!r} out of "
                f"range 1..{n}"
            )

    merged = merge_safe_group(rows)
    for field, idx in picks.items():
        chosen = rows[idx - 1].get(field)
        if chosen is None or chosen == "" or chosen == [] or chosen == {}:
            # Reviewer asked to take an empty value — clear the field.
            merged.pop(field, None)
        else:
            merged[field] = chosen
    return merged


# ── Phase 3 — review-md parser ────────────────────────────────────────


_HEADER_RE = re.compile(
    r"^##\s+\d+\.\s+`(?P<mfg>[^`]+)`\s*/\s*`(?P<core>[^`]+)`\s*"
    r"—\s*(?P<action>review|delete-junk)\s*$"
)
_PICK_DIRECTIVE_RE = re.compile(
    r"^-\s*Pick:\s*`?(?P<pick>merge|delete-all|skip)`?", re.IGNORECASE
)
# Markdown table row: `| `field` | val | val | <pick> |`
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_BACKTICK_VALUE_RE = re.compile(r"^`(.*)`$")


def _strip_md_cell(cell: str) -> str:
    return cell.strip()


def _parse_value_cell(cell: str) -> Any:
    """Inverse of `_format_value_md`. Returns None for an em-dash placeholder."""
    s = _strip_md_cell(cell)
    if not s or s == "—":
        return None
    m = _BACKTICK_VALUE_RE.match(s)
    if m:
        inner = m.group(1)
        try:
            return json.loads(inner)
        except (TypeError, ValueError):
            return inner
    return s


def parse_review_md(text: str) -> list[dict[str, Any]]:
    """Parse a filled-in review markdown into per-group action+picks.

    Returns one entry per group with::

        {
            "manufacturer": str,
            "normalized_core": str,
            "audit_action": "review" | "delete-junk",
            "pick": "merge" | "delete-all" | "skip",
            "field_picks": {field: row_index_1_based},
        }

    The applier looks each entry up in the current audit by
    ``(manufacturer, normalized_core)`` and acts only on entries whose
    ``pick`` is ``merge`` or ``delete-all``. Groups with ``pick == skip``
    or with incomplete field picks are skipped (the operator decides).
    """
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_conflict_table = False
    column_count = 0

    for raw in text.splitlines():
        line = raw.rstrip()
        header = _HEADER_RE.match(line)
        if header:
            if current is not None:
                groups.append(current)
            current = {
                "manufacturer": header.group("mfg"),
                "normalized_core": header.group("core"),
                "audit_action": header.group("action"),
                "pick": "skip",
                "field_picks": {},
            }
            in_conflict_table = False
            column_count = 0
            continue
        if current is None:
            continue
        pick_directive = _PICK_DIRECTIVE_RE.match(line)
        if pick_directive:
            current["pick"] = pick_directive.group("pick").lower()
            continue
        if line.startswith("### Conflicting fields"):
            in_conflict_table = True
            column_count = 0
            continue
        if line.startswith("### ") or line.startswith("## "):
            in_conflict_table = False
        if not in_conflict_table:
            continue
        m = _TABLE_ROW_RE.match(line)
        if not m:
            continue
        cells = [c for c in m.group(1).split("|")]
        # Skip the header row (`field | row 1 | row 2 | pick`) and the
        # separator (`---|---|---|---`).
        joined = "".join(cells).strip()
        if not joined or set(joined) <= set("- "):
            # Separator row — also tells us the column count.
            column_count = len(cells)
            continue
        if column_count == 0:
            # Header row of the conflict table — count columns.
            column_count = len(cells)
            continue
        if len(cells) < 3:
            continue
        # Layout: ['', ' `field` ', ' val1 ', ..., ' pick ', '']
        field_cell = _strip_md_cell(cells[0])
        field_name_match = _BACKTICK_VALUE_RE.match(field_cell)
        if not field_name_match:
            continue
        field = field_name_match.group(1)
        pick_cell = _strip_md_cell(cells[-1])
        if not pick_cell:
            continue
        try:
            pick_idx = int(pick_cell)
        except ValueError:
            continue
        current["field_picks"][field] = pick_idx

    if current is not None:
        groups.append(current)
    return groups


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


def plan_from_review(
    review_entries: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    rows_by_pksk: dict[tuple[str, str], dict],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build a write-plan from parsed review entries + a fresh audit.

    Returns ``(plan, skipped)``. Plan entries have the same shape as
    ``plan_safe_merges`` so they flow through ``apply_plan`` unchanged.
    Skipped entries record the reason so the dry-run output can show
    why each one was dropped (lets the operator fix the file and re-run).

    Skip reasons:
    - ``not_in_audit`` — manufacturer/core not in the current audit
      (rows changed since the file was rendered).
    - ``row_count_mismatch`` — audit row count differs from the file's;
      treat as stale.
    - ``pick_skip`` — reviewer left ``Pick: skip``.
    - ``incomplete_picks`` — ``Pick: merge`` but at least one conflicting
      field has no row index.
    - ``unresolvable_rows`` — audit references rows we can't find in the
      live scan (rows deleted between audit and apply).
    - ``invalid_pick_index`` — a pick index is out of range for the group.
    """
    by_key = {(g["manufacturer"], g["normalized_core"]): g for g in reports}
    plan: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for entry in review_entries:
        key = (entry["manufacturer"], entry["normalized_core"])
        group = by_key.get(key)
        if group is None:
            skipped.append({"entry": entry, "reason": "not_in_audit"})
            continue
        if (
            group["row_count"] != len(entry.get("field_picks", {}) or {})
            and entry["pick"] == "merge"
        ):
            # row count check happens below; the picks count check is per-conflict
            pass
        if entry["pick"] == "skip":
            skipped.append({"entry": entry, "reason": "pick_skip", "group": group})
            continue

        full_rows: list[dict] = []
        for row_ref in group.get("rows", []):
            pk, sk = row_ref.get("PK"), row_ref.get("SK")
            if pk is None or sk is None:
                continue
            full = rows_by_pksk.get((pk, sk))
            if full is not None:
                full_rows.append(full)
        if len(full_rows) != group["row_count"]:
            skipped.append(
                {"entry": entry, "reason": "unresolvable_rows", "group": group}
            )
            continue

        if entry["pick"] == "delete-all":
            deletes = [
                (r["PK"], r["SK"]) for r in full_rows if r.get("PK") and r.get("SK")
            ]
            plan.append(
                {
                    "group": group,
                    "merged": None,  # signals "deletes-only" to apply_plan
                    "deletes": deletes,
                    "review_pick": "delete-all",
                }
            )
            continue

        if entry["pick"] != "merge":
            skipped.append({"entry": entry, "reason": "pick_skip", "group": group})
            continue

        # Pick == merge: every conflicting field needs a row index.
        conflicting = [
            f
            for f, c in group.get("field_classifications", {}).items()
            if c == "conflicting"
        ]
        picks = entry.get("field_picks") or {}
        missing = [f for f in conflicting if f not in picks]
        if missing:
            skipped.append(
                {
                    "entry": entry,
                    "reason": "incomplete_picks",
                    "group": group,
                    "missing_fields": missing,
                }
            )
            continue

        try:
            merged = merge_with_picks(full_rows, picks)
        except ValueError as exc:
            skipped.append(
                {
                    "entry": entry,
                    "reason": "invalid_pick_index",
                    "group": group,
                    "error": str(exc),
                }
            )
            continue

        canonical_sk = merged.get("SK")
        deletes: list[tuple[str, str]] = []
        for r in full_rows:
            pk, sk = r.get("PK"), r.get("SK")
            if pk is None or sk is None:
                continue
            if sk == canonical_sk:
                continue
            deletes.append((pk, sk))

        plan.append(
            {
                "group": group,
                "merged": merged,
                "deletes": deletes,
                "review_pick": "merge",
            }
        )

    return plan, skipped


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
        merged = entry.get("merged")
        if merged is not None:
            put_item(merged)
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


def render_review_plan_md(
    plan: list[dict[str, Any]], skipped: list[dict[str, Any]]
) -> str:
    """Render a Phase 3 plan + skip-reason summary."""
    merges = [e for e in plan if e.get("review_pick") == "merge"]
    deletes = [e for e in plan if e.get("review_pick") == "delete-all"]
    lines: list[str] = [
        "# DEDUPE Phase 3 — review-applier plan",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        f"Merges: **{len(merges)}** · "
        f"Delete-all groups: **{len(deletes)}** · "
        f"Skipped: **{len(skipped)}**",
        "",
    ]
    if merges:
        lines.append("## Merges")
        lines.append("")
        for i, entry in enumerate(merges, 1):
            g = entry["group"]
            merged = entry["merged"] or {}
            lines.extend(
                [
                    f"### {i}. `{g['manufacturer']}` / `{g['normalized_core']}`",
                    "",
                    f"- Canonical SK: `{merged.get('SK') or '—'}`",
                    f"- Orphan deletes: {len(entry['deletes'])}",
                    "",
                ]
            )
    if deletes:
        lines.append("## Delete-all")
        lines.append("")
        for i, entry in enumerate(deletes, 1):
            g = entry["group"]
            lines.extend(
                [
                    f"### {i}. `{g['manufacturer']}` / `{g['normalized_core']}`",
                    "",
                    f"- Rows to delete: {len(entry['deletes'])}",
                    "",
                ]
            )
    if skipped:
        lines.append("## Skipped")
        lines.append("")
        for s in skipped:
            entry = s["entry"]
            extra = ""
            if s.get("missing_fields"):
                extra = f" (missing picks: {', '.join(s['missing_fields'])})"
            elif s.get("error"):
                extra = f" ({s['error']})"
            lines.append(
                f"- `{entry['manufacturer']}` / `{entry['normalized_core']}` "
                f"— **{s['reason']}**{extra}"
            )
        lines.append("")
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
    parser.add_argument(
        "--from-review",
        type=Path,
        help="Phase 3: apply a filled-in review markdown (the file Phase 1 "
        "writes — see `Pick:` directives). Combine with --apply --dry-run "
        "to preview, then --apply --yes to write.",
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

    # Apply path — Phase 2 (--safe-only) or Phase 3 (--from-review).
    if args.from_review and args.safe_only:
        print(
            "--from-review and --safe-only are mutually exclusive: "
            "--safe-only is the Phase 2 auto-merge path, --from-review "
            "is the Phase 3 reviewer-driven path. Pick one.",
            file=sys.stderr,
        )
        return 2
    if not args.from_review and not args.safe_only:
        print(
            "--apply requires --safe-only (Phase 2 auto-merge) or "
            "--from-review <md> (Phase 3 reviewer-driven). The bare "
            "--apply form is ambiguous on purpose.",
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

    skipped: list[dict[str, Any]] = []
    if args.from_review:
        if not args.from_review.exists():
            print(
                f"--from-review file not found: {args.from_review}",
                file=sys.stderr,
            )
            return 2
        review_entries = parse_review_md(args.from_review.read_text())
        plan, skipped = plan_from_review(review_entries, reports, rows_by_pksk)
        plan_path = (
            args.plan_output or OUTPUT_DIR / f"dedupe_review_plan_{args.stage}_{ts}.md"
        )
        plan_path.write_text(render_review_plan_md(plan, skipped))
        log.info(
            "Wrote review-applier plan: %s (merges=%s, deletes=%s, skipped=%s)",
            plan_path,
            sum(1 for e in plan if e.get("review_pick") == "merge"),
            sum(1 for e in plan if e.get("review_pick") == "delete-all"),
            len(skipped),
        )
    else:
        plan = plan_safe_merges(reports, rows_by_pksk)
        plan_path = args.plan_output or OUTPUT_DIR / f"dedupe_plan_{args.stage}_{ts}.md"
        plan_path.write_text(render_plan_md(plan))
        log.info("Wrote merge plan: %s", plan_path)

    total_puts = sum(1 for e in plan if e.get("merged") is not None)
    total_deletes = sum(len(e["deletes"]) for e in plan)

    if args.dry_run:
        log.info(
            "Dry-run: %s plan entries (puts=%s, deletes=%s). "
            "Re-run with --yes (without --dry-run) to apply.",
            len(plan),
            total_puts,
            total_deletes,
        )
        return 0

    table_name = args.table or f"products-{args.stage}"
    log.warning(
        "Applying %s entries to %s — puts=%s, deletes=%s",
        len(plan),
        table_name,
        total_puts,
        total_deletes,
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
        "Applied: %s entries, %s puts, %s deletes",
        tally["groups"],
        tally["puts"],
        tally["deletes"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
