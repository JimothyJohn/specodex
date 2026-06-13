/**
 * listProducts cursor pagination tests.
 *
 * The backend caps each /api/products response at 2000 rows and returns
 * an opaque `cursor` when more pages exist. listProducts must follow the
 * cursor until `cursor: null` so client-side filters and the table see
 * the same row count as the categories endpoint (the "dropdown says
 * 10,471 but the table holds 2,000" mismatch).
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { apiClient } from './client';

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

const product = (id: number) => ({
  product_id: `p${id}`,
  product_type: 'motor',
  manufacturer: 'X',
});

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe('listProducts pagination', () => {
  it('follows the cursor until null and concatenates all pages', async () => {
    const pages = [
      { success: true, data: [product(1), product(2)], cursor: 'CURSOR_1' },
      { success: true, data: [product(3)], cursor: 'CURSOR_2' },
      { success: true, data: [product(4)], cursor: null },
    ];
    let call = 0;
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url);
      // Page N+1 must carry page N's cursor back to the server.
      if (call === 1) expect(u).toContain('cursor=CURSOR_1');
      if (call === 2) expect(u).toContain('cursor=CURSOR_2');
      return jsonResponse(pages[call++]);
    }) as typeof fetch;

    const products = await apiClient.listProducts('motor');
    expect(products.map((p) => p.product_id)).toEqual(['p1', 'p2', 'p3', 'p4']);
    expect(call).toBe(3);
  });

  it('stops after one request when the backend sends no cursor field (v2 degradation)', async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ success: true, data: [product(1)] })
    ) as typeof fetch;

    const products = await apiClient.listProducts('motor');
    expect(products).toHaveLength(1);
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it('an explicit limit makes a single bounded request with no cursor param', async () => {
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url);
      expect(u).toContain('limit=100');
      expect(u).not.toContain('cursor');
      // Even if the server offers a cursor, a limited call must not follow it.
      return jsonResponse({ success: true, data: [product(1)], cursor: 'MORE' });
    }) as typeof fetch;

    const products = await apiClient.listProducts('motor', 100);
    expect(products).toHaveLength(1);
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it('bounds the loop when the backend returns cursors forever', async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ success: true, data: [product(1)], cursor: 'AGAIN' })
    ) as typeof fetch;

    const products = await apiClient.listProducts('motor');
    // MAX_LIST_PAGES caps the loop; one row per page.
    expect(products.length).toBeLessThanOrEqual(25);
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBeLessThanOrEqual(25);
  });

  it('a page without data rejects instead of silently returning a partial listing', async () => {
    const pages = [
      { success: true, data: [product(1)], cursor: 'CURSOR_1' },
      { success: true }, // data missing on page 2
    ];
    let call = 0;
    globalThis.fetch = vi.fn(async () => jsonResponse(pages[call++])) as typeof fetch;

    await expect(apiClient.listProducts('motor')).rejects.toThrow(/No products data/);
  });
});
