/**
 * Regression: rapid product-type switches raced on the cache-miss path.
 *
 * The background-refresh path guards writes with currentProductTypeRef,
 * but the cache-miss `await apiClient.listProducts(type)` path had no
 * stale-response guard: switching motor → drive while motor's (slower)
 * request was in flight let the late motor response overwrite `products`
 * and `currentProductType` after drive had already rendered — motor rows
 * under drive columns. The shared `loading` boolean also flipped false
 * when the FIRST of two concurrent loads settled.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { AppProvider, useApp } from './AppContext';
import type { Product } from '../types/models';

const listProducts = vi.fn();

vi.mock('../api/client', () => ({
  apiClient: {
    listProducts: (...args: unknown[]) => listProducts(...args),
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

const motorA = product('motor', 'motor-a');
const driveA = product('drive', 'drive-a');

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>(r => {
    resolve = r;
  });
  return { promise, resolve };
}

beforeEach(() => {
  window.localStorage.clear();
  listProducts.mockReset();
});

describe('loadProducts type-switch race (cache miss)', () => {
  it('discards the stale response when a newer load resolves first', async () => {
    const motorLoad = deferred<Product[]>();
    const driveLoad = deferred<Product[]>();
    listProducts.mockImplementation((type: string) =>
      type === 'motor' ? motorLoad.promise : driveLoad.promise
    );

    const { result } = renderHook(() => useApp(), { wrapper });

    act(() => {
      void result.current.loadProducts('motor');
    });
    act(() => {
      void result.current.loadProducts('drive');
    });

    // The newer (drive) request resolves first…
    await act(async () => {
      driveLoad.resolve([driveA]);
    });
    // …then the stale motor response lands late.
    await act(async () => {
      motorLoad.resolve([motorA]);
    });

    // currentProductType isn't exposed on the context value; the
    // products array is the observable contract.
    expect(result.current.products).toEqual([driveA]);
  });

  it('keeps the loading flag until the LATEST load settles', async () => {
    const motorLoad = deferred<Product[]>();
    const driveLoad = deferred<Product[]>();
    listProducts.mockImplementation((type: string) =>
      type === 'motor' ? motorLoad.promise : driveLoad.promise
    );

    const { result } = renderHook(() => useApp(), { wrapper });

    act(() => {
      void result.current.loadProducts('motor');
    });
    act(() => {
      void result.current.loadProducts('drive');
    });

    // Stale motor load settles first — must NOT clear the flag while
    // drive is still in flight.
    await act(async () => {
      motorLoad.resolve([motorA]);
    });
    expect(result.current.loading).toBe(true);

    await act(async () => {
      driveLoad.resolve([driveA]);
    });
    expect(result.current.loading).toBe(false);
    expect(result.current.products).toEqual([driveA]);
  });

  it('a stale failure does not surface an error over the newer success', async () => {
    const motorLoad = deferred<Product[]>();
    listProducts.mockImplementation((type: string) => {
      if (type === 'motor') return motorLoad.promise;
      return Promise.resolve([driveA]);
    });

    const { result } = renderHook(() => useApp(), { wrapper });

    act(() => {
      void result.current.loadProducts('motor');
    });
    await act(async () => {
      await result.current.loadProducts('drive');
    });

    // Reject the stale motor request after drive already succeeded.
    await act(async () => {
      motorLoad.resolve(Promise.reject(new Error('network down')) as never);
      await Promise.resolve();
    });

    expect(result.current.error).toBeNull();
    expect(result.current.products).toEqual([driveA]);
  });
});
