/**
 * Streamed product loading — cache-miss path.
 *
 * A 5,600-row type used to block the UI behind an exhaustive cursor
 * walk with no indicator (the "it just hangs" report, 2026-07-25).
 * The cache-miss path now streams: first page paints and clears
 * `loading`, later pages append, `loadProgress` tracks the walk, and a
 * superseded stream must neither write state nor poison the cache with
 * a partial listing.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { AppProvider, useApp } from './AppContext';
import type { Product } from '../types/models';

type OnPage = (batch: Product[], loaded: number) => void;
type StreamOpts = { onPage?: OnPage; shouldContinue?: () => boolean };

const streamProducts = vi.fn();

vi.mock('../api/client', () => ({
  apiClient: {
    streamProducts: (type: unknown, opts: StreamOpts) => streamProducts(type, opts),
    listProducts: vi.fn(() => Promise.reject(new Error('not in this test'))),
    getSummary: vi.fn(() => Promise.reject(new Error('not in this test'))),
    getCategories: vi.fn(() => Promise.reject(new Error('not in this test'))),
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

function product(product_type: string, product_id: string): Product {
  return { product_id, product_type, manufacturer: 'TestCo' } as Product;
}

const page1 = [product('gearhead', 'g1'), product('gearhead', 'g2')];
const page2 = [product('gearhead', 'g3')];

/** A stream the test drives page by page. */
function manualStream() {
  let opts!: StreamOpts;
  let finish!: (all: Product[]) => void;
  const done = new Promise<Product[]>(r => {
    finish = r;
  });
  streamProducts.mockImplementation((_type: unknown, o: StreamOpts) => {
    opts = o;
    return done;
  });
  return {
    emit: (batch: Product[], loaded: number) => opts.onPage?.(batch, loaded),
    finish,
    shouldContinue: () => opts.shouldContinue?.() ?? true,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  streamProducts.mockReset();
});

describe('loadProducts streaming (cache miss)', () => {
  it('paints the first page immediately, appends later pages, tracks progress', async () => {
    const stream = manualStream();
    const { result } = renderHook(() => useApp(), { wrapper });

    // Seed categories so the progress total is determinate.
    act(() => {
      result.current.setCategories([
        { type: 'gearhead', count: 3, display_name: 'Gearheads' },
      ]);
    });

    act(() => {
      void result.current.loadProducts('gearhead');
    });
    expect(result.current.loading).toBe(true);

    // First page lands: rows on screen, loading cleared, progress live.
    await act(async () => {
      stream.emit(page1, 2);
    });
    expect(result.current.loading).toBe(false);
    expect(result.current.products).toEqual(page1);
    expect(result.current.loadProgress).toEqual({ loaded: 2, total: 3 });

    // Second page appends.
    await act(async () => {
      stream.emit(page2, 3);
    });
    expect(result.current.products).toEqual([...page1, ...page2]);
    expect(result.current.loadProgress).toEqual({ loaded: 3, total: 3 });

    // Walk completes: progress clears.
    await act(async () => {
      stream.finish([...page1, ...page2]);
    });
    expect(result.current.loadProgress).toBeNull();
    expect(result.current.products).toEqual([...page1, ...page2]);
  });

  it('reports an indeterminate total when categories have not loaded', async () => {
    const stream = manualStream();
    const { result } = renderHook(() => useApp(), { wrapper });

    act(() => {
      void result.current.loadProducts('gearhead');
    });
    await act(async () => {
      stream.emit(page1, 2);
    });
    expect(result.current.loadProgress).toEqual({ loaded: 2, total: null });
    await act(async () => {
      stream.finish(page1);
    });
  });

  it('a superseded stream stops writing state and abandons its walk', async () => {
    const gearStream = {
      pages: [] as StreamOpts[],
    };
    let gearFinish!: (all: Product[]) => void;
    const gearDone = new Promise<Product[]>(r => {
      gearFinish = r;
    });
    const motorRows = [product('motor', 'm1')];
    streamProducts.mockImplementation((type: unknown, o: StreamOpts) => {
      if (type === 'gearhead') {
        gearStream.pages.push(o);
        return gearDone;
      }
      // motor: resolves immediately as a one-page stream
      o.onPage?.(motorRows, 1);
      return Promise.resolve(motorRows);
    });

    const { result } = renderHook(() => useApp(), { wrapper });

    act(() => {
      void result.current.loadProducts('gearhead');
    });
    // User switches type before gearhead's first page arrives.
    await act(async () => {
      await result.current.loadProducts('motor');
    });
    expect(result.current.products).toEqual(motorRows);

    // Gearhead's late pages must not touch state, and its walk reports
    // it should stop.
    const gearOpts = gearStream.pages[0];
    expect(gearOpts.shouldContinue?.()).toBe(false);
    await act(async () => {
      gearOpts.onPage?.(page1, 2);
    });
    expect(result.current.products).toEqual(motorRows);
    expect(result.current.loadProgress).toBeNull();

    // The partial gearhead listing must NOT have been cached: selecting
    // gearhead again triggers a fresh stream (2 calls for gearhead).
    await act(async () => {
      gearFinish(page1); // stale promise settles with partial data
    });
    act(() => {
      void result.current.loadProducts('gearhead');
    });
    const gearheadCalls = streamProducts.mock.calls.filter(c => c[0] === 'gearhead');
    expect(gearheadCalls).toHaveLength(2);
  });
});
