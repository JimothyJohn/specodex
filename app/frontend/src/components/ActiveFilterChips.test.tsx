/**
 * ActiveFilterChips (UI_CLEANUP Phase 2, 2026-09-13).
 *
 * Pins: valueless seeded chips never render; a valued numeric filter
 * renders label + unit-aware summary; the × hands back the exact
 * criterion object so the parent can drop it by identity.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ActiveFilterChips from './ActiveFilterChips';
import type { AttributeMetadata, FilterCriterion } from '../types/filters';
import type { Product } from '../types/models';

const attrs: AttributeMetadata[] = [
  { key: 'rated_torque', displayName: 'Rated Torque', type: 'object', applicableTypes: ['motor'], unit: 'Nm' },
  { key: 'manufacturer', displayName: 'Manufacturer', type: 'string', applicableTypes: ['motor'] },
];

const rows = [
  { product_type: 'motor', manufacturer: 'ABB', rated_torque: { value: 3, unit: 'Nm' } },
] as unknown as Product[];

const metric = () => 'metric' as const;

describe('ActiveFilterChips', () => {
  it('renders nothing when every filter is valueless', () => {
    const { container } = render(
      <ActiveFilterChips
        filters={[{ attribute: 'rated_torque', mode: 'include', operator: '>=', displayName: 'Rated Torque' }]}
        attributes={attrs}
        products={rows}
        unitSystemFor={metric}
        onRemove={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders one chip per valued filter with a unit-aware summary', () => {
    const filters: FilterCriterion[] = [
      { attribute: 'rated_torque', mode: 'include', operator: '>=', value: 10, displayName: 'Rated Torque' },
      { attribute: 'manufacturer', mode: 'exclude', value: 'ABB', displayName: 'Manufacturer' },
      { attribute: 'rated_speed', mode: 'include', operator: '>=', displayName: 'Rated Speed' },
    ];
    render(
      <ActiveFilterChips filters={filters} attributes={attrs} products={rows} unitSystemFor={metric} onRemove={vi.fn()} />,
    );
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('Rated Torque');
    expect(items[0]).toHaveTextContent('≥ 10 Nm');
    expect(items[1]).toHaveTextContent('≠ ABB');
    expect(items[1].className).toContain('active-filter-chip--exclude');
  });

  it('× calls onRemove with the same criterion object', () => {
    const onRemove = vi.fn();
    const target: FilterCriterion = {
      attribute: 'rated_torque', mode: 'include', operator: '>=', value: 10, displayName: 'Rated Torque',
    };
    render(
      <ActiveFilterChips filters={[target]} attributes={attrs} products={rows} unitSystemFor={metric} onRemove={onRemove} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Remove Rated Torque filter' }));
    expect(onRemove).toHaveBeenCalledTimes(1);
    expect(onRemove.mock.calls[0][0]).toBe(target);
  });
});
