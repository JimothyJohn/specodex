/**
 * "geared" header marker (UI_CLEANUP N7, 2026-09-13).
 *
 * While the motor gear-ratio cascade is active, ProductList rescales
 * each row's torque and speed by its picked ratio. The columns used to
 * announce that with a full-height percentile tint — which also fired
 * for the *valueless* default chips, painting both columns on every
 * motor load. The tint is gone; the meaning ("these numbers are
 * post-gear") now lives in a header-only marker driven by the
 * `cascadeKey` prop. This pins: marker present iff the prop is set,
 * and the memo comparator re-renders when it flips.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ColumnHeader from './ColumnHeader';
import { AppProvider } from '../context/AppContext';
import type { AttributeMetadata } from '../types/filters';
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
};

const rows: Product[] = Array.from({ length: 12 }, (_, i) =>
  ({
    product_type: 'motor',
    manufacturer: 'X',
    rated_torque: { value: 1 + i * 0.7, unit: 'Nm' },
  }) as unknown as Product,
);

function renderHeader(cascadeKey: boolean | undefined) {
  return render(
    <AppProvider>
      <ColumnHeader
        attribute={torqueAttr}
        label={torqueAttr.displayName}
        products={rows}
        allProducts={rows}
        filter={null}
        allFilters={[]}
        sortConfig={null}
        sortIndex={-1}
        totalSorts={0}
        cascadeKey={cascadeKey}
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

describe('ColumnHeader cascade marker', () => {
  it('renders no marker by default (prop omitted)', () => {
    renderHeader(undefined);
    expect(screen.queryByText('geared')).toBeNull();
  });

  it('renders no marker when cascadeKey is false', () => {
    renderHeader(false);
    expect(screen.queryByText('geared')).toBeNull();
  });

  it('renders the "geared" marker when cascadeKey is true', () => {
    renderHeader(true);
    expect(screen.getByText('geared')).toBeInTheDocument();
  });

  it('re-renders when cascadeKey flips (memo comparator includes it)', () => {
    const { rerender } = renderHeader(false);
    expect(screen.queryByText('geared')).toBeNull();
    rerender(
      <AppProvider>
        <ColumnHeader
          attribute={torqueAttr}
          label={torqueAttr.displayName}
          products={rows}
          allProducts={rows}
          filter={null}
          allFilters={[]}
          sortConfig={null}
          sortIndex={-1}
          totalSorts={0}
          cascadeKey={true}
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
    expect(screen.getByText('geared')).toBeInTheDocument();
  });
});
