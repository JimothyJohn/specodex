/**
 * BalanceToggle — persistence round-trip for the Commercial / Balanced /
 * Technical control (todo/COMMERCIAL.md Phase 3). Mirrors the
 * DensityToggle test pattern: AppProvider + localStorage assertions.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AppProvider } from '../context/AppContext';
import BalanceToggle from './BalanceToggle';

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

const renderToggle = () =>
  render(
    <AppProvider>
      <BalanceToggle />
    </AppProvider>,
  );

const segment = (label: string) =>
  screen.getAllByRole('radio').find((b) => b.textContent === label)!;

describe('BalanceToggle', () => {
  it('defaults to balanced when localStorage is empty', () => {
    renderToggle();
    expect(segment('BAL').getAttribute('aria-checked')).toBe('true');
    expect(segment('COMM').getAttribute('aria-checked')).toBe('false');
    expect(segment('TECH').getAttribute('aria-checked')).toBe('false');
  });

  it('selecting Commercial persists to specodex.columnBalance', () => {
    renderToggle();
    fireEvent.click(segment('COMM'));
    expect(segment('COMM').getAttribute('aria-checked')).toBe('true');
    expect(window.localStorage.getItem('specodex.columnBalance')).toBe('commercial');
  });

  it('round-trips: a persisted technical preference hydrates on mount', () => {
    window.localStorage.setItem('specodex.columnBalance', 'technical');
    renderToggle();
    expect(segment('TECH').getAttribute('aria-checked')).toBe('true');
  });

  it('a malformed stored value falls back to balanced', () => {
    window.localStorage.setItem('specodex.columnBalance', 'spreadsheet');
    renderToggle();
    expect(segment('BAL').getAttribute('aria-checked')).toBe('true');
  });

  it('cycling through all three states persists each', () => {
    renderToggle();
    for (const [label, stored] of [
      ['COMM', 'commercial'],
      ['TECH', 'technical'],
      ['BAL', 'balanced'],
    ] as const) {
      fireEvent.click(segment(label));
      expect(window.localStorage.getItem('specodex.columnBalance')).toBe(stored);
    }
  });
});
