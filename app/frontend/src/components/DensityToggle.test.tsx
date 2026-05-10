/**
 * DensityToggle — Phase 4 of FRONTEND_TESTING.md.
 *
 * The toggle's icon depicts the CURRENT state (3 thin lines = cozy,
 * 5 thin lines = compact); `aria-pressed` reflects "currently compact".
 * Locks down L8 (rowDensity persists + propagates) at the component
 * layer.
 *
 * May-2026 rename: mode set flipped from compact/comfy → cozy/compact,
 * with the new compact significantly denser than the old one. The .v2
 * localStorage key bump prevents stored old-mode strings from leaking
 * the wrong intent into the new toggle.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AppProvider } from '../context/AppContext';
import DensityToggle from './DensityToggle';

vi.mock('../api/client', () => ({
  apiClient: {
    listProducts: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    getSummary: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    getCategories: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
  },
}));

beforeEach(() => {
  window.localStorage.clear();
});

function renderToggle() {
  return render(
    <AppProvider>
      <DensityToggle />
    </AppProvider>,
  );
}

describe('DensityToggle', () => {
  it('defaults to cozy when localStorage is empty', () => {
    renderToggle();
    const btn = screen.getByRole('button');
    expect(btn.getAttribute('aria-pressed')).toBe('false');
    expect(btn.getAttribute('aria-label')).toMatch(/cozy/i);
  });

  it('flips to compact on click and persists to the v2 key', () => {
    renderToggle();
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(btn.getAttribute('aria-pressed')).toBe('true');
    expect(btn.getAttribute('aria-label')).toMatch(/compact/i);
    expect(window.localStorage.getItem('productListRowDensity.v2')).toBe('compact');
  });

  it('flips back to cozy on second click', () => {
    renderToggle();
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(btn.getAttribute('aria-pressed')).toBe('false');
    expect(window.localStorage.getItem('productListRowDensity.v2')).toBe('cozy');
  });

  it('hydrates from compact in the v2 key', () => {
    window.localStorage.setItem('productListRowDensity.v2', 'compact');
    renderToggle();
    const btn = screen.getByRole('button');
    expect(btn.getAttribute('aria-pressed')).toBe('true');
  });

  it('ignores the pre-rename v1 key — stored "comfy" defaults to cozy', () => {
    // The .v2 key bump is the migration. Reading the literal 'comfy'
    // string from the v1 key would either fail validation (it isn't a
    // RowDensity anymore) or — if we silently mapped it — push users
    // into a layout they never asked for. Default-to-cozy wins.
    window.localStorage.setItem('productListRowDensity', 'comfy');
    renderToggle();
    const btn = screen.getByRole('button');
    expect(btn.getAttribute('aria-pressed')).toBe('false');
  });

  it('renders three-line icon when cozy, five-line icon when compact', () => {
    const { container } = renderToggle();
    expect(container.querySelectorAll('svg rect')).toHaveLength(3);
    fireEvent.click(screen.getByRole('button'));
    expect(container.querySelectorAll('svg rect')).toHaveLength(5);
  });
});
