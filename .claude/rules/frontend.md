---
paths:
  - "app/frontend/**"
---

# Frontend conventions

The one-line rules in `CLAUDE.md` always apply. This loads when you touch `app/frontend/`.

## Attribute derivation

Filter chips and results-table columns both derive their attribute list from a merge of **static per-type metadata** (rich display names, tuned units) and **records-derived attributes** (caught at runtime from the actual DynamoDB rows) — `app/frontend/src/types/filters.ts:deriveAttributesFromRecords` + `mergeAttributesByKey`. Adding a product type does not require editing `filters.ts`; the table auto-populates from whatever fields the records carry, with display names generated from the snake_case keys. Curated `getXxxAttributes()` lists are an override, not a requirement. User preferences (hidden columns, row density, column cap, sort direction) persist in `localStorage`.

## No native browser/OS chrome — every action stays inside the app

Specodex should feel like a designed app, not a browser tab with our colors painted on it. Every user action has an app-native visualization; when you reach for a built-in browser primitive, use (or build) the app-native equivalent.

**Banned by default** (use the app-native primitive instead):

| Native primitive | Use this instead |
|---|---|
| `title=` attribute (OS tooltip) | `app/frontend/src/components/ui/Tooltip.tsx` |
| `window.confirm()` / `confirm()` | `useConfirm()` hook + `<ConfirmDialog>` (`components/ui/ConfirmDialog.tsx`) |
| `window.alert()` / `alert()` | `useToast()` (`components/ui/Toast.tsx`) for non-blocking; `<ConfirmDialog>` if an explicit ack is required |
| Silent `console.error` in user-triggered flows | `useToast().error(...)` paired with the console log |
| `<form>` without `noValidate` (UA validation bubbles) | `noValidate` + JS validation + inline error — no shared form-field primitive exists yet; build one before adding a form |
| `<input type="checkbox">` without `appearance: none` | Styled checkbox (see the filter sidebar pattern) |
| `<select>` (OS dropdown) | `components/Dropdown.tsx` (single) or `components/MultiSelectFilterPopover.tsx` (multi) |
| `<input type="file">` (OS file picker) | Custom dropzone — none exists today; build before adding upload |
| `<input type="date|color|range|datetime-local">` (OS pickers) | Custom picker — none exists today |
| `<dialog>` (UA backdrop) | Custom modal pattern (see `components/ProductDetailModal.tsx`) |
| `<details>` / `<summary>` (UA disclosure triangle) | Custom collapse component |
| `target="_blank"` bare anchor | `<ExternalLink>` (themed icon + Tooltip + `rel="noopener noreferrer"`) |
| Default scrollbars | Scrollbars are themed globally (`*::-webkit-scrollbar` in `App.css`); don't ship per-element overrides that fight it |
| `window.print()` | Custom print stylesheet — none exists; build before adding print |
| `window.open()` (popup with OS chrome) | In-app modal/route |
| `<progress>` element (UA chrome) | `components/ui/ProgressBar.tsx` |

When a new feature's obvious implementation reaches for a row above, that is the signal you're about to ship native chrome: use the listed primitive, or build one and add a row to this table before shipping the feature.

**Drift gates.** `./Quickstart verify` (`cli/quickstart.py:_style_drift_check`) greps `app/frontend/src` for `title=` JSX attributes and `target="_blank"` outside `<ExternalLink>`; CI mirrors verify, so a regression PR is red before review. The toast and form-`noValidate` phases are not gated yet — reviewers hold that line. If the gate hits a false positive (an `<svg><title>` is already excluded by the pattern), allowlist the specific case rather than disabling the rule.

**Exceptions worth knowing.** Browser autofill on login forms (`:-webkit-autofill`) is intentionally left alone — password managers depend on it, and theming the yellow background hasn't been worth the complexity. The native context menu stays for non-interactive content (text selection, "Inspect"). If you suppress either, document why.

## Per-component CSS files

When adding a UI primitive under `app/frontend/src/components/ui/`, ship its CSS in a sibling `.css` file imported from the component, **not** appended to `App.css`. The mega-file's bottom edge was the universal merge target on every parallel feature PR. `ConfirmDialog`, `FeedbackModal`, `Toast`, `AnchoredPopover`, `SourcePopover`, and `ProgressBar` follow the pattern (`Tooltip` and `ExternalLink` predate it and still live in `App.css`).

- `components/ui/Foo.tsx` does `import './Foo.css';` near the top.
- `components/ui/Foo.css` holds the selectors that target Foo's own DOM.
- Cross-component selectors (typography reset, layout grid, color tokens, theme switches, the global scrollbar theme) stay in `App.css` — that's its remaining job.
- `Foo.test.tsx` doesn't need to import the CSS; vitest doesn't render styles.

`App.css` should keep shrinking as primitives extract. When a PR adds a selector that's clearly Foo-specific, push it into `Foo.css` even if you didn't author Foo.
