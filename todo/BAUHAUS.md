# BAUHAUS — UI refresh port from `docs/design/bauhaus-catalog.html`

> **Status:** 🎨 in flight. Wave 1 shipped 2026-05-12 via [PR #150](https://github.com/JimothyJohn/specodex/pull/150).
> Phases 2–10 below break the remaining work into independently shippable PRs.
>
> **Date drafted:** 2026-05-12.
> **Owner:** Nick. **Contributors welcome on hygiene phases (6, 7);** visual phases (2–5, 8–10) need a single hand for consistency.

---

## 0. Why this exists

[PR #136](https://github.com/JimothyJohn/specodex/pull/136) explored three design
directions; Bauhaus Catalog won. [PR #150](https://github.com/JimothyJohn/specodex/pull/150)
landed wave 1 — the tokens layer, the header, the filter-chip truncation fix,
and Oswald column headers. That PR's "out of scope" section is the explicit
follow-up menu; this doc turns it into a phased plan keyed to the static mock.

**The audit's three "amateur tells"** the wider port still has to resolve:

1. **Silent truncation.** Long attribute names (`network_protocol`,
   `encoder_resolution_bits`) clipped to `Re…` on chips and `MANUF…` on
   column headers. PR #150 fixed the chip side; the column-header side is
   the dirty working-tree edit that was lost in cross-session churn — see
   **Phase 1 — recovery** below for the captured diff.
2. **Layering chaos.** App.css has **21 inline `z-index` values** ranging
   `0 → 10000`. PR #150 added 6 tokens (`--z-content` → `--z-toast`); the
   call-site migration is Phase 6.
3. **Specificity wars.** App.css has **116 `!important` declarations**
   across ~6 clusters (column-remove-btn, button overrides, modal close,
   filter chip, density variants, scrollbar). Phase 7.

Plus a fourth tell that's visual rather than structural: **web-pill rounded
corners and soft drop-shadows** that fight the paper-and-stamp aesthetic.
Each visual phase strips those at the surface it touches.

**This is a MASSIVE lift in aggregate, BUT** every phase is independently
shippable. Stop after any one and the app still works; later surfaces just
keep their pre-port look until their phase lands.

---

## 1. End-state visual = `docs/design/bauhaus-catalog.html`

The mock at `docs/design/bauhaus-catalog.html` (829 lines, self-contained
HTML/CSS) is the single source of truth for the target visual. Every phase
references it by surface:

| Surface in mock | Selectors / shape | Maps to phase |
|---|---|---|
| `.top-strip` + `.top-nav` + `.top-options` | square brand-mark, joined-button nav, density/units/theme top-options | ✅ Wave 1 (header) + small polish in Phase 2 |
| `.breadcrumb-strip` | second header bar with `FIG. 01` rotated-square marker | Phase 2 |
| `aside.rail` (280px) | left filter rail container, 2px ink right-border | Phase 4 |
| `.rail-section` + `.rail-head` + `.badge` | Oswald uppercase head with 2px underline, mono count badge | Phase 4 |
| `.rail-type-list` (with `.marker`, `.label`, `.count`) | product-type list, 8×8 ink squares, active = ink-on-paper | Phase 4 |
| `.filter-stack` + `.filter-row` + `label` | label-over-chip stack, Oswald 10px uppercase labels | Phase 5 |
| `.filter-chip` + `.op` + `.close` | hard-bordered chip, brass `≥/≤/IN` op prefix, divided close-X cell with stamp-red hover | Phase 5 |
| `.add-filter` | dashed-border button, hover → solid | Phase 5 |
| `.results-head` + `.results-eyebrow` (with triangle) + `.results-title h1` | brass uppercase eyebrow with `▼` triangle, 32px Oswald headline | Phase 2 |
| `.results-counts` (with large `<strong>` tabular numerals) | "matching **142** of **6,108**" pattern, 28px display numerals | Phase 2 |
| `.toolbar` | joined-button view toggle (Table / Grid / Compat), active = ink-on-paper | Phase 2 |
| `.stat-row` + `.stat-cell` + `.stat-label` + `.stat-value` | 4-cell summary bar with hard borders and 8×8 corner marks | Phase 2 |
| `.results-table-wrap` + `table.results` | bordered table, paper-dim thead, hairline grid-fine row separators, `.is-numeric` right-aligned tabular | Phase 3 |
| `td .pn` + `td .mfg` | Oswald part-number + mute mono manufacturer caption | Phase 3 |
| `.tag` / `.tag.brass` | inline 1px-ink-border tag chips inside cells | Phase 3 |
| `.legend` | dashed-border footer key | Phase 3 |
| `.footnote` | top-ruled footer with corner-mark | Phase 3 |

**What the mock does NOT show** (and therefore each phase has to decide
without it):
- Modal dialogs (ProductDetailModal, ChainReviewModal, AuthModal) — **Phase 8**
- BuildPage, AdminPanel, DatasheetsPage — **Phases 9, 10**
- Dropdown / MultiSelectFilterPopover / Tooltip primitives — extend the mock's
  hard-border vocabulary; see per-phase scope.
- Dark theme (espresso variant) — derived from tokens; should follow per surface
  without dedicated phases. Validated at each phase's exit.

---

## 2. Phases at a glance

| Phase | Scope | Effort | Risk | Reversible? | Gate |
|---|---|---|---|---|---|
| **1** | Column-header WRAP fix (recover lost edit) + any small wave-1 polish | XS (< 1h) | low | yes (revert) | vitest unchanged; long-label visual check |
| **2** | Catalog summary bar: results-head + stat-row + toolbar | M (1–2 days) | medium | yes | visual diff vs mock; no data-shape changes |
| **3** | Catalog table: borders + hairline rules + `.pn/.mfg` cell content + tag chips | M (1–2 days) | medium | yes | vitest unchanged; row hover preserved |
| **4** | Filter rail: layout (380→280), rail-section heads, type-list with markers | M (2 days) | medium | yes | filter logic untouched, visual swap only |
| **5** | Filter chips: operator-prefix, hard-bordered close-X, dashed +ADD FILTER | S (½ day) | low | yes | filter state-management contract preserved |
| **6** | Z-index migration: 21 inline magic numbers → 6 tokens from wave 1 | S (½ day) | low | yes | visual diff is **null**; layering preserved |
| **7** | `!important` sweep: ~6 specificity clusters refactored | M (1–2 days) | medium | yes | visual diff is **null**; all overrides still work |
| **8** | Modal pattern: ProductDetailModal + ChainReviewModal port | M (1–2 days) | medium | yes | modal contracts preserved; e2e modal flows pass |
| **9** | BuildPage Bauhaus port | M (1–2 days) | medium | yes | build-flow regression tests pass |
| **10** | AdminPanel + DatasheetsPage port | L (3–4 days) | medium | yes | admin + datasheet edit smoke tests pass |

**Stop-after-any-phase property.** Phases 1–5 are visual, phases 6–7 are
hygiene, phases 8–10 are remaining surfaces. The app stays shippable after
each. Hygiene phases (6, 7) can be slotted between visual phases or done
in parallel by a different contributor.

**Recommended order if shipping serially:** 1 → 2 → 3 → 6 → 4 → 5 → 7 → 8 → 9 → 10.
Rationale: visual catalog (1, 2, 3) lands the most user-visible delta first;
z-index migration (6) before rail/chips (4, 5) means the rail/chips don't
inherit new magic numbers; `!important` sweep (7) after all chip-and-rail
selectors stabilise so the cluster refactor doesn't have to be redone.

---

## Phase 1 — Column-header wrap + recovery

**Goal:** finish the column-header truncation thread from PR #150. The
captured-but-lost edit is recoverable from this doc's appendix.

### 1.1 Scope

- One selector: `.column-header-label-text` in `app/frontend/src/App.css`.
- Replace `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`
  with the same `word-break: break-word; overflow-wrap: anywhere; min-width: 0`
  treatment `.filter-attribute` got in PR #150.
- Add `line-height: 1.15` so 2-line wrap doesn't overflow the cell vertically.

### 1.2 Why this happens at the live cell width

PR #150 switched the column-header type to Oswald uppercase on the theory
that the condensed font would fit more characters per line and the existing
ellipsis was a safety net. At the live cell width (~52px after the sort
indicator and remove button claim their share) even `"Manufacturer"` still
clips to `MANUF…`. Wrapping is the actual fix; the condensed font remains
beneficial for keeping wrap-count low.

### 1.3 Files touched

- `app/frontend/src/App.css` (one selector, ~22 lines)

### 1.4 Exit criteria

- [ ] Visual: long column labels (`Manufacturer`, `Network Protocol`,
      `Encoder Resolution Bits`) wrap to 2 lines instead of `…`.
- [ ] Visual: short labels (`Type`, `Power`) still render on one line.
- [ ] `npm test -- --run` — unchanged pass/fail counts.
- [ ] `npm run lint` — unchanged warning count.

### 1.5 Recovery source

The captured diff for this phase lives in the **Appendix A** at the bottom
of this doc — verbatim from the lost dirty working-tree edit.

---

## Phase 2 — Catalog summary bar

**Goal:** port the top of the catalog page (results-head + stat-row +
toolbar) to match the mock.

### 2.1 Scope

The mock's `main.content` opens with three blocks:

- **`.results-head`** — eyebrow (brass uppercase with `▼` triangle), 32px
  Oswald headline, results-counts pattern (`matching **N** of **TOTAL**`
  with 28px tabular numerals), joined-button toolbar on the right.
- **`.stat-row`** — 4-cell summary with hard ink borders and 8×8 corner
  marks. Each cell: Oswald 10px uppercase label + 24px tabular value
  + optional unit / accent.
- **`.toolbar`** — joined buttons (Table / Grid / Compat), active state
  is ink-on-paper inversion.

### 2.2 Current state

The React app today has a `.results-header` or equivalent in `App.tsx`
that renders count + filters chips but does NOT have:
- The eyebrow + triangle pattern
- The 4-cell stat-row (this is new — derives stats from the current result set)
- The joined-button toolbar (currently `<DensityToggle>` + separate
  view-mode controls scattered in the header)

The stat-row needs a small TypeScript surface for deriving the 4 stats from
the filtered result set. Keep it pure-frontend; no backend change.

### 2.3 Files touched

- `app/frontend/src/App.tsx` (markup, ~50 lines)
- `app/frontend/src/App.css` (selectors, ~150 lines)
- Possibly extract `ResultsSummary.tsx` + `ResultsSummary.css` if the JSX
  grows past 30 lines (per CLAUDE.md's per-component CSS rule).

### 2.4 Risk

Medium. The stat-row needs to derive stats from the result set, which
means deciding which stats per product type. **Sub-question to surface
during phase 2 design:** is the stat selection static-per-product-type
(declared in `filters.ts`) or computed dynamically (top-4 numeric-range
attributes by spread)? Recommend static-per-product-type for predictability;
the mock implies it.

### 2.5 Exit criteria

- [ ] Summary bar renders for `drive`, `motor`, `gearhead`, `contactor`,
      `robot_arm`, `electric_cylinder` (every `ProductType`).
- [ ] Toolbar `Table` toggle preserved as default; new `Grid` and `Compat`
      toggles can stub to "coming soon" or wire to existing modes if any.
- [ ] Eyebrow triangle renders without SVG (CSS triangle per the mock).
- [ ] No new z-index magic numbers (use tokens from PR #150).

---

## Phase 3 — Catalog table

**Goal:** port the results table to the mock's bordered/ruled shape.

### 3.1 Scope

- `<table>` wrapper with `border: 1px solid var(--ink)`.
- `thead th` — paper-dim background, 2px ink bottom rule, hairline
  `grid-line` right rules, Oswald 10px uppercase, optional unit-tag in
  mono. `.is-numeric` right-aligned.
- `tbody td` — hairline `grid-fine` bottom + right separators, hover →
  `paper-warm`, `.is-numeric` tabular right-aligned.
- `.pn` (Oswald 13px part-number) + `.mfg` (mute 11px mono caption) cell
  content pattern for the primary identity column.
- `.tag` chips for protocols / certifications cells (1px ink border,
  uppercase, optional brass-fill variant).

### 3.2 Current state

The React app has `ResultsTable.tsx` (or equivalent) and `ColumnHeader.tsx`.
PR #150 already ported the column-header *type*; Phase 1 finishes the wrap.
Phase 3 ports the surrounding structure: borders, row rules, cell content
patterns.

### 3.3 Risk

Medium. The largest visual delta in the port. Hover handling and selection
state need to coexist with the new hairline rules without specificity wars.
**Watch out:** the current row-selected state uses `!important` in the
`.column-remove-btn` cluster — Phase 3 may need partial Phase 7 work to land
cleanly, OR Phase 7 lands first and Phase 3 inherits the clean specificity.

### 3.4 Files touched

- `app/frontend/src/App.css` (table selectors, ~200 lines)
- `app/frontend/src/components/ColumnHeader.tsx` if structural changes needed
- Possibly extract a `ResultsTable.css` sibling per the per-component CSS rule.

### 3.5 Exit criteria

- [ ] Table renders with hard outer border + hairline inner rules.
- [ ] Sort indicators preserved (PR #150 didn't touch them).
- [ ] Density toggle (cozy / compact) still works.
- [ ] No new `!important` declarations.
- [ ] Column reorder / hide / sort UX unchanged.

---

## Phase 4 — Filter rail

**Goal:** port the left filter rail to the mock's 280px / Bauhaus shape.

### 4.1 Scope

- Rail container: 280px wide (down from 380px in current app), 2px ink
  right-border.
- `.rail-section` blocks separated by `.rail-head` (Oswald uppercase with
  2px underline) and `.badge` count on the right.
- `.rail-type-list` for product-type selector: 8×8 ink squares as markers,
  active state = ink-on-paper inversion, count column right-aligned tabular.

### 4.2 Current state

The current `<FilterSidebar>` (or equivalent) is the 380px rigid sidebar
the PR #150 description called out as rough. It mixes product-type selector,
active-filter chips, and add-filter UI without strong sectioning. Phase 4
introduces the rail-section structure.

### 4.3 Files touched

- `app/frontend/src/components/FilterSidebar.tsx` (or whichever file holds
  the rail) — possibly rename or split.
- Sibling `.css` extraction per the per-component CSS rule.
- `app/frontend/src/App.css` — remove the old 380px-wide layout rules.

### 4.4 Risk

Medium. Layout width change (380 → 280) propagates to every page that uses
the rail. Need to verify density / compact modes still fit at 280px.

### 4.5 Exit criteria

- [ ] Rail width = 280px on desktop, full-width above on mobile (media query
      at 980px per the mock).
- [ ] Rail-section structure for `Product Type`, `Active Filters`,
      `Manufacturer` (and any other sections the current sidebar has).
- [ ] Product-type list renders all 6 types with marker + label + count.
- [ ] Active type's count is rendered in `--brass-bright` per the mock.

---

## Phase 5 — Filter chips + add-filter

**Goal:** complete the rail's filter-row + filter-chip + add-filter pattern.

### 5.1 Scope

- `.filter-row` — Oswald 10px uppercase label above, chip below.
- `.filter-chip` — 1px ink border, brass-colored op prefix (`≥/≤/IN`),
  divided close-X cell with stamp-red hover.
- `.add-filter` — dashed-border button, hover → solid border.

### 5.2 Risk

Low. Filter state-management contract is preserved; this is purely
presentational. Operator-prefix logic already exists in the
`FilterChip.tsx` value-formatter.

### 5.3 Exit criteria

- [ ] Active filters render with op prefix (`≥`, `≤`, `=`, `IN`, etc.)
      in brass.
- [ ] Remove-X button has stamp-red hover.
- [ ] `+ ADD FILTER` button matches dashed → solid hover.
- [ ] No regression in filter add/remove flow tests.

---

## Phase 6 — Z-index call-site migration (hygiene)

**Goal:** migrate the 21 inline `z-index` values in App.css onto the tokens
PR #150 added.

### 6.1 Scope

Current inline values to migrate:

| Current | Token |
|---|---|
| `z-index: 0`, `z-index: 1`, `z-index: 2` | `var(--z-content)` |
| `z-index: 10`, `z-index: 30` | `var(--z-toolbar)` |
| `z-index: 50`, `z-index: 100` | `var(--z-header)` |
| `z-index: 999`, `z-index: 1000` | `var(--z-popover)` |
| `z-index: 2000` | `var(--z-modal)` |
| `z-index: 9999`, `z-index: 10000` | `var(--z-toast)` |

Mapping requires reading each call site's intent — a `z-index: 1000` on a
dropdown is `--z-popover`, not `--z-modal`. **Phase 6 is non-trivial
reading-work** even though the diff is small.

### 6.2 Risk

Low if mappings are done carefully; medium if rushed. The risk is layering
regressions (modal under dropdown, toast under modal). Mitigation: take
screenshots of every modal / popover / toast state before and after; visual
diff.

### 6.3 Exit criteria

- [ ] Zero inline numeric `z-index:` values in App.css; all tokens.
- [ ] Visual diff of layered surfaces is null.
- [ ] Toast over modal, modal over popover, popover over header, header
      over content — all preserved.

---

## Phase 7 — `!important` cluster refactor (hygiene)

**Goal:** reduce App.css's 116 `!important` declarations across ~6 clusters
by refactoring specificity.

### 7.1 The clusters (from PR #150's audit)

1. `.column-remove-btn` — ~20 declarations
2. Button-override stack (`.btn`, `.btn-primary`, `.btn-ghost`, etc.) — ~20
3. Modal close button — ~10
4. Filter chip variants — ~15
5. Density-mode overrides (cozy vs compact) — ~25
6. Scrollbar / overlay overrides — ~10
7. The rest — scattered ~16

Total ~116 (matches grep count).

### 7.2 Approach

For each cluster:
1. Identify the original specificity conflict the `!important` was patching.
2. Refactor to a single canonical selector with appropriate specificity (use
   `:where()` for low-specificity defaults, explicit classes for overrides).
3. Delete the `!important`s.

This is the highest-risk hygiene phase because of the breadth. **Recommendation:** do
clusters in separate PRs (Phase 7a, 7b, ...) rather than one mega-PR, so each
is reviewable.

### 7.3 Exit criteria

- [ ] Cluster N's `!important` count drops to zero.
- [ ] Visual diff for the cluster's surface is null.
- [ ] No new specificity hacks introduced (no `[class][class]` chains).

---

## Phase 8 — Modal pattern (ProductDetailModal + ChainReviewModal)

**Goal:** port modal dialogs to the Bauhaus vocabulary (hard borders, no
rounded corners, no soft shadows).

### 8.1 Scope

The mock does not include a modal, but the vocabulary is derivable:
- Backdrop: solid `var(--ink)` at 60% opacity (no blur).
- Modal frame: 2px ink border, no rounded corners, no drop shadow.
- Header: Oswald uppercase with 2px ink bottom rule.
- Close button: square ink-bordered cell with stamp-red hover (mirror the
  filter-chip `.close` from Phase 5).
- Footer: top-ruled action bar with right-aligned hard-bordered buttons.

### 8.2 Files touched

- `app/frontend/src/components/ProductDetailModal.tsx` + `.css`
- `app/frontend/src/components/ChainReviewModal.tsx`
- `app/frontend/src/components/ui/ConfirmDialog.tsx` + `.css`
- `app/frontend/src/components/ui/FeedbackModal.tsx` + `.css`

### 8.3 Exit criteria

- [ ] All modals share the same hard-border + no-shadow vocabulary.
- [ ] No native `<dialog>` UA chrome (already enforced by the no-native-chrome rule).
- [ ] Modal close button has stamp-red hover.
- [ ] Existing modal-flow tests pass.

---

## Phase 9 — BuildPage

**Goal:** port the Build flow to Bauhaus.

### 9.1 Notes

BuildPage has its own CSS file already (`BuildPage.css`, 228 lines). Phase 9
rewrites that file against the mock's vocabulary. The page-level shape
(stepper / tray / canvas) stays; only the visual layer changes.

### 9.2 Risk

Medium. Build flow has integration tests (`BuildPage.test.tsx`,
`BuildTray.test.tsx`); they assert DOM structure, not visual styling, so
should survive. Verify regardless.

---

## Phase 10 — AdminPanel + DatasheetsPage

**Goal:** port the admin surfaces (AdminPanel, DatasheetsPage,
ProductManagement) to Bauhaus.

### 10.1 Notes

Largest phase by file count (`AdminPanel.css` 247 lines,
`ProductManagement.css` 258 lines). Mostly tabular layouts that should map
cleanly onto Phase 3's table vocabulary.

### 10.2 Risk

Medium. Admin surfaces have less test coverage than the catalog flow;
manual smoke is important here. Run `./Quickstart admin` flows end-to-end.

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Specificity refactor (Phase 7) breaks an obscure override path | medium | Per-cluster PRs, not mega-PR; visual diff per cluster |
| Rail width change (Phase 4: 380 → 280) breaks at density-compact | low | Test all density modes before merge |
| Stat-row derivation (Phase 2) is wrong for an under-tested product type | medium | Stat selection static-per-product-type, not dynamic; explicit per-type test fixtures |
| Z-index mappings (Phase 6) miscategorise a `z-index: 1000` as popover when it's actually modal | low | Manual layering review; screenshot diff |
| App.css mega-file conflicts on every parallel PR | high (already burning) | Aggressive per-component CSS extraction during each phase (already the established pattern) |
| Modal vocabulary divergence between Phase 8 modals and Phase 5 chips' close-X | low | Both reference same `.close` selector or a shared `--close-button-pattern` mixin |

---

## What this deliberately does not do

- **Dark theme rework.** The espresso variant derives from the same tokens
  and follows automatically per surface. Validate at each phase's exit;
  don't dedicate a phase to it.
- **Mobile redesign.** The mock has one media query at 980px (rail collapses
  to top bar, stat-row to 2-column). Each visual phase includes its own
  responsive behaviour; no separate mobile phase.
- **Frontend logic.** This is CSS + minimal JSX className/structure changes
  only. No state-management or data-fetching changes.
- **Welcome page and AuthModal.** Outside the catalog flow, distinct visual
  identity. Out of scope.
- **Backend, API, infrastructure.** Untouched.
- **Replacing existing component libraries.** No `shadcn`, no `radix`,
  no `MUI`. The app-native primitives pattern stays per CLAUDE.md.
- **New design tokens.** Phases 2–10 consume the tokens added in PR #150
  and nothing else. If a phase needs a new token, surface it for review
  before adding.

---

## Triggers — when to revisit this doc

- Wave-N PR opened → mark Phase N as `🚧 in flight` here.
- Wave-N PR merged → mark Phase N as `✅ shipped <date>`.
- Mock changes (`docs/design/bauhaus-catalog.html`) → re-read the surface
  table in §1 to see which phases the diff affects.
- Audit re-runs (`grep -c '!important' App.css`, `grep -c 'z-index' App.css`)
  and the counts increase → flag the regression on the relevant phase.

---

## References

- [`docs/design/bauhaus-catalog.html`](../docs/design/bauhaus-catalog.html) — target visual mock (829 lines, self-contained)
- [`docs/design/index.html`](../docs/design/index.html) — design lab with all three explored directions
- [PR #136](https://github.com/JimothyJohn/specodex/pull/136) — three direction explorations; Bauhaus picked
- [PR #150](https://github.com/JimothyJohn/specodex/pull/150) — wave 1 (tokens + header + filter chip + Oswald column headers)
- [CLAUDE.md](../CLAUDE.md) — "No native browser/OS chrome" and "Per-component CSS files" rules that constrain every phase

---

## Appendix A — Phase 1 recovery diff (captured 2026-05-12)

The dirty working-tree edit lost in cross-session reflog churn. Re-apply
this to `app/frontend/src/App.css` near line 6740 as the entire content of
Phase 1.

```diff
@@ -6740,16 +6740,18 @@ input.transmission-param-input {
   font-weight: 600;
   letter-spacing: 0.16em;
   text-transform: uppercase;
-  /* Bauhaus stencil label. Column headers were proportional-cased mono
-   * with `nowrap + ellipsis`, which clipped long attribute names into
-   * "Re…" — one of the two truncation surfaces the audit called out.
-   * Switching to Oswald uppercase reads as a manual heading and the
-   * ellipsis is no longer the only resort because the headline font
-   * is condensed. We still clip on very long labels, but at the cell
-   * width it now takes a *real* outlier to lose information. */
-  white-space: nowrap;
-  overflow: hidden;
-  text-overflow: ellipsis;
+  line-height: 1.15;
+  /* Wrap to multiple lines rather than ellipsis-clipping. PR #150
+   * switched to Oswald (condensed) on the theory that more characters
+   * would fit on one line, but at the live cell width (~52px after
+   * the sort indicator and remove button claim their share) even
+   * "Manufacturer" still clips to "MANUF…". Mirror the same wrap
+   * treatment used on .filter-attribute: word-break + overflow-wrap,
+   * no nowrap, no ellipsis. The header grows by ~17px on long labels
+   * — cheap given the column-header is already 208px tall. */
+  word-break: break-word;
+  overflow-wrap: anywhere;
+  min-width: 0;
 }

 .column-header-slider {
```

Selector context: this is inside `.column-header-label-text` in the
`/* === Column header (sticky table header cell) === */` region of
`app/frontend/src/App.css`. The selector starts at approximately line 6735
(check before applying — surrounding code may have shifted in any
intervening commits).
