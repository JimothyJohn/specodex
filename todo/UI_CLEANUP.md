# UI_CLEANUP — de-crowd and de-jank the catalog UI

> **Status:** 🚧 in progress. Audit done 2026-09-13 (headless Chromium
> screenshots of local dev at 1440 / 1024 / 390 px, both themes).
> **Phase 0 shipped 2026-09-13** in the same PR as this doc.
> **Phase 1 shipped 2026-09-13** in two PRs (1a: N1, N2, N3, N5, N7 —
> #415; 1b: N4, N6 — #416). **Phase 2 direction decided 2026-09-13: A —
> popover per column** (see §3). **S1 shipped 2026-09-13**: header
> band 210 px → 78 px at rest. S2 (default column set by fill rate)
> and S3 (mobile) remain.
>
> Nick's brief, verbatim: "The interface needs a lot of work, it's very
> crowded and janky looking."

---

## 0. Where the crowding actually comes from

The Bauhaus port (todo/BAUHAUS.md, May 2026) got the *vocabulary* right —
the detail modal is clean and is the reference surface. What's crowded is
the catalog table header: every column permanently carries six controls
(histogram, two-thumb slider, `any` pill, `≥` op toggle, unit toggle,
close-X). At 10 default columns that is ~60 interactive elements in a
210 px band above the first data row. Everything else on the list is
smaller, but it all stacks on top of that.

Ranked by how much of the "crowded / janky" impression each one causes.

## 1. Findings

### Bugs (Phase 0 — no design decision needed)

| # | What | Where | Evidence |
|---|---|---|---|
| B1 | Light theme: `FEEDBACK` and `SIGN IN` are near-invisible on the header band. The header stays dark in both themes but these two buttons colour with `--text-primary` (ink). | `components/ui/FeedbackModal.css:103`, `App.css:6303` | screenshot 06 |
| B2 | Engineering-paper grid shows only as a ~50 px strip under the table. The body carries the grid, the table/toolbar are opaque, so the grid only peeks out below the last row and reads as a rendering glitch. | `App.css:5534` (body background stack) | 03, 06, 07, 09 |
| B3 | Tooltip "Click anywhere to sort…" pops up over the type dropdown whenever the mouse rests anywhere on the Part Number header. Not a positioning bug: the tooltip was anchored to the whole 200 px-tall cell, so "above the anchor" is the toolbar. Fixed by anchoring it to the label text only. | `ProductList.tsx` Part Number header | 03 |
| B4 | 390 px: header overflows horizontally (SIGN IN clipped), `MANUFACTURE R` wraps mid-word, `28%` clips to `2°`, toolbar wraps to two rows. 1024 px: table is cut at the right edge with no scroll affordance. **Phase 0 fixed only the header overflow** (GitHub icon hidden ≤ 480 px); the label wrap, `%` clip, and scroll affordance are mobile layout work → S3. | header, `.column-header-label-text`, `.results-table-wrap` | 07, 08 |
| B5 | Manufacturer legend clips without ellipsis: `BODINE EL`, `MITSUBISI`. | ColumnHeader manufacturer legend | 03, 09 |
| B6 | Drives: `+ Add Spec` hangs off the end of the header row outside the table's right border; table doesn't fill the content width. | `ProductList.tsx:1127` | 09 |

### Noise (Phase 1 — small, low-risk, opinionated but safe)

| # | What | Why it reads as crowded |
|---|---|---|
| N1 | ✅ Result count stated twice: `1-25 of 4265` (mono, left) and `4265 / 12936 MATCHING 33%` + bar (Oswald, right). Same fact, two type systems. **Decision:** the Bauhaus match block stays; the mono count is gone. The streaming "Loading records…" bar is functional status, not a count — kept. | `ProductList.tsx` toolbar |
| N2 | ✅ `GEAR (ratio)` column on motors shows `—` on every row until a torque filter exists. Always-empty column by default. **Decision:** column appears only once a torque floor is set (undoes the always-on choice from PR #201). | `ProductList.tsx` `showGearColumn` |
| N3 | ✅ Dashed underline on every manufacturer cell (vendor-drawer affordance) = 25 dashed rules per page. Now hover/focus-only; the global `:where(button)` stamp shadow is also dropped on the link, it drew a faint box behind every name once the underline went. | `VendorDrawer.css` `.vendor-link` |
| N4 | ✅ Header decoration with no function: `▸ ◂` glyphs flanking the wordmark, the `OPTIONS` eyebrow (and the hairline it anchored), GitHub button in primary nav position. **Decision:** all three go; the GitHub link now sits in the right-hand options cluster between the theme toggle and the account menu. | `App.tsx` header, `App.css` |
| N5 | ✅ Ragged row heights when manufacturer wraps (`Mitsubishi Electric`, `Advanced Motion Controls`). One line + ellipsis; the existing "Vendor facts for …" Tooltip carries the full name. | `VendorDrawer.css` `.vendor-link` |
| N6 | ✅ Welcome page rendered "T1 — Ratio Studies" logo explorations and a favicon strip below the hero — design scratch on the public landing. Section, data, and CSS deleted; recoverable via `git log -S T1_RATIO_STUDIES`. | `Welcome.tsx`, `Welcome.css` |
| N7 | ✅ Column tint on the gear-cascade key columns (Rated Torque / Rated Speed) was a full-height background band. Root cause: `getProximityColor` painted the percentile gradient for any column with a filter *entry*, including the valueless seeded chips — so both columns were banded on every motor load with no filter set. Fixed by skipping valueless chips. The cascade meaning now lives in a header-only `GEARED` marker (`cascadeKey` prop on `ColumnHeader`) shown on torque/speed columns while a torque floor is active. | `ProductList.tsx` `getProximityColor`, `ColumnHeader.tsx` |

### Structure (Phase 2 — needs direction)

| # | What |
|---|---|
| S1 | ✅ Per-column inline filter panel in the permanent header (the 210 px band). Shipped as direction A: at rest each column is label + sort + a 10 px sparkline + one trigger that summarises the filter (`any` / `≥ 10 Nm` / `ABB, Siemens`); the histogram, slider, value box and operator/unit pills live in an `AnchoredPopover` under the trigger. Categorical columns keep the multi-select popover behind the same-sized trigger. Applied constraints render as chips in the toolbar (`ActiveFilterChips`, × removes one). Measured at rest on the motor view: 78 px (the 2-line wrapped labels set the floor — a one-line label column would be ~62 px). Compact density is unchanged (it never had the inline stack; 57 px). |
| S2 | Sparse columns get the widest slots: Axial Load Force Rating is ~90% empty on motors and the widest column. Default column set should favour fill rate. |
| S3 | Mobile layout has no real design; depends on S1. |

## 2. Phases

- **Phase 0 — bugs.** B1–B6. ✅ Shipped 2026-09-13 (one PR with this
  doc). B4 only partially — see the row. Side effect worth knowing: the
  catalog page is now exactly viewport-height (`.page-products-layout`
  = `100dvh - --header-h`, flex chain down to `.product-grid-scroll`),
  so the body's engineering-paper grid never shows on the catalog at
  all. It only ever showed as the 50 px strip, so nothing visible was
  lost; if the grid is wanted back it belongs on the empty state, not
  under the table.
- **Phase 1 — noise.** N1–N7. ✅ Shipped 2026-09-13 in two PRs: 1a
  (N1, N2, N3, N5, N7 — #415) and 1b (N4, N6).
- **Phase 2 — header structure.** Direction **A (popover per column)**
  decided 2026-09-13. **S1 ✅ shipped 2026-09-13.** S2 next. Then S3.

Exit criteria for the whole doc: catalog header ≤ 56 px at rest, no
duplicated count, no always-empty default column, both themes legible in
the header, 390 px usable, no grid strip.

Scorecard 2026-09-13 after S1: header 78 px (target 56 — the residual is
the two-line label wrap, "RATED TORQUE" at ~80 px column width; a
tighter label treatment is S2/S3 territory), count ✅, empty column ✅,
themes ✅, grid strip ✅, 390 px ⏳ (S3).

## 3. S1 direction — decided 2026-09-13: **A, popover per column**

The header band is the crowding. Three options were on the table;
Nick picked A. Kept for the record:

- **A — popover per column (recommended).** Header = label + sort + a
  hairline sparkline. Clicking the label opens the existing histogram /
  slider / op / unit as a popover. Active filters render as chips in one
  row above the table, reusing `FilterChip` / `MultiSelectFilterPopover`.
  Keeps "filter in place", drops the header to ~48 px, all primitives
  already exist.
- **B — collapsed by default.** Keep the inline controls but show one
  row at rest; a `FILTERS` toggle (or hover/focus) expands the band.
  Least code change, least improvement.
- **C — left rail.** The original Bauhaus mock's `aside.rail`. Plain
  table headers, all filters in a 280 px rail. Biggest layout change;
  costs horizontal room the wide tables already lack.

Recommendation: **A**.
