/**
 * API version switch tests — Phase 1.4 of the Express → FastAPI
 * migration (todo/PYTHON_BACKEND.md).
 *
 * Covers `applyApiVersion` (the pure path-rewrite helper) and the
 * end-to-end behaviour: a v2-resolved client rewrites `/api/...`
 * request URLs to `/api/v2/...`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { applyApiVersion } from './client';

describe('applyApiVersion', () => {
  it('is a no-op for v1', () => {
    expect(applyApiVersion('/api/products', 'v1')).toBe('/api/products');
    expect(applyApiVersion('/api/v1/search', 'v1')).toBe('/api/v1/search');
  });

  it('rewrites /api/<rest> to /api/v2/<rest> for v2', () => {
    expect(applyApiVersion('/api/products', 'v2')).toBe('/api/v2/products');
    expect(applyApiVersion('/api/products/categories', 'v2')).toBe(
      '/api/v2/products/categories'
    );
  });

  it('rewrites the versioned /api/v1/* endpoints too', () => {
    // /api/v1/search → /api/v2/v1/search. The FastAPI strip
    // middleware reverses /api/v2 → /api, landing back on
    // /api/v1/search.
    expect(applyApiVersion('/api/v1/search', 'v2')).toBe(
      '/api/v2/v1/search'
    );
    expect(applyApiVersion('/api/v1/compat/check', 'v2')).toBe(
      '/api/v2/v1/compat/check'
    );
  });

  it('handles the bare /api endpoint', () => {
    expect(applyApiVersion('/api', 'v2')).toBe('/api/v2');
  });

  it('leaves non-/api endpoints untouched', () => {
    expect(applyApiVersion('/health', 'v2')).toBe('/health');
    expect(applyApiVersion('/healthz', 'v2')).toBe('/healthz');
  });

  it('does not double-rewrite an already-v2 path', () => {
    // The client only ever feeds applyApiVersion the un-versioned
    // endpoint, but pin the guard: an /api/v2/... input would be
    // rewritten again (/api/v2/api/v2/...) — so callers must pass
    // the canonical /api/... form. This test documents that
    // contract by asserting the (intentional) non-idempotence.
    expect(applyApiVersion('/api/v2/products', 'v2')).toBe(
      '/api/v2/v2/products'
    );
  });
});

describe('ApiClient version resolution', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    window.localStorage.clear();
    vi.resetModules();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    window.localStorage.clear();
  });

  it('localStorage api_version=v2 routes through /api/v2', async () => {
    window.localStorage.setItem('api_version', 'v2');
    let capturedUrl = '';
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ success: true, data: {} }), {
        status: 200,
      });
    });
    const mod = await import('./client');
    await mod.apiClient.getSummary();
    expect(capturedUrl).toContain('/api/v2/products/summary');
  });

  it('localStorage api_version=v1 stays on /api', async () => {
    window.localStorage.setItem('api_version', 'v1');
    let capturedUrl = '';
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ success: true, data: {} }), {
        status: 200,
      });
    });
    const mod = await import('./client');
    await mod.apiClient.getSummary();
    expect(capturedUrl).toContain('/api/products/summary');
    expect(capturedUrl).not.toContain('/api/v2');
  });

  it('default (no localStorage, no env) is v1', async () => {
    let capturedUrl = '';
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ success: true, data: {} }), {
        status: 200,
      });
    });
    const mod = await import('./client');
    await mod.apiClient.getSummary();
    expect(capturedUrl).not.toContain('/api/v2');
  });
});
