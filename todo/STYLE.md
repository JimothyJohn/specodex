# STYLE — eliminate native browser/OS UI from the app

> **Goal.** Every user action stays inside Specodex's visual language.
> No `alert`/`confirm` boxes, no native tooltips, no UA scrollbars, no
> UA validation bubbles, no UA file pickers, no UA dropdowns. The app
> feels like an app, not a browser tab with our colors painted on it.

The frontend is already 80% of the way there: focus rings are styled,
modals are custom, single-select dropdowns use a custom `Dropdown.tsx`,
and there are no native `<dialog>`, `<details>`, `<select>`, file
pickers, drag-drop, or `window.open` calls. **What remains is the
long tail of native chrome that still shows up daily** — `title` tooltips,
`window.confirm`, `alert`, validation bubbles, and unstyled scrollbars.

Plan is sequenced by **leverage**: each phase ships one new primitive,
then migrates every call site onto it. After each phase the surface
it owns is *closed* — no new code is allowed to introduce native chrome
in that category, and a lint rule enforces it.

---

## Inventory (snapshot 2026-05-02)

Counts come from a full sweep of `app/frontend/src/`. File:line refs
live in the per-phase sections below.

| Surface | Count | Owner phase |
|---|---|---|
| `title=` native tooltips | 36 | Phase 1 |
| `window.confirm()` | 2 | Phase 2 |
| `alert()` | 1 | Phase 2 |
| Console-only error paths (no toast) | ~25 | Phase 3 |
| Forms with `required` + no `noValidate` (UA validation bubbles) | 9 | Phase 4 |
| Native `<input type="checkbox">` w/o `appearance: none` | 1 | Phase 4 |
| Scrollable containers without custom `::-webkit-scrollbar` | 17 | Phase 5 |
| `target="_blank"` external links (UA "leaving site" affordance) | 3 | Phase 6 |
| `navigator.clipboard` with no in-app feedback | 1 | folded into Phase 3 |

Already clean (do **not** touch): native `<select>` (zero), native
file picker (zero), `<input type="file">` (zero), `<input type="date|color|range>"` (zero),
native `<dialog>` (zero), native `<details>`/`<summary>` (zero),
`onContextMenu` overrides (zero needed — none introduced),
`window.open` (zero), `window.print` (zero), `<progress>` (zero),
focus rings (all UA outlines replaced).

---

## Design principles

1. **One primitive per surface.** Tooltip, Toast, Confirm, Dialog,
   FormField, Scrollable. Each lives in `app/frontend/src/components/ui/`,
   has a `.test.tsx`, and is the *only* sanctioned way to do that thing.
2. **Portal + theme-aware.** Floating UI (tooltips, toasts, confirms,
   menus) renders into a portal so z-index can't trap them, and reads
   from the same CSS custom properties (`--surface`, `--text`, `--accent`,
   etc.) the rest of the app uses. Light/dark mode is automatic.
3. **Keyboard-first.** Every primitive supports Tab/Shift+Tab, Esc,
   Enter, and arrow keys where it applies. No mouse-only affordances.
4. **No regressions allowed.** After each phase, ESLint + a custom
   rule (or grep CI step) blocks new uses of the native API the phase
   replaced. Drift is what got us here.
5. **Prefer extending existing primitives.** `Dropdown.tsx`,
   `MultiSelectFilterPopover.tsx`, the modal components, and the
   `ErrorBoundary` are good. Don't rewrite them — wrap, refactor,
   or extract shared bits as we go.

---

## Phase 1 — Tooltip primitive (replaces 36 `title=` attributes)

The `title` attribute renders the OS tooltip after a 1.5s delay,
in a system font, with no theming. Every other piece of chrome we
build looks designed; tooltips look like a Windows 95 leftover.

### 1.1 Build `app/frontend/src/components/ui/Tooltip.tsx`

- `<Tooltip content={...} placement="top|bottom|left|right" delay={300}>`
  wraps any element, listens to `mouseenter`/`focus` on the child,
  positions a portaled `<div role="tooltip">` with CSS custom-property
  styling. Use a small floating-position helper or hand-roll with
  `getBoundingClientRect` + `position: fixed`. **Do not** add
  `@floating-ui/react` — it's 5KB+ and we only need 4 placements.
- Accessible: child gets `aria-describedby`; tooltip has `role="tooltip"`.
- Pointer-leave hides; Esc hides; click-elsewhere hides; scroll re-positions.
- Visual spec: 13px text, `--surface-elevated` background, 1px border in
  `--border`, 6px radius, 8px padding, drop-shadow `--shadow-md`,
  caret pointing at anchor. Match the `MultiSelectFilterPopover`
  vocabulary so tooltips and popovers feel like one family.
- Test (`Tooltip.test.tsx`): renders content on hover, hides on Esc,
  positions itself when target is near a viewport edge.

### 1.2 Migrate all 36 call sites

**ProductList.tsx:946, 1066, 1166** — header buttons.
**BuildTray.tsx:133, 158, 184, 193** — slot/bom/audit buttons.
**ColumnHeader.tsx:435, 452, 479 (empty — delete), 564, 583, 593, 621** —
column header affordances.
**ProductDetailModal.tsx:231** — datasheet PDF link.
**DatasheetList.tsx:253** — delete datasheet.
**DistributionChart.tsx:290, 305, 402** — chart bar hover (this one
*needs* the tooltip to follow the cursor; spec the API to support
a `followCursor` mode or use a separate ChartTooltip variant).
**AdminPanel.tsx:473** — dry-run gate.
**ThemeToggle.tsx:31** — light/dark switch.
**AccountMenu.tsx:62** — email overflow.
**DensityToggle.tsx:26** — density label.
**Welcome.tsx:211** — mark badge.
**GitHubLink.tsx:11** — source link.
**FilterChip.tsx:578, 586, 593, 638, 655, 716, 762** — chip controls.
**MultiSelectFilterPopover.tsx:180, 190, 200** — header buttons.
**CompatBadge.tsx:24** — compat detail.

For the `ColumnHeader.tsx:479` empty `title=""`, just delete the prop —
that's a leftover. For dynamic titles (e.g. "Click to switch units to
{unit}"), pass the same string as `content`.

### 1.3 Lint rule

Add an ESLint rule (or a grep step in `Quickstart verify`) that fails
the build on any `title=` JSX attribute under `app/frontend/src/`.
Allowlist only `<svg><title>` (semantic, not the tooltip).

**Acceptance.** Hovering any of the 36 surfaces shows a themed tooltip
in both light and dark mode, with no native OS tooltip ever appearing.
`grep -RnE 'title=["{]' app/frontend/src/components/` returns 0 results
(after excluding `<svg>` titles).

---

## Phase 2 — Confirm + Alert dialog primitive (replaces `window.confirm` and `alert`) ✅ shipped 2026-05-07

`window.confirm` and `alert` block the JS thread, render in the OS's
modal style, and can't be themed or animated. Three call sites today;
zero allowed after this phase.

**What landed:** `app/frontend/src/components/ui/ConfirmDialog.tsx`
exports `ConfirmProvider` + `useConfirm()`. The provider holds at most
one pending confirm and resolves a `Promise<boolean>`. Mounted in
`App.tsx` next to `AppProvider`. Esc / backdrop click / Cancel resolve
`false`; Enter / Confirm resolve `true`. Returns focus to the trigger
on close. Theme matches `--bg-primary` / `--border` / `--accent-primary`
tokens; danger variant uses the existing `#b03232`. The native `alert`
in `DatasheetEditModal.tsx:65` is **deliberately not migrated** — that's
a Phase 3 toast scenario per the plan.

Migrated sites: `ProjectsPage.tsx`, `ProjectDetailPage.tsx`,
`DatasheetList.tsx`. 7 new unit tests on the primitive itself + 3
existing tests updated to drive the dialog instead of stubbing
`window.confirm`.

### 2.1 Build `app/frontend/src/components/ui/ConfirmDialog.tsx` + `useConfirm()` hook

- Imperative API mirroring `confirm()`'s ergonomics so the migration
  is a one-line swap:
  ```tsx
  const confirm = useConfirm();
  if (!(await confirm({
    title: "Delete project?",
    body: "This removes the project and all its product references. This cannot be undone.",
    confirmLabel: "Delete",
    confirmVariant: "danger",
  }))) return;
  ```
- Implementation: a single `<ConfirmProvider>` mounted in `App.tsx`
  next to `AppProvider`. The provider owns a `useState` queue and
  renders one `<Dialog>` at a time. The hook returns a function that
  pushes onto the queue and returns a `Promise<boolean>`.
- The `Dialog` component itself becomes a reusable primitive (Phase 2
  ships both — `Dialog.tsx` for general use, `ConfirmDialog.tsx` as a
  preset on top).
- Visuals: same modal vocabulary as `ProductDetailModal`/`AuthModal`
  (overlay backdrop, centered card, Esc to close, focus trap, return
  focus to trigger). Danger variant uses the existing destructive
  red token — don't introduce a new color.
- Replace `alert()` with `useAlert()` (same shape, single button,
  resolves on dismiss). Or fold into the toast system in Phase 3 —
  decide when we get there. **Default: keep alerts as a one-button
  confirm dialog** when the message demands an explicit ack
  (`DatasheetEditModal.tsx:65` is "failed to update" — that's a toast,
  not a dialog — see Phase 3).

### 2.2 Migrate

- **`ProjectsPage.tsx:86`** — `window.confirm("Delete project...")` →
  `await confirm({ title: "Delete project?", confirmVariant: "danger" })`.
- **`DatasheetList.tsx:102`** — `window.confirm('Are you sure you want
  to delete this datasheet?')` → same pattern, danger variant.
- **`DatasheetEditModal.tsx:65`** — `alert('Failed to update datasheet')`
  → demote to a toast in Phase 3. **Don't migrate to a dialog** — failed
  updates shouldn't block the UI; the user should see the error and keep
  going.

### 2.3 Lint rule

Block `window.confirm`, `window.alert`, bare `confirm(`/`alert(` calls
under `app/frontend/src/`. Allowlist only test mocks.

**Acceptance.** Deleting a project / datasheet shows a themed dialog
that traps focus, animates in, and respects Esc/Enter. `grep -RnE
'\b(window\.)?(confirm|alert)\(' app/frontend/src/` returns 0 results.

---

## Phase 3 — Toast / notification system (closes ~25 silent failure paths)

Today, when an API call fails, the user sees nothing — the error goes
to `console.error` and the optimistic state silently reverts. This
phase makes failure visible *inside* the app instead of via the
browser's dev tools.

### 3.1 Build `app/frontend/src/components/ui/Toast.tsx` + `useToast()` hook + `<ToastProvider>`

- Imperative API:
  ```tsx
  const toast = useToast();
  toast.error("Couldn't update product", { detail: err.message });
  toast.success("Copied BOM to clipboard");
  toast.info("Refreshing categories…");
  ```
- Provider mounts in `App.tsx` *outside* `AppProvider` so the API client
  can reach it (or, simpler: provide a singleton emitter and let the
  provider subscribe — same pattern `react-hot-toast` uses).
- Visuals: bottom-right stack, max 4 visible, auto-dismiss after 5s
  (errors stick longer — 8s — and have a manual close), slide-up
  animation, themed via existing CSS custom properties. Variants:
  `success` (accent green), `error` (destructive red), `info` (neutral).
- Accessibility: `role="status"` for info/success, `role="alert"` for
  errors. Toast region is `aria-live="polite"`.
- Test: `Toast.test.tsx` covers stacking, auto-dismiss timer, manual
  close, error variant rendering.

### 3.2 Wire up the silent failure paths

**`AppContext.tsx`** — every `console.error` in a user-triggered mutation
gets a paired `toast.error()`:
- `:500` add product failed
- `:528` create datasheet failed
- `:575` update product failed
- `:652` delete product failed
- `:697` force refresh failed

**Background refreshes (`:284`, `:350`, `:411`, `:284`)** stay silent —
those are intentional. Document the distinction in `AppContext.tsx`
with a one-line comment at each retained `console.warn` so future
agents don't add a toast there reflexively.

**`api/client.ts:170, 197, 205`** — these are too low-level to know the
user intent. Leave the `console.error`/`console.warn` for debugging,
let the call sites decide whether to toast.

**`AuthContext.tsx:170`** — refresh-failed → logout. This is already
disruptive (the user is bounced back to login); add `toast.info("Session
expired — please log in again.")` so it doesn't feel like a glitch.

**`DatasheetsPage.tsx:67`** — keep the `console.error`, replace the
not-yet-migrated alert pattern with `toast.error("Failed to submit
datasheet", { detail: error.message })`.

**`DatasheetEditModal.tsx:65`** (the alert from Phase 2) — `toast.error("Failed
to update datasheet")`.

**`BuildTray.tsx:115`** — the `navigator.clipboard.writeText()` catch
silently swallows failures. Add `toast.success("Copied BOM")` on success
and `toast.error("Couldn't copy to clipboard")` on failure.

**`ProductManagement.tsx:36`** — admin fetch failures → `toast.error()`.

### 3.3 Lint rule

Add a soft warning (not error) for `console.error` in component files —
the convention going forward is "console for debugging *and* toast for
the user." Don't block; just nudge.

**Acceptance.** Disconnect the dev backend, click "Add product" — a
themed toast appears in the corner with the error message. Reconnect
the backend and click "Copy BOM" — a success toast appears. None of
this requires opening DevTools.

---

## Phase 4 — Form validation + checkbox styling (replaces UA validation bubbles)

When a `required` field is empty on submit, browsers render their own
tooltip-style "Please fill out this field" bubble in the OS font,
attached to the input. We need to take that over.

### 4.1 Add `noValidate` to every form, validate in JS

The 9 forms that currently rely on UA validation:
- `AuthModal.tsx:168, 204, 239, 276, 299` (login, register, confirm,
  forgot, reset)
- `DatasheetsPage.tsx:162` (create datasheet)
- `DatasheetEditModal.tsx:91` (edit datasheet)
- `AddToProjectMenu.tsx:240` (no required fields — just add `noValidate`
  for consistency)
- `ProductManagement.tsx:245` (no required fields — same)

For each form:
1. Add `noValidate` to the `<form>`.
2. In `onSubmit`, validate with explicit JS — empty string checks,
   email regex (don't rely on `type="email"` UA validation), URL
   parsing for `type="url"`.
3. On invalid submit, set per-field error state and render the error
   *inline* below the input in the existing form-error styling
   (already present in `AuthModal.tsx` for server errors — extend it
   to client validation).

### 4.2 Build `app/frontend/src/components/ui/FormField.tsx`

A thin wrapper that owns the label + input + inline error pattern.
Most forms already do this manually; consolidating makes it impossible
to ship a form *without* the inline error slot. Migrate the 9 forms
to use it.

### 4.3 Style the one native checkbox

`AddToProjectMenu.tsx:224` is the only `<input type="checkbox">` rendered
without `appearance: none`. Either:
- Add a custom checkbox style block to `App.css` (preferred — it's a
  10-line rule with `appearance: none`, a sibling pseudo-element for
  the check, and focus styling), or
- Use a `<button role="checkbox" aria-checked>` pattern (more code,
  same outcome).

Prefer the CSS approach for parity with the rest of the codebase. The
filter sidebar uses styled checkboxes already — copy the pattern.

### 4.4 Lint rule

Block `<form>` JSX without `noValidate` under `app/frontend/src/`
(allowlist tests). Block `type="email"` and `type="url"` on inputs
where validation is server-driven anyway — keep them only as
keyboard-hint affordances on mobile, never for validation.

**Acceptance.** Submit any form with empty required fields — the error
appears inline below the input in our typography, never as a UA bubble.
Tab through forms — focus rings are ours, not the UA default.

---

## Phase 5 — Scrollbar styling

17 scrollable containers currently render the OS scrollbar (which on
macOS is fine, but on Windows is a chunky gray block, and in either
case is a different visual language from our chrome).

### 5.1 Add a global custom-scrollbar block in `App.css`

One rule, applied to every scrollable surface:

```css
.scrollable, .filter-sidebar, .results-main, .results-sidebar,
.product-detail-content, .product-grid-scroll, .filter-input,
.attribute-selector-list, .add-to-project-list, .compat-candidate-list,
.multi-filter-popover-list, .custom-dropdown-list,
.transmission-slider-container, .filter-sidebar-mobile,
.admin-panel-form, .page-products-layout .results-main {
  scrollbar-width: thin;
  scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
}
.scrollable::-webkit-scrollbar { width: 8px; height: 8px; }
.scrollable::-webkit-scrollbar-track { background: var(--scrollbar-track); }
.scrollable::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border-radius: 4px;
}
.scrollable::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-thumb-hover); }
```

(The class list above just enumerates the 17 sites; the cleaner
approach is **a single utility class `.scrollable`** that we apply
to every overflowing container, and then drop the long selector
list. Refactor as we go.)

### 5.2 Add the tokens

In the `:root` and `[data-theme="dark"]` blocks at the top of
`App.css`, add:
- `--scrollbar-track: transparent`
- `--scrollbar-thumb: <subtle border color>`
- `--scrollbar-thumb-hover: <slightly stronger border color>`

### 5.3 Refactor sites to use `.scrollable`

Touch each of the 17 line refs in `App.css` to add the class on the
JSX side, drop the per-component scrollbar style if any. The header
nav (`.header nav`, line 5652) already has `display: none` on the
scrollbar — keep that, it's intentional (overflow-x: auto with hidden
scrollbar = a swipeable nav row).

**Acceptance.** Open the filter sidebar and scroll — the scrollbar
matches the app theme in both light and dark mode. Test on a Windows
VM (or Chromium with `forcedColors`) to confirm — that's where the
worst native scrollbar lives.

---

## Phase 6 — External link affordance (3 `target="_blank"` sites)

External links jump the user out of the SPA into a new tab. That's
fine — but the *signal* should be ours: a small "↗" icon next to the
link text, and a tooltip ("Opens in a new tab — datasheet PDF").
Today these links look identical to internal nav.

### 6.1 Build `<ExternalLink href={...} children />`

- Renders `<a target="_blank" rel="noopener noreferrer">` (security:
  `noopener` blocks the new tab from accessing `window.opener`).
- Appends a 12px arrow-up-right icon styled as inline-flex.
- Wraps the whole thing in a Phase 1 Tooltip with placement="top".
- Tests cover the `rel` attribute (security regression guard) and
  the icon presence.

### 6.2 Migrate

- `ProductDetailModal.tsx:228` — datasheet PDF.
- `DatasheetList.tsx:222` — datasheet link.
- `GitHubLink.tsx:8` — source.

### 6.3 Lint rule

Block bare `target="_blank"` JSX outside `ExternalLink.tsx`. The rule
also catches the `rel` security gap that's already present in some of
the sites above (none of them currently set `rel="noopener noreferrer"`).

**Acceptance.** Hover any "↗" link — the tooltip explains where it
goes. Click — it opens in a new tab without leaking `window.opener`.

---

## Phase 7 — Verification + drift gates

Once the six phases ship:

1. **Add a `Quickstart verify` step** that greps for the forbidden
   patterns:
   - `title=` (excluding `<svg><title>`)
   - `window.confirm`/`window.alert`/bare `confirm(`/`alert(`
   - `<form` without `noValidate`
   - `target="_blank"` outside `ExternalLink.tsx`
   - `overflow: auto` / `overflow-y: auto` / `overflow-x: auto` in CSS
     without an accompanying `::-webkit-scrollbar` selector or
     `.scrollable` class on the JSX side (heuristic; allowlist via a
     short list of OK selectors).

   Fail the verify on hits. CI mirrors `Quickstart verify`, so any
   regression PR is red before review.

2. **Visual smoke test.** Add to `tests/post_deploy/` (or a new
   `tests/visual/`) a Playwright run that:
   - Hovers an element with a tooltip and asserts the themed `[role="tooltip"]`
     appears (and that no UA tooltip is reachable — heuristic: assert
     the element has no `title` attribute).
   - Triggers a delete-confirm and asserts a custom `[role="dialog"]`
     mounts.
   - Triggers an API failure (mock the endpoint) and asserts a toast
     `[role="alert"]` mounts.
   - Submits an empty `required` form and asserts an inline error
     appears (and no `:invalid` UA bubble — checkable via the
     `validity.valid` API).

3. **Dark-mode parity.** Run the same Playwright run with
   `[data-theme="dark"]` set and screenshot-diff against a baseline.
   Catches the most common regression: a new primitive that hardcodes
   a light-mode color.

---

## Sequencing & effort

| Phase | Surface | Files touched | Effort | Reversible? |
|---|---|---|---|---|
| 1 | Tooltip | 1 new + ~15 components | 🟢 1 day | Yes — tooltip primitive can be backed out per-site |
| 2 | Confirm/Dialog | 1 new + 3 sites | 🟢 ½ day | Yes |
| 3 | Toast | 1 new + ~10 sites in `AppContext` + scattered | 🟡 1-2 days | Yes |
| 4 | Form validation + checkbox | 1 new (FormField) + 9 forms | 🟡 1-2 days | Yes — `noValidate` is the only risky bit; if JS validation has a gap, the UA bubble was masking it |
| 5 | Scrollbars | `App.css` only, ~17 selectors | 🟢 ½ day | Yes |
| 6 | External link | 1 new + 3 sites | 🟢 ½ day | Yes |
| 7 | Drift gates + visual smoke | `Quickstart verify` + Playwright | 🟡 1 day | n/a |

**Total**: ~5-6 working days, parallelizable across phases 1/2/5/6
(low-risk, isolated) and 3/4 (touch shared state, do these single-stream).

**Suggested merge order**: 1 → 5 → 6 → 2 → 4 → 3 → 7. Tooltip (1) and
scrollbars (5) are pure-additive and unblock nothing — ship them first
to bank visible polish wins. Confirm (2) and form validation (4) before
toasts (3) so the toast wiring doesn't double back through the migrated
forms. Drift gates (7) last so they don't fight in-flight migrations.

---

## Out of scope

These are *also* native UI surfaces, but we don't have them today and
introducing them is its own decision:

- **Native file picker** (`<input type="file">`) — not needed; uploads
  go through the API directly, never the browser. If/when we add
  drag-and-drop file ingest, we build a custom dropzone *first*.
- **Date/time pickers** — no date inputs exist. If a feature needs one,
  build a custom picker; never use `<input type="date">`.
- **Print styles** — no `window.print()` call site, no current need.
  If we add a "Print BOM" feature, it's a custom print stylesheet,
  not a default-print fallback.
- **Native context menus** — leaving the OS context menu in place is
  *fine* for non-interactive content (selecting text, "Inspect" for
  power users). Don't suppress unless we have a specific app-native
  context action to put there.
- **Browser autofill styling** — current login forms work with browser
  password managers, which is desirable. We're not styling
  `:-webkit-autofill` until we hear a complaint about the yellow
  pre-fill background not matching the theme.

---

## Triggers — when to surface this doc

Add a row to `todo/README.md`'s trigger table:

| Trigger | Surface |
|---|---|
| New JSX with `title=`, `window.confirm`, `alert`, `target="_blank"`, `<form>` without `noValidate`, or new scrollable container in CSS | [STYLE.md](STYLE.md) |
