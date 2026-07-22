/**
 * Balance-aware column ordering (todo/COMMERCIAL.md Phase 3).
 *
 * Extends the PR #285 pinning mechanism: `orderColumnAttributes` takes a
 * ColumnBalance and reorders the commercial band accordingly —
 *   commercial: band first (price, lead time, warranty), then manufacturer,
 *               then the technical order;
 *   balanced:   the existing default (manufacturer, msrp, availability, …);
 *   technical:  technical columns first, commercial band trailing last —
 *               after even the unlisted alphabetical spill.
 */

import { describe, it, expect } from 'vitest';
import {
  orderColumnAttributes,
  COMMERCIAL_BAND_KEYS,
  isColumnBalance,
} from './columnOrder';
import type { AttributeMetadata } from './filters';

const attr = (key: string, displayName = key): AttributeMetadata => ({
  key,
  displayName,
  type: 'object',
  applicableTypes: ['motor'],
});

// Deliberately shuffled input — ordering must not depend on input order.
const motorAttrs: AttributeMetadata[] = [
  attr('rated_torque', 'Rated Torque'),
  attr('availability', 'Lead Time'),
  attr('zeta_spec', 'Zeta Spec'), // unlisted → alphabetical spill
  attr('manufacturer', 'Manufacturer'),
  attr('warranty', 'Warranty'),
  attr('msrp', 'Price'),
  attr('rated_power', 'Rated Power'),
  attr('alpha_spec', 'Alpha Spec'), // unlisted → alphabetical spill
];

const keys = (attrs: AttributeMetadata[]) => attrs.map((a) => a.key);

describe('isColumnBalance', () => {
  it('accepts the three modes and rejects garbage', () => {
    expect(isColumnBalance('commercial')).toBe(true);
    expect(isColumnBalance('balanced')).toBe(true);
    expect(isColumnBalance('technical')).toBe(true);
    expect(isColumnBalance('comfy')).toBe(false);
    expect(isColumnBalance('')).toBe(false);
  });
});

describe('orderColumnAttributes with balance', () => {
  it('balanced keeps the existing default order (and is the default arg)', () => {
    const explicit = keys(orderColumnAttributes(motorAttrs, 'motor', 'balanced'));
    const implicit = keys(orderColumnAttributes(motorAttrs, 'motor'));
    expect(explicit).toEqual(implicit);
    // Authored motor order leads: manufacturer, msrp, availability, …
    expect(explicit.slice(0, 3)).toEqual(['manufacturer', 'msrp', 'availability']);
  });

  it('commercial pins the commercial band first, before manufacturer', () => {
    const ordered = keys(orderColumnAttributes(motorAttrs, 'motor', 'commercial'));
    const bandPresent = COMMERCIAL_BAND_KEYS.filter((k) => ordered.includes(k));
    expect(ordered.slice(0, bandPresent.length)).toEqual(bandPresent);
    expect(ordered[bandPresent.length]).toBe('manufacturer');
  });

  it('technical pushes the commercial band last — after the alphabetical spill', () => {
    const ordered = keys(orderColumnAttributes(motorAttrs, 'motor', 'technical'));
    const bandPresent = COMMERCIAL_BAND_KEYS.filter((k) => ordered.includes(k));
    expect(ordered.slice(-bandPresent.length)).toEqual(bandPresent);
    // Technical columns lead with manufacturer, then the authored specs.
    expect(ordered[0]).toBe('manufacturer');
    // The unlisted spill (alpha/zeta) still sits before the trailing band.
    expect(ordered.indexOf('zeta_spec')).toBeLessThan(ordered.indexOf('msrp'));
  });

  it('never drops or duplicates attributes in any mode', () => {
    for (const balance of ['commercial', 'balanced', 'technical'] as const) {
      const ordered = keys(orderColumnAttributes(motorAttrs, 'motor', balance));
      expect([...ordered].sort()).toEqual(keys(motorAttrs).sort());
    }
  });

  it('applies the band pinning to types without an explicit COLUMN_ORDER entry', () => {
    const ordered = keys(orderColumnAttributes(motorAttrs, 'linear_actuator', 'commercial'));
    expect(ordered[0]).toBe('msrp');
    expect(ordered).toContain('rated_torque');
  });
});
