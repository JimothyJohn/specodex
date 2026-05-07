/**
 * AppContext setter contract — Phase 2 of FRONTEND_TESTING.md.
 *
 * Treats the context as a black box: render <AppProvider>, exercise each
 * setter through useApp(), assert state + localStorage round-trip. Does not
 * cover data-fetching (those go through apiClient and are owned by
 * api/client.test.ts). The apiClient is mocked at module level so an
 * accidental call surfaces as a loud failure instead of a JSDOM network
 * attempt.
 *
 * Spillover-bestiary mapping (see FRONTEND_TESTING.md):
 *   L6: stale specodex.build shape doesn't crash init
 *   L7: unitSystem persists + propagates
 *   L8: rowDensity persists + propagates
 *   L9: addToBuild replaces a slot rather than growing an array
 *   L10: compatibleOnly persists across re-mount
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { AppProvider, useApp } from './AppContext';
import type { Product } from '../types/models';

vi.mock('../api/client', () => ({
  apiClient: {
    listProducts: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    getSummary: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    getCategories: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    createProduct: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    updateProduct: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    deleteProduct: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    createDatasheet: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    updateDatasheet: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
  },
}));

const wrapper = ({ children }: { children: ReactNode }) => <AppProvider>{children}</AppProvider>;

function product(product_type: string, product_id: string): Product {
  return {
    product_id,
    product_type,
    manufacturer: 'TestCo',
  } as Product;
}

const motorA = product('motor', 'motor-a');
const motorB = product('motor', 'motor-b');
const driveA = product('drive', 'drive-a');

beforeEach(() => {
  window.localStorage.clear();
});

describe('AppContext defaults', () => {
  it('hydrates with documented defaults when localStorage is empty', () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    expect(result.current.unitSystem).toBe('metric');
    expect(result.current.rowDensity).toBe('compact');
    expect(result.current.compatibleOnly).toBe(true);
    expect(result.current.build).toEqual({});
  });
});

describe('setUnitSystem (L7)', () => {
  it('updates context and persists raw string to localStorage', () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    act(() => result.current.setUnitSystem('imperial'));
    expect(result.current.unitSystem).toBe('imperial');
    expect(window.localStorage.getItem('unitSystem')).toBe('imperial');
  });

  it('round-trips imperial across a re-mount', () => {
    window.localStorage.setItem('unitSystem', 'imperial');
    const { result } = renderHook(() => useApp(), { wrapper });
    expect(result.current.unitSystem).toBe('imperial');
  });

  it('falls back to metric when stored value is off-schema', () => {
    window.localStorage.setItem('unitSystem', 'kelvin');
    const { result } = renderHook(() => useApp(), { wrapper });
    expect(result.current.unitSystem).toBe('metric');
  });
});

describe('setRowDensity (L8)', () => {
  it('updates context and persists raw string to localStorage', () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    act(() => result.current.setRowDensity('comfy'));
    expect(result.current.rowDensity).toBe('comfy');
    expect(window.localStorage.getItem('productListRowDensity')).toBe('comfy');
  });

  it('round-trips comfy across a re-mount', () => {
    window.localStorage.setItem('productListRowDensity', 'comfy');
    const { result } = renderHook(() => useApp(), { wrapper });
    expect(result.current.rowDensity).toBe('comfy');
  });
});

describe('setCompatibleOnly (L10)', () => {
  it('updates context and persists JSON-encoded boolean', () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    act(() => result.current.setCompatibleOnly(false));
    expect(result.current.compatibleOnly).toBe(false);
    expect(window.localStorage.getItem('specodex.compatibleOnly')).toBe('false');
  });

  it('round-trips false across a re-mount', () => {
    window.localStorage.setItem('specodex.compatibleOnly', 'false');
    const { result } = renderHook(() => useApp(), { wrapper });
    expect(result.current.compatibleOnly).toBe(false);
  });
});

describe('addToBuild (L9)', () => {
  it('populates the motor slot and persists to localStorage', () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    act(() => result.current.addToBuild(motorA));
    expect(result.current.build).toEqual({ motor: motorA });
    const persisted = JSON.parse(window.localStorage.getItem('specodex.build') ?? '{}');
    expect(persisted).toEqual({ motor: motorA });
  });

  it('replaces an existing motor in the same slot rather than growing', () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    act(() => result.current.addToBuild(motorA));
    act(() => result.current.addToBuild(motorB));
    expect(result.current.build).toEqual({ motor: motorB });
    expect(Array.isArray(result.current.build)).toBe(false);
  });

  it('places drive and motor in their own slots without overlap', () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    act(() => result.current.addToBuild(motorA));
    act(() => result.current.addToBuild(driveA));
    expect(result.current.build).toEqual({ motor: motorA, drive: driveA });
  });

  it('is a no-op when product_type is not a BUILD_SLOT', () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    const robotArm = product('robot_arm', 'r-1');
    act(() => result.current.addToBuild(robotArm));
    expect(result.current.build).toEqual({});
  });
});

describe('removeFromBuild', () => {
  it('deletes the key entirely (not set to undefined)', () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    act(() => result.current.addToBuild(motorA));
    act(() => result.current.addToBuild(driveA));
    act(() => result.current.removeFromBuild('motor'));
    expect(result.current.build).toEqual({ drive: driveA });
    expect('motor' in result.current.build).toBe(false);
  });
});

describe('clearBuild', () => {
  it('empties every slot', () => {
    const { result } = renderHook(() => useApp(), { wrapper });
    act(() => result.current.addToBuild(motorA));
    act(() => result.current.addToBuild(driveA));
    act(() => result.current.clearBuild());
    expect(result.current.build).toEqual({});
  });

  it('persists the empty state so a remount sees {}', () => {
    const { result, unmount } = renderHook(() => useApp(), { wrapper });
    act(() => result.current.addToBuild(motorA));
    act(() => result.current.clearBuild());
    unmount();
    const fresh = renderHook(() => useApp(), { wrapper });
    expect(fresh.result.current.build).toEqual({});
  });
});

describe('build hydration (L6)', () => {
  it('falls back to {} when stored shape is invalid (slot value not an object)', () => {
    window.localStorage.setItem(
      'specodex.build',
      JSON.stringify({ motor: 'string-not-product' }),
    );
    const { result } = renderHook(() => useApp(), { wrapper });
    expect(result.current.build).toEqual({});
  });

  it('falls back to {} when stored value is an array', () => {
    window.localStorage.setItem('specodex.build', JSON.stringify([motorA, driveA]));
    const { result } = renderHook(() => useApp(), { wrapper });
    expect(result.current.build).toEqual({});
  });

  it('falls back to {} when stored value uses an unknown slot name', () => {
    window.localStorage.setItem(
      'specodex.build',
      JSON.stringify({ blender: product('blender', 'b-1') }),
    );
    const { result } = renderHook(() => useApp(), { wrapper });
    expect(result.current.build).toEqual({});
  });

  it('round-trips a valid prefilled build', () => {
    window.localStorage.setItem('specodex.build', JSON.stringify({ motor: motorA }));
    const { result } = renderHook(() => useApp(), { wrapper });
    expect(result.current.build).toEqual({ motor: motorA });
  });
});
