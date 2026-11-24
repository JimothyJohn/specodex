/**
 * Slider-eligibility gate for bare-number columns (2026-07-25).
 *
 * gear_ratio / efficiency are plain floats. The column-header filter UI
 * gated the slider on attributeType ∈ {'object','range'}, so numeric
 * gearbox specs fell through to the multi-select popover ("a selection")
 * — reported twice: first fixed in FilterChip (PR #356), which turned
 * out to be the datasheets-page component; ColumnHeader is the
 * products-page filter UI. High-cardinality numerics (> 10 distinct)
 * slide; small discrete sets (stages) keep the pick list.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ColumnHeader from './ColumnHeader';
import { AppProvider } from '../context/AppContext';
import type { AttributeMetadata } from '../types/filters';
import type { Product } from '../types/models';

// ColumnHeader reads useApp; the provider's data methods are never
// invoked in these renders, so stub the client wholesale.
vi.mock('../api/client', () => ({
  apiClient: new Proxy(
    {},
    { get: () => vi.fn(() => Promise.reject(new Error('not in this test'))) },
  ),
}));

const noop = () => {};

function renderHeader(attribute: AttributeMetadata, rows: Product[]) {
  return render(
    <AppProvider>
    <ColumnHeader
      attribute={attribute}
      label={attribute.displayName}
      products={rows}
      allProducts={rows}
      filter={null}
      allFilters={[]}
      sortConfig={null}
      sortIndex={-1}
      totalSorts={0}
      width={160}
      unitSystem="metric"
      onUnitToggle={noop}
      onFilterChange={vi.fn()}
      onSort={noop}
      onRemove={noop}
      onResizeStart={noop}
    />
    </AppProvider>,
  );
}

const gearRatioAttr: AttributeMetadata = {
  key: 'gear_ratio',
  displayName: 'Gear Ratio',
  type: 'number',
  applicableTypes: ['gearhead'],
};

const stagesAttr: AttributeMetadata = {
  key: 'stages',
  displayName: 'Stages',
  type: 'number',
  applicableTypes: ['gearhead'],
};

const row = (fields: Record<string, unknown>): Product =>
  ({ product_type: 'gearhead', manufacturer: 'X', ...fields }) as Product;

describe('ColumnHeader slider gate for number attributes', () => {
  // Since UI_CLEANUP Phase 2 (2026-09-13) the slider lives in a popover
  // under the header's filter trigger; open it before asserting.
  const openFilterPopover = () =>
    fireEvent.click(screen.getByRole('button', { name: /any/i }));

  it('high-cardinality numeric column renders the slider', () => {
    const rows = Array.from({ length: 15 }, (_, i) => row({ gear_ratio: 3 + i * 7.5 }));
    renderHeader(gearRatioAttr, rows);
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    openFilterPopover();
    expect(screen.getByRole('slider')).toBeInTheDocument();
  });

  it('low-cardinality numeric column keeps the multi-select popover', () => {
    const rows = Array.from({ length: 15 }, (_, i) => row({ stages: (i % 3) + 1 }));
    renderHeader(stagesAttr, rows);
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    const trigger = screen.getByRole('button', { name: /any/i });
    expect(trigger).toBeInTheDocument();
    // The categorical trigger opens the multi-select listbox, not a slider.
    fireEvent.click(trigger);
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    expect(screen.getByRole('listbox')).toBeInTheDocument();
  });

  it('unit-bearing object column still renders the slider', () => {
    const attr: AttributeMetadata = {
      key: 'max_continuous_torque',
      displayName: 'Rated Torque',
      type: 'object',
      applicableTypes: ['gearhead'],
      nested: true,
      unit: 'Nm',
    };
    const rows = [
      row({ max_continuous_torque: { value: 10, unit: 'Nm' } }),
      row({ max_continuous_torque: { value: 90, unit: 'Nm' } }),
    ];
    renderHeader(attr, rows);
    openFilterPopover();
    expect(screen.getByRole('slider')).toBeInTheDocument();
  });
});
