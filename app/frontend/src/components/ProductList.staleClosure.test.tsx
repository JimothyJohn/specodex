/**
 * ProductList stale-closure regression — 2026-05-23.
 *
 * User reported: "When I sort a column all of the filters seem to be reset."
 *
 * Root cause: ColumnHeader is React.memo'd with `arePropsEqual` that
 * intentionally ignores callback identity (onFilterChange, onSort, etc.) so
 * the catalog page's 20+ columns skip re-rendering during a slider drag.
 * But the inline callbacks in ProductList.tsx originally did
 * `setFilters([...filters, updated])` and `setSorts([...sorts, ...])` —
 * reading `filters` / `sorts` from closure. When memo skipped a column's
 * re-render, that column kept its stale callback, and on the next click
 * the spread clobbered any sibling chip values that had landed in between.
 *
 * Fix: every setFilters / setSorts callsite that's wired to a memo-ignored
 * callback must use the function-updater form (`setFilters(prev => ...)`).
 *
 * This test is a source-text check — it asserts the ProductList.tsx
 * source DOES NOT contain the buggy spread patterns inside the relevant
 * inline lambdas. A future refactor that reintroduces them fails here.
 *
 * (A full React Testing Library test would be stronger but requires
 * mounting ProductList with the full AppContext / AuthContext / data
 * fixture; this test catches the structural regression in <10 ms.)
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const productListPath = join(__dirname, 'ProductList.tsx');
const source = readFileSync(productListPath, 'utf-8');

describe('ProductList stale-closure regression (2026-05-23)', () => {
  it('does not read `filters` from closure inside spread setters', () => {
    // The buggy patterns: any `setFilters` whose first argument reads
    // `filters` directly (as opposed to `prev`). This catches both
    // `setFilters([...filters, x])` and `setFilters(filters.filter(...))`
    // and `setFilters(filters.map(...))`.
    //
    // Allowlist: `setFilters(reset.filters)` in handleProductTypeChange
    // is fine — it's a fresh array from defaultStateForType, no
    // closure-read of the state slice. Same for `setFilters([])` (Clear).
    const buggyPatterns = [
      /setFilters\(\[\.\.\.filters\b/,
      /setFilters\(filters\.filter\b/,
      /setFilters\(filters\.map\b/,
      /setFilters\(filters,/, // setFilters(filters, ...)
    ];
    for (const pat of buggyPatterns) {
      const hits = source.match(new RegExp(pat.source, 'g'));
      expect(
        hits,
        `ProductList.tsx contains a stale-closure setFilters pattern matching ${pat}; ` +
          'use setFilters(prev => ...) instead. See ColumnHeader.tsx arePropsEqual comment.',
      ).toBeNull();
    }
  });

  it('does not read `sorts` from closure inside spread setters', () => {
    const buggyPatterns = [
      /setSorts\(\[\.\.\.sorts\b/,
      /setSorts\(sorts\.filter\b/,
      /setSorts\(sorts\.map\b/,
      /setSorts\(sorts,/,
    ];
    for (const pat of buggyPatterns) {
      const hits = source.match(new RegExp(pat.source, 'g'));
      expect(
        hits,
        `ProductList.tsx contains a stale-closure setSorts pattern matching ${pat}; ` +
          'use setSorts(prev => ...) instead.',
      ).toBeNull();
    }
  });

  it('uses the function-updater form for the inline onFilterChange callback', () => {
    // The block that wires ColumnHeader's onFilterChange — the prop
    // identity is ignored by the memo, so this is the canonical place
    // for the bug to creep back in. Assert the prev-form is present.
    const onFilterChangeBlock = source.match(
      /onFilterChange=\{[\s\S]+?\}\}\s*$/m,
    );
    expect(onFilterChangeBlock).not.toBeNull();
    const block = onFilterChangeBlock![0];
    expect(block).toMatch(/setFilters\(prev =>/);
    // And does NOT spread `filters` from closure.
    expect(block).not.toMatch(/setFilters\(\[\.\.\.filters/);
    expect(block).not.toMatch(/setFilters\(filters\./);
  });

  it('uses the function-updater form inside handleColumnSort', () => {
    // Look for the handleColumnSort function body.
    const m = source.match(/handleColumnSort = [\s\S]+?\n  \};/);
    expect(m, 'could not find handleColumnSort body').not.toBeNull();
    const body = m![0];
    expect(body).toMatch(/setSorts\(prev =>/);
    expect(body).toMatch(/setFilters\(prev =>/);
    expect(body).not.toMatch(/setSorts\(\[\.\.\.sorts/);
    expect(body).not.toMatch(/setFilters\(\[\.\.\.filters/);
  });

  it('uses the function-updater form inside handleRemoveColumn', () => {
    const m = source.match(/handleRemoveColumn = [\s\S]+?\n  \};/);
    expect(m, 'could not find handleRemoveColumn body').not.toBeNull();
    const body = m![0];
    expect(body).toMatch(/setSorts\(prev =>/);
    expect(body).toMatch(/setFilters\(prev =>/);
  });
});
