/**
 * ProductList type-switch reset contract — Phase 3 of FRONTEND_TESTING.md.
 *
 * Pins the exact bundle of state ProductList resets when the user picks a
 * new product type. The bundle lives in `defaultStateForType`, which
 * `handleProductTypeChange` calls and applies setter-by-setter; testing
 * the bundle directly is enough to lock down L1–L3 of the spillover
 * bestiary. L4 (`currentPage` reset) is tested transitively — it's wired
 * to a useEffect on `filters, sorts, itemsPerPage`, and Phase 1's
 * persistence-key tests already pin those.
 *
 * The full DOM-level test (open modal → switch type → modal gone) is a
 * follow-up; it adds little safety beyond the bundle test as long as
 * `handleProductTypeChange` keeps calling `defaultStateForType` for
 * every slice it resets.
 */

import { describe, it, expect } from 'vitest';
import { defaultStateForType } from './ProductList';

describe('defaultStateForType', () => {
  it('clears selectedProduct + clickPosition (L1)', () => {
    const reset = defaultStateForType('motor');
    expect(reset.selectedProduct).toBeNull();
    expect(reset.clickPosition).toBeNull();
  });

  it('returns curated default filters per type (L2)', () => {
    const motorReset = defaultStateForType('motor');
    const driveReset = defaultStateForType('drive');
    // Both should produce arrays — the shape and per-type contents come
    // from buildDefaultFiltersForType, which is the source of truth and
    // owns its own coverage. We only assert that we got a fresh, typed
    // array (not stale state passed through).
    expect(Array.isArray(motorReset.filters)).toBe(true);
    expect(Array.isArray(driveReset.filters)).toBe(true);
    motorReset.filters.forEach(f => {
      expect(f.mode).toBe('include');
      expect(f.value).toBeUndefined();
    });
  });

  it('clears sorts (L3)', () => {
    expect(defaultStateForType('motor').sorts).toEqual([]);
    expect(defaultStateForType('drive').sorts).toEqual([]);
    expect(defaultStateForType(null).sorts).toEqual([]);
  });

  it('returns a fresh object each call (no shared state across switches)', () => {
    const a = defaultStateForType('motor');
    const b = defaultStateForType('motor');
    expect(a).not.toBe(b);
    expect(a.filters).not.toBe(b.filters);
    expect(a.sorts).not.toBe(b.sorts);
  });

  it('handles a null type (no selection) without throwing', () => {
    const reset = defaultStateForType(null);
    expect(reset.selectedProduct).toBeNull();
    expect(reset.sorts).toEqual([]);
    // filters may be empty for null type; just assert it's an array
    expect(Array.isArray(reset.filters)).toBe(true);
  });
});
