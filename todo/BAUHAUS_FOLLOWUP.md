# BAUHAUS port — follow-up status

> Companion to [`todo/BAUHAUS.md`](BAUHAUS.md) — that's the plan, this is
> the state. Pick this up when you sit back down. Last updated end of
> the 2026-05-12 session.

---

## TL;DR

The catalog page and every modal are Bauhaus-ported. What's left is
**BuildPage** (Phase 9), **AdminPanel + DatasheetsPage + ProductManagement**
(Phase 10), and the deferred **`!important` cluster sweep** (Phase 7).
Two visual threads I never nailed down: filter chip internals (you said
"internals look wrong beyond what I shipped" but the specific element
was never identified), and the slider "fine resolution" ask (continuous-
interpolation interpretation in PR #180 was wrong, reverted via #181;
the actual intent is undefined).

---

## What shipped today

Grouped by area, with PR links and one-line "what it does":

### Foundation
- [#158](https://github.com/JimothyJohn/specodex/pull/158) — `todo/BAUHAUS.md` plan doc (10-phase roadmap).

### Catalog page (top to bottom)
- [#170](https://github.com/JimothyJohn/specodex/pull/170) Phase 2a — `.page-toolbar` Bauhaus restyle (2px ink rule, Oswald numerals, brass percent, ink-on-paper Clear button).
- [#171](https://github.com/JimothyJohn/specodex/pull/171) Phase 2b — Collapsible `CatalogStatRow` component (4-cell stats over filtered set; drive + motor today, easy to extend).
- [#172](https://github.com/JimothyJohn/specodex/pull/172) Phase 3 — Catalog table outer frame + hairline cell rules + Oswald part numbers + mono mfg captions + dropped zebra striping.
- [#173](https://github.com/JimothyJohn/specodex/pull/173) Phase 4b — Column-header section treatment (2px ink underline, 0.16em tracking, hairline column separators).
- [#159](https://github.com/JimothyJohn/specodex/pull/159) Phase 1 — Column-header label wrap recovery (the lost edit).
- [#176](https://github.com/JimothyJohn/specodex/pull/176) — Column-header sort button can grow when label wraps (was clipping at fixed 22px).

### Filter rail
- [#161](https://github.com/JimothyJohn/specodex/pull/161) Phase 5 — Filter chip internals: brass op-prefix, hard-divided stamp-red close-X, dashed `+ ADD FILTER`.
- [#162](https://github.com/JimothyJohn/specodex/pull/162) Phase 4 partial — Filter sidebar width 380px → 280px (only affects DatasheetList — the catalog has no rail).

### Modal vocabulary (all four now share one)
- [#164](https://github.com/JimothyJohn/specodex/pull/164) Phase 8a — `ConfirmDialog` (reference implementation).
- [#165](https://github.com/JimothyJohn/specodex/pull/165) Phase 8b — `FeedbackModal` (inherits ConfirmDialog frame + form-internal styling).
- [#182](https://github.com/JimothyJohn/specodex/pull/182) Phase 8c — `ProductDetailModal` (incl. spec section heads; spec-table internals deferred).
- [#183](https://github.com/JimothyJohn/specodex/pull/183) Phase 8d — `ChainReviewModal`.

### Slider visual + perf
- [#177](https://github.com/JimothyJohn/specodex/pull/177) — Slider rail/region/thumb/value-pill Bauhaus port (squared, ink borders, dropped drop-shadow + halo stacks; thumb stays circular as a drag affordance).
- [#178](https://github.com/JimothyJohn/specodex/pull/178) — Initial perf attempt: `startTransition` on `onFilterChange` + local-thumb state. Helped but still laggy.
- [#179](https://github.com/JimothyJohn/specodex/pull/179) — Real perf fix: `DistributionChart`'s `anchorNumeric` memo had `products` in its deps even though the body resolved `allProducts ?? products` (always `allProducts` from ColumnHeader). Memo invalidated on every drag tick, walking 6k items + sorting per column. Hoisted the ternary out of the memo. Synchronous filter path works at 60Hz now. Dropped the startTransition.
- [#175](https://github.com/JimothyJohn/specodex/pull/175) — Sort indicator + sort-priority badge + alignment fix (dropped brass glow, squared the priority badge, centered the label-and-indicator pair).
- [#174](https://github.com/JimothyJohn/specodex/pull/174) — `useDeferredValue` on CatalogStatRow products + computed-column header wrap (`.product-grid-header-label`).

### Hygiene
- [#160](https://github.com/JimothyJohn/specodex/pull/160) Phase 6 — `App.css` z-index call-site migration (12 of 20 sites; 8 intentionally left as numeric for intra-component / decorative stacking).
- [#163](https://github.com/JimothyJohn/specodex/pull/163) Phase 6b — Per-component CSS z-index migration (Toast, ConfirmDialog, ProductManagement).

### Unrelated (still landed this session)
- [#155](https://github.com/JimothyJohn/specodex/pull/155) — `chore(dependabot)`: ignore uuid majors until Express deletion.
- [#169](https://github.com/JimothyJohn/specodex/pull/169) — `fix(smoke)`: retry once on known-transient AWS-edge deny (closes [#151](https://github.com/JimothyJohn/specodex/issues/151)).

### Reverts
- [#181](https://github.com/JimothyJohn/specodex/pull/181) — Reverted [#180](https://github.com/JimothyJohn/specodex/pull/180) (continuous-value slider) after you reported "it just got worse." See landmines below.

---

## What's left in the plan

| Phase | Surface | Effort | State |
|---|---|---|---|
| 7 | `!important` cluster sweep (116 across 36 selector leaves, 6 families) | M | **Deferred** — needs browser inspector to read specificity conflicts before refactoring |
| 9 | BuildPage | M | Untouched |
| 10 | AdminPanel + DatasheetsPage + ProductManagement | L | Untouched |

The catalog itself is **structurally done**. Phase 9/10 are non-catalog surfaces (build flow + admin tools).

---

## Open threads with no clear resolution

### 1. Filter chip internals

You said in this session: *"the chip internals look wrong beyond what I shipped."* When asked which element, you confirmed "yes" but never named the specific component. We pivoted into slider work and never came back. **Best guess:** the chip container itself (`.filter-chip-minimal`) was never ported — Phase 5 only touched the inside (operator, remove-X, +ADD FILTER), so the outer container still has soft `--border-color` and the prior background, while the inside is hard-Bauhaus. The mismatch reads as broken. Next pickup should ask you to confirm, then port `.filter-chip-minimal` (1px ink border, flat paper, squared corners — same vocabulary as the other chip-like cells).

### 2. Slider "fine resolution"

You said *"i want the slider to have a fine resolution."* I interpreted as continuous interpolation between catalog points (PR #180). You said *"it just got worse"* and we reverted via [#181](https://github.com/JimothyJohn/specodex/pull/181). The actual intent is undefined. Possibilities from the original menu that weren't picked:
- Continuous values (PR #180's interpretation — REJECTED)
- More pixels per value on the track (longer slider)
- Drop percentile mapping, go linear
- Something else entirely

**Don't re-attempt without asking what specifically "fine resolution" means.** Guessing again risks another revert.

### 3. Histogram bar styling

Bars are rendered inside `DistributionChart.tsx` (component-internal SVG/canvas, didn't trace fully). The slider track around them is now Bauhaus-vocabulary (PR #177) but the histogram bars themselves haven't been touched. Likely small fix — bars are probably already squared `<rect>` SVG; need a color/tracking review. Easy follow-up.

### 4. ProductDetailModal spec-table internals

PR #182 ported the modal frame + header + close + section heads, but left the spec-table internals (row label/value text, units, nested sub-tables) un-ported. Current treatment is readable; I called it "could read as over-touched" if pushed further into Bauhaus. Up to you whether to polish or leave.

### 5. The 8 inline z-indexes in App.css

PR #160 migrated 12 of 20 sites; 8 remain (intra-component stacking + `::before` decorations like vignette + scan-lines). PR #160's description asked: *add a `--z-local` / `--z-decoration` token, or leave numeric?* No answer yet. Leaving numeric with comments is the safer default.

---

## Landmines (where I made wrong calls today)

These are documented so you don't have to dig through the conversation to find them.

### Parallel-session HEAD shift in shared worktree

Mid-session another agent was active in `/Users/nick/github/specodex` working on `auto/drift-audit-ci-152-20260512` (PR #168). Between my `git checkout -b fix/smoke-retry-...` and my `git commit`, their session moved HEAD to their branch, so my smoke-retry commit (`c3f7fc9`) landed on top of THEIR work-in-progress. I recovered via cherry-pick onto the correct branch, and they cleanly rebased my mistaken commit out of their branch.

**Lesson for next session:** run `git branch --show-current` immediately before every `git commit` in this shared worktree, not just at session start. The parallel sessions are real and HEAD moves silently.

If you want isolated sessions in future, `git worktree add ../specodex-bauhaus design/bauhaus-port-tokens-header-table-20260510` would have prevented this.

### Continuous slider PR #180

Interpreted "fine resolution" wrong. Reverted via #181. Listed in open threads above. **Ask before re-attempting.**

### Phase 4b's "rail port" doesn't apply

The plan's Phase 4 assumed the catalog had a left filter rail. Surveying the React app: it doesn't. The catalog uses per-column filter UI (histogram + slider + operator inside each `ColumnHeader`). The mock's rail-section-with-marker pattern translated to "column header section treatment" — PR #173 added a 2px ink underline + 0.16em tracking + hairline column separators. **If you want the full mock layout (left rail with type list + filter chips), that's a structural UX shift, not a CSS port.** Phase 4 in the plan is over-spec'd against the actual app.

### File-state collision in `auto/drift-audit-ci-152-20260512` worktree

When I checked out the parallel-session branch by mistake, my `git add` picked up the OTHER session's staged `drift-audit.yml` file along with my test files. The cleanup involved a `git reset --mixed HEAD~1` + cherry-pick. If you see commits mixing concerns in shared worktrees in future, that's a likely cause.

---

## Recommended next pickup

In order of estimated value:

1. **Resolve the filter chip internals thread.** Ask Nick which specific chip element looks wrong; my guess is `.filter-chip-minimal` container. If yes, single small PR (1 selector, ~20 lines CSS).
2. **Phase 9 — BuildPage.** Self-contained (`BuildPage.css` already exists at 228 lines). One PR. Apply same modal/chip/table vocabulary patterns as the catalog port. Medium effort.
3. **Histogram bar styling in `DistributionChart`.** Probably a small SVG/color tweak inside the component. Low effort, high mono-soup reduction.
4. **Phase 10 — AdminPanel + DatasheetsPage + ProductManagement.** Larger phase. Three separate files (`AdminPanel.css` 247 lines, `ProductManagement.css` 258 lines, plus DatasheetsPage selectors in App.css). Could be three PRs.
5. **Phase 7 — `!important` sweep.** Highest risk, lowest velocity per LOC. Needs browser-inspector pass; do per-cluster (column-remove-btn first, then button overrides, etc.). Six families, six PRs.

Skip until specifically asked:
- Slider "fine resolution" (open thread #2)
- ProductDetailModal spec-table internals (open thread #4)
- The 8 remaining inline z-indexes (open thread #5)

---

## Verification checklist (5 min)

Before assuming today's work is good:

- `./Quickstart dev`, drive or motor catalog
- Toolbar: 2px ink rule beneath, Oswald numerals, brass percent, ink-on-paper Clear hover
- Stat-row beneath toolbar: 4 cells with hard ink borders + 8×8 corner marks; toggle to collapse persists in localStorage
- Table: 1px ink frame, no zebra striping, Oswald part numbers, mono mfg captions, hairline cell rules
- Column headers: 2px ink underline, sort arrows are clean (no glow), sort-priority chips are squared brass-border (not round brass-fill), labels wrap on overflow
- Sliders: hard ruled line, brass-filled active region, clean ink-bordered circular thumb
- Click any product → modal: ink backdrop, 2px ink frame, Oswald title with rule, stamp-red close-X hover
- Trigger any confirm dialog → same modal frame, joined-button action strip
- Feedback modal → same frame, hard-bordered form fields
- Build a motor+drive pair → chain review modal → same frame

If anything looks wrong, the PR is named per phase in the table above — easy to revert individually.
