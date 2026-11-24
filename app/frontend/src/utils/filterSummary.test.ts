/**
 * filterSummary — the one-line description a column trigger and an
 * active-filter chip render (UI_CLEANUP Phase 2, 2026-09-13).
 */

import { describe, it, expect } from 'vitest';
import {
  filterHasValue,
  formatCompactNumber,
  formatThreshold,
  summarizeFilter,
  sniffAttributeUnit,
} from './filterSummary';
import type { FilterCriterion } from '../types/filters';
import type { Product } from '../types/models';

const f = (over: Partial<FilterCriterion>): FilterCriterion => ({
  attribute: 'rated_torque',
  mode: 'include',
  displayName: 'Rated Torque',
  ...over,
});

describe('filterHasValue', () => {
  it('is false for the seeded valueless chip and empty shapes', () => {
    expect(filterHasValue(f({}))).toBe(false);
    expect(filterHasValue(f({ value: '' }))).toBe(false);
    expect(filterHasValue(f({ value: [] }))).toBe(false);
  });
  it('is true for zero, false, and non-empty values', () => {
    expect(filterHasValue(f({ value: 0 }))).toBe(true);
    expect(filterHasValue(f({ value: false }))).toBe(true);
    expect(filterHasValue(f({ value: 'ABB' }))).toBe(true);
    expect(filterHasValue(f({ value: ['ABB'] }))).toBe(true);
  });
});

describe('formatCompactNumber', () => {
  it('steps precision down with magnitude', () => {
    expect(formatCompactNumber(15700)).toBe('15,700');
    expect(formatCompactNumber(186.4)).toBe('186');
    expect(formatCompactNumber(24.63)).toBe('24.6');
    expect(formatCompactNumber(2.39)).toBe('2.39');
    expect(formatCompactNumber(0.037)).toBe('0.037');
    expect(formatCompactNumber(0)).toBe('0');
  });
  it('rounds integer-like units regardless of magnitude', () => {
    expect(formatCompactNumber(3.6, 'rpm')).toBe('4');
  });
  it('never emits NaN', () => {
    expect(formatCompactNumber(Number.NaN)).toBe('—');
    expect(formatCompactNumber(Number.POSITIVE_INFINITY)).toBe('—');
  });
});

describe('formatThreshold', () => {
  it('renders in the display unit system with a suffix', () => {
    expect(formatThreshold(10, 'Nm', 'metric')).toBe('10 Nm');
    // Imperial conversion goes through the shared table; assert shape,
    // not the constant, so a table tweak doesn't break this.
    const imp = formatThreshold(10, 'Nm', 'imperial');
    expect(imp).not.toContain('Nm');
    expect(imp).toMatch(/^[\d.,]+ \S+/);
  });
  it('omits the suffix for unitless attributes', () => {
    expect(formatThreshold(4.5, undefined, 'metric')).toBe('4.5');
  });
});

describe('summarizeFilter', () => {
  const opts = { unit: 'Nm', unitSystem: 'metric' as const };

  it('returns "any" for a valueless entry', () => {
    expect(summarizeFilter(f({}), opts)).toBe('any');
  });
  it('numeric threshold with operator symbol and unit', () => {
    expect(summarizeFilter(f({ operator: '>=', value: 10 }), opts)).toBe('≥ 10 Nm');
    expect(summarizeFilter(f({ operator: '<', value: 0.037 }), opts)).toBe('< 0.037 Nm');
    expect(summarizeFilter(f({ operator: '>=', value: 10, mode: 'exclude' }), opts)).toBe('not ≥ 10 Nm');
  });
  it('defaults the operator to ≥ when absent', () => {
    expect(summarizeFilter(f({ value: 5 }), opts)).toBe('≥ 5 Nm');
  });
  it('categorical scalar and lists, include and exclude', () => {
    const c = { unitSystem: 'metric' as const };
    expect(summarizeFilter(f({ attribute: 'manufacturer', value: 'ABB' }), c)).toBe('ABB');
    expect(summarizeFilter(f({ attribute: 'manufacturer', value: 'ABB', mode: 'exclude' }), c)).toBe('≠ ABB');
    expect(summarizeFilter(f({ attribute: 'manufacturer', value: ['ABB', 'Siemens'] }), c)).toBe('ABB, Siemens');
    expect(summarizeFilter(f({ attribute: 'manufacturer', value: ['a', 'b', 'c'] }), c)).toBe('3 values');
    expect(summarizeFilter(f({ attribute: 'manufacturer', value: ['a', 'b', 'c'], mode: 'exclude' }), c)).toBe('not 3 values');
  });
  it('numeric [lo, hi] tuple reads as a range', () => {
    expect(summarizeFilter(f({ value: [1, 5] }), opts)).toBe('1 Nm – 5 Nm');
  });
  it('booleans', () => {
    expect(summarizeFilter(f({ attribute: 'brake', value: true }), { unitSystem: 'metric' })).toBe('yes');
    expect(summarizeFilter(f({ attribute: 'brake', value: true, mode: 'exclude' }), { unitSystem: 'metric' })).toBe('no');
  });
});

describe('sniffAttributeUnit', () => {
  const rows = [
    { product_type: 'motor', rated_torque: null },
    { product_type: 'motor', rated_torque: { value: 3, unit: 'Nm' } },
  ] as unknown as Product[];
  it('returns the first unit found, skipping nulls', () => {
    expect(sniffAttributeUnit(rows, 'rated_torque')).toBe('Nm');
  });
  it('returns undefined when no row carries a unit', () => {
    expect(sniffAttributeUnit(rows, 'manufacturer')).toBeUndefined();
    expect(sniffAttributeUnit([], 'rated_torque')).toBeUndefined();
  });
});
