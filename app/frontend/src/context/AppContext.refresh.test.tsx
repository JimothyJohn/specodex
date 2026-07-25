/**
 * Cache-hit background refresh economy (2026-07-25).
 *
 * Every cache hit used to silently re-download the ENTIRE type
 * (9,392 gearhead rows after the multi-vendor ingests) and then
 * double-JSON.stringify it on the main thread to decide "unchanged".
 * The categories endpoint's per-type counts are the cheap freshness
 * signal: equal count → skip the refetch entirely; differing count →
 * refetch and replace. Same-count edits stay stale until forceRefresh
 * — the documented trade.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { AppProvider, useApp } from './AppContext';
import type { Product } from '../types/models';

const listProducts = vi.fn();
const getCategories = vi.fn();
const streamProducts = vi.fn();

vi.mock('../api/client', () => ({
  apiClient: {
    listProducts: (...a: unknown[]) => listProducts(...a),
    getCategories: (...a: unknown[]) => getCategories(...a),
    streamProducts: (type: unknown, opts: { onPage?: (b: Product[], n: number) => void }) =>
      streamProducts(type, opts),
    getSummary: vi.fn(() => Promise.reject(new Error('not in this test'))),
    createProduct: vi.fn(() => Promise.reject(new Error('not in this test'))),
    updateProduct: vi.fn(() => Promise.reject(new Error('not in this test'))),
    deleteProduct: vi.fn(() => Promise.reject(new Error('not in this test'))),
    createDatasheet: vi.fn(() => Promise.reject(new Error('not in this test'))),
    updateDatasheet: vi.fn(() => Promise.reject(new Error('not in this test'))),
  },
}));

const wrapper = ({ children }: { children: ReactNode }) => (
  <AppProvider>{children}</AppProvider>
);

const rows = [
  { product_id: 'g1', product_type: 'gearhead', manufacturer: 'X' },
  { product_id: 'g2', product_type: 'gearhead', manufacturer: 'X' },
] as Product[];

async function primeCache(result: { current: ReturnType<typeof useApp> }) {
  streamProducts.mockImplementationOnce(async (_t, opts) => {
    opts.onPage?.(rows, rows.length);
    return rows;
  });
  await act(async () => {
    await result.current.loadProducts('gearhead');
  });
  // Leave the type so the next loadProducts('gearhead') is a cache hit.
  await act(async () => {
    await result.current.loadProducts(null);
  });
}

beforeEach(() => {
  window.localStorage.clear();
  listProducts.mockReset();
  getCategories.mockReset();
  streamProducts.mockReset();
});

describe('cache-hit background refresh', () => {
  it('equal categories count skips the refetch entirely', async () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    await primeCache(result);

    getCategories.mockResolvedValue([
      { type: 'gearhead', count: rows.length, display_name: 'Gearheads' },
    ]);
    await act(async () => {
      await result.current.loadProducts('gearhead'); // cache hit
    });
    expect(getCategories).toHaveBeenCalled();
    expect(listProducts).not.toHaveBeenCalled();
    expect(result.current.products).toEqual(rows);
  });

  it('differing count refetches and replaces cache', async () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    await primeCache(result);

    const grown = [...rows, { product_id: 'g3', product_type: 'gearhead', manufacturer: 'X' } as Product];
    getCategories.mockResolvedValue([
      { type: 'gearhead', count: 3, display_name: 'Gearheads' },
    ]);
    listProducts.mockResolvedValue(grown);
    await act(async () => {
      await result.current.loadProducts('gearhead');
    });
    expect(listProducts).toHaveBeenCalledWith('gearhead');
    expect(result.current.products).toEqual(grown);
  });

  it('a failed freshness check keeps serving the cache silently', async () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    await primeCache(result);

    getCategories.mockRejectedValue(new Error('network down'));
    await act(async () => {
      await result.current.loadProducts('gearhead');
    });
    expect(result.current.products).toEqual(rows);
    expect(result.current.error).toBeNull();
    expect(listProducts).not.toHaveBeenCalled();
  });
});
