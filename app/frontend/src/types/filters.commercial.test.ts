/**
 * Commercial filter wiring (todo/COMMERCIAL.md Phase 3): the stocked-only
 * quick filter and the per-filter include-estimates price toggle.
 */

import { describe, it, expect } from 'vitest';
import {
  applyFilters,
  buildStockedOnlyFilter,
  isStockedOnlyFilter,
  FilterCriterion,
} from './filters';
import type { Product } from './models';

const product = (over: Record<string, unknown>): Product =>
  ({
    product_id: String(Math.random()),
    product_type: 'motor',
    product_name: 'M',
    manufacturer: 'ACME',
    ...over,
  }) as unknown as Product;

const listed = product({ product_id: 'listed', msrp: { value: 900, unit: 'USD' } });
const estimated = product({
  product_id: 'estimated',
  price_estimate: {
    value: { value: 500, unit: 'USD' },
    confidence: 'inferred',
    sources: [
      {
        url: null,
        kind: 'db_inference',
        retrieved_at: '2026-07-01T00:00:00Z',
        comparable_ids: ['a', 'b', 'c'],
        method_version: 'price-comps-v1',
      },
    ],
  },
});
const unpriced = product({ product_id: 'unpriced' });

describe('stocked-only quick filter', () => {
  const stocked = buildStockedOnlyFilter();

  it('keeps in_stock and limited, drops everything else (and unknown)', () => {
    const rows = [
      product({ product_id: 'a', availability: 'in_stock' }),
      product({ product_id: 'b', availability: 'limited' }),
      product({ product_id: 'c', availability: 'back_order' }),
      product({ product_id: 'd', availability: 'out_of_stock' }),
      product({ product_id: 'e', availability: 'discontinued' }),
      product({ product_id: 'f', availability: 'pre_order' }),
      product({ product_id: 'g' }),
    ];
    const out = applyFilters(rows, [stocked]);
    expect(out.map((p) => p.product_id)).toEqual(['a', 'b']);
  });

  it('is recognized by isStockedOnlyFilter, and ordinary filters are not', () => {
    expect(isStockedOnlyFilter(stocked)).toBe(true);
    const other: FilterCriterion = {
      attribute: 'availability',
      mode: 'include',
      value: 'in_stock',
      displayName: 'Lead Time',
    };
    expect(isStockedOnlyFilter(other)).toBe(false);
  });
});

describe('price filter include-estimates toggle', () => {
  const priceFloor = (includeEstimates: boolean): FilterCriterion => ({
    attribute: 'msrp',
    mode: 'include',
    operator: '>=',
    value: 400,
    displayName: 'Price',
    includeEstimates,
  });

  it('default (off) matches listed prices only', () => {
    const out = applyFilters([listed, estimated, unpriced], [priceFloor(false)]);
    expect(out.map((p) => p.product_id)).toEqual(['listed']);
  });

  it('on: rows without msrp fall back to price_estimate.value', () => {
    const out = applyFilters([listed, estimated, unpriced], [priceFloor(true)]);
    expect(out.map((p) => p.product_id)).toEqual(['listed', 'estimated']);
  });

  it('on: the estimate must still satisfy the comparison', () => {
    const tight: FilterCriterion = { ...priceFloor(true), value: 600 };
    const out = applyFilters([listed, estimated, unpriced], [tight]);
    expect(out.map((p) => p.product_id)).toEqual(['listed']);
  });

  it('listed msrp always wins over the estimate when both exist', () => {
    const both = product({
      product_id: 'both',
      msrp: { value: 900, unit: 'USD' },
      price_estimate: {
        value: { value: 100, unit: 'USD' },
        confidence: 'inferred',
        sources: [
          {
            url: null,
            kind: 'db_inference',
            retrieved_at: '2026-07-01T00:00:00Z',
            comparable_ids: ['a'],
            method_version: 'price-comps-v1',
          },
        ],
      },
    });
    // Floor at 400: the listed 900 passes even though the estimate (100)
    // wouldn't — the listed figure is the one the filter must see.
    const out = applyFilters([both], [priceFloor(true)]);
    expect(out.map((p) => p.product_id)).toEqual(['both']);
  });

  it('nested msrp.value paths resolve through the estimate too', () => {
    const nested: FilterCriterion = {
      attribute: 'msrp.value',
      mode: 'include',
      operator: '>=',
      value: 400,
      displayName: 'Price',
      includeEstimates: true,
    };
    const out = applyFilters([estimated], [nested]);
    expect(out.map((p) => p.product_id)).toEqual(['estimated']);
  });
});
