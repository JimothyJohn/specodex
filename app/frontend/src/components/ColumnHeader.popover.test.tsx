/**
 * ColumnHeader popover-per-column contract (UI_CLEANUP Phase 2, S1,
 * 2026-09-13).
 *
 * At rest a numeric column shows label + sparkline + one trigger that
 * summarises the filter; the histogram / slider / value / operator /
 * unit controls only exist while the popover is open. Pins:
 *   - rest: no slider, trigger reads "any"
 *   - open: slider present; Escape closes it
 *   - a valued filter renders the unit-aware summary on the trigger
 *   - "clear" in the popover hands null to onFilterChange
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ColumnHeader from './ColumnHeader';
import { AppProvider } from '../context/AppContext';
import type { AttributeMetadata, FilterCriterion } from '../types/filters';
import type { Product } from '../types/models';

vi.mock('../api/client', () => ({
  apiClient: new Proxy(
    {},
    { get: () => vi.fn(() => Promise.reject(new Error('not in this test'))) },
  ),
}));

const noop = () => {};

const torqueAttr: AttributeMetadata = {
  key: 'rated_torque',
  displayName: 'Rated Torque',
  type: 'object',
  applicableTypes: ['motor'],
  nested: true,
  unit: 'Nm',
};

const rows: Product[] = Array.from({ length: 12 }, (_, i) =>
  ({
    product_type: 'motor',
    manufacturer: 'X',
    rated_torque: { value: 1 + i * 0.7, unit: 'Nm' },
  }) as unknown as Product,
);

function renderHeader(filter: FilterCriterion | null, onFilterChange = vi.fn()) {
  render(
    <AppProvider>
      <ColumnHeader
        attribute={torqueAttr}
        label={torqueAttr.displayName}
        products={rows}
        allProducts={rows}
        filter={filter}
        allFilters={filter ? [filter] : []}
        sortConfig={null}
        sortIndex={-1}
        totalSorts={0}
        width={160}
        unitSystem="metric"
        onUnitToggle={noop}
        onFilterChange={onFilterChange}
        onSort={noop}
        onRemove={noop}
        onResizeStart={noop}
      />
    </AppProvider>,
  );
  return onFilterChange;
}

describe('ColumnHeader popover per column', () => {
  it('at rest: trigger reads "any", no slider, no dialog', () => {
    renderHeader(null);
    expect(screen.getByRole('button', { name: 'any' })).toBeInTheDocument();
    expect(screen.queryByRole('slider')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens the popover with the slider on click and closes on Escape', () => {
    renderHeader(null);
    const trigger = screen.getByRole('button', { name: 'any' });
    fireEvent.click(trigger);
    expect(screen.getByRole('dialog', { name: 'Rated Torque filter' })).toBeInTheDocument();
    expect(screen.getByRole('slider')).toBeInTheDocument();
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('slider')).toBeNull();
  });

  it('a valued filter shows the unit-aware summary on the trigger', () => {
    renderHeader({
      attribute: 'rated_torque',
      mode: 'include',
      operator: '>=',
      value: 3.5,
      displayName: 'Rated Torque',
    });
    expect(screen.getByRole('button', { name: '≥ 3.5 Nm' })).toBeInTheDocument();
  });

  it('"clear" inside the popover hands null to onFilterChange', () => {
    const onChange = renderHeader({
      attribute: 'rated_torque',
      mode: 'include',
      operator: '>=',
      value: 3.5,
      displayName: 'Rated Torque',
    });
    fireEvent.click(screen.getByRole('button', { name: '≥ 3.5 Nm' }));
    fireEvent.click(screen.getByRole('button', { name: 'clear' }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('a valueless seeded chip still reads "any" and shows no clear button', () => {
    renderHeader({
      attribute: 'rated_torque',
      mode: 'include',
      operator: '>=',
      displayName: 'Rated Torque',
    });
    fireEvent.click(screen.getByRole('button', { name: 'any' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'clear' })).toBeNull();
  });
});
