/**
 * Regression — 2026-09-13. User report: "when a filter eliminates all
 * results everything is eliminated even the headers so you can't
 * readjust your filters to show them all again."
 *
 * The listing body decision is the pure `resolveListBody`; the render
 * maps 'pick-type' | 'no-products' to the header-less empty state and
 * everything else to the grid (headers included). 'no-match' therefore
 * MUST NOT collapse into 'no-products'.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveListBody } from './ProductList';

describe('resolveListBody', () => {
  it('asks for a product type before anything else', () => {
    expect(
      resolveListBody({ productType: null, loading: false, totalProducts: 0, displayedProducts: 0 }),
    ).toBe('pick-type');
    expect(
      resolveListBody({ productType: null, loading: true, totalProducts: 5, displayedProducts: 0 }),
    ).toBe('pick-type');
  });

  it('shows the loading state only while nothing has been fetched', () => {
    expect(
      resolveListBody({ productType: 'motor', loading: true, totalProducts: 0, displayedProducts: 0 }),
    ).toBe('loading');
    // Streaming: first page on screen, rest still loading → grid, not spinner.
    expect(
      resolveListBody({ productType: 'motor', loading: true, totalProducts: 40, displayedProducts: 40 }),
    ).toBe('rows');
  });

  it('reports an empty database as no-products', () => {
    expect(
      resolveListBody({ productType: 'motor', loading: false, totalProducts: 0, displayedProducts: 0 }),
    ).toBe('no-products');
  });

  it('keeps the grid (headers) when filters eliminate every row', () => {
    expect(
      resolveListBody({ productType: 'motor', loading: false, totalProducts: 120, displayedProducts: 0 }),
    ).toBe('no-match');
  });

  it('does not flash no-match while a stream is still loading', () => {
    expect(
      resolveListBody({ productType: 'motor', loading: true, totalProducts: 120, displayedProducts: 0 }),
    ).toBe('rows');
  });

  it('renders rows when anything survives the filters', () => {
    expect(
      resolveListBody({ productType: 'drive', loading: false, totalProducts: 120, displayedProducts: 1 }),
    ).toBe('rows');
  });
});

describe('ProductList render wiring (source-text)', () => {
  const __dirname = dirname(fileURLToPath(import.meta.url));
  const source = readFileSync(join(__dirname, 'ProductList.tsx'), 'utf-8');

  it("only 'pick-type' and 'no-products' take the header-less empty-state branch", () => {
    // The branch that swaps the grid out must be gated on exactly these
    // two states; adding 'no-match' back here reintroduces the bug.
    expect(source).toMatch(
      /listBody === 'pick-type' \|\| listBody === 'no-products' \? \(/,
    );
    expect(source).not.toMatch(/listBody === 'no-match' \|\| listBody === 'no-products' \?/);
  });

  it("renders the 'no-match' message inside the grid scroll container", () => {
    const gridStart = source.indexOf('<div className="product-grid-scroll">');
    const noMatch = source.indexOf("listBody === 'no-match' &&");
    const sentinel = source.indexOf('className="infinite-scroll-sentinel"');
    expect(gridStart).toBeGreaterThan(-1);
    expect(noMatch).toBeGreaterThan(gridStart);
    expect(noMatch).toBeLessThan(sentinel);
  });
});
