/**
 * Tests for FilterChip component
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '../test/utils';
import FilterChip from './FilterChip';
import { FilterCriterion, AttributeMetadata } from '../types/filters';

describe('FilterChip', () => {
  const mockFilter: FilterCriterion = {
    attribute: 'manufacturer',
    mode: 'include',
    value: 'ACME',
    displayName: 'Manufacturer',
  };

  const mockUpdate = vi.fn();
  const mockRemove = vi.fn();

  it('should render filter chip with display name', () => {
    render(
      <FilterChip
        filter={mockFilter}
        products={[]}
        suggestedValues={[]}
        onUpdate={mockUpdate}
        onRemove={mockRemove}
        onEditAttribute={vi.fn()}
      />
    );

    expect(screen.getByText(/Manufacturer/i)).toBeInTheDocument();
  });

  it('should show filter value if set', () => {
    render(
      <FilterChip
        filter={mockFilter}
        products={[]}
        suggestedValues={[]}
        onUpdate={mockUpdate}
        onRemove={mockRemove}
        onEditAttribute={vi.fn()}
      />
    );

    const input = screen.getByDisplayValue('ACME');
    expect(input).toBeInTheDocument();
  });

  it('should call onRemove when remove button clicked', () => {
    render(
      <FilterChip
        filter={mockFilter}
        products={[]}
        suggestedValues={[]}
        onUpdate={mockUpdate}
        onRemove={mockRemove}
        onEditAttribute={vi.fn()}
      />
    );

    const removeButton = screen.getByRole('button', { name: /Remove spec/i });
    fireEvent.click(removeButton);

    expect(mockRemove).toHaveBeenCalledTimes(1);
  });

  it('should handle mode changes', () => {
    const { rerender } = render(
      <FilterChip
        filter={mockFilter}
        products={[]}
        suggestedValues={[]}
        onUpdate={mockUpdate}
        onRemove={mockRemove}
        onEditAttribute={vi.fn()}
      />
    );

    const excludeFilter: FilterCriterion = {
      ...mockFilter,
      mode: 'exclude',
    };

    rerender(
      <FilterChip
        filter={excludeFilter}
        products={[]}
        suggestedValues={[]}
        onUpdate={mockUpdate}
        onRemove={mockRemove}
        onEditAttribute={vi.fn()}
      />
    );

    // Should render with different mode
    expect(screen.getByText(/Manufacturer/i)).toBeInTheDocument();
  });

  it('should render without value for new filters', () => {
    const newFilter: FilterCriterion = {
      attribute: 'manufacturer',
      mode: 'include',
      displayName: 'Manufacturer',
    };

    render(
      <FilterChip
        filter={newFilter}
        products={[]}
        suggestedValues={['ACME', 'Beta Corp']}
        onUpdate={mockUpdate}
        onRemove={mockRemove}
        onEditAttribute={vi.fn()}
      />
    );

    expect(screen.getByText(/Manufacturer/i)).toBeInTheDocument();
  });

  describe('multi-select string fields', () => {
    // Two products with distinct part numbers — getAvailableOperators
    // sees only string values for `part_number`, returns ['=', '!='],
    // and the chip switches to multi-select mode.
    const products = [
      { part_number: 'DRV-A1', product_type: 'drive' as const, manufacturer: 'X' },
      { part_number: 'DRV-B2', product_type: 'drive' as const, manufacturer: 'Y' },
      { part_number: 'DRV-C3', product_type: 'drive' as const, manufacturer: 'Z' },
    ] as any;

    const baseFilter: FilterCriterion = {
      attribute: 'part_number',
      mode: 'include',
      displayName: 'Part Number',
      operator: '=',
    };

    it('Enter on a typed value commits it as a new pill (free-form)', () => {
      const onUpdate = vi.fn();
      render(
        <FilterChip
          filter={baseFilter}
          products={products}
          suggestedValues={['DRV-A1', 'DRV-B2', 'DRV-C3']}
          onUpdate={onUpdate}
          onRemove={vi.fn()}
          onEditAttribute={vi.fn()}
        />,
      );

      const input = screen.getByPlaceholderText(/type or pick/i);
      fireEvent.change(input, { target: { value: 'NEW-FREEFORM-VALUE' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      // First commit stores as plain string (not array yet).
      expect(onUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ value: 'NEW-FREEFORM-VALUE' }),
      );
    });

    it('selecting a second suggestion stores both values as an array (OR)', () => {
      const onUpdate = vi.fn();
      const filterWithOne: FilterCriterion = {
        ...baseFilter,
        value: 'DRV-A1',
      };
      render(
        <FilterChip
          filter={filterWithOne}
          products={products}
          suggestedValues={['DRV-A1', 'DRV-B2', 'DRV-C3']}
          onUpdate={onUpdate}
          onRemove={vi.fn()}
          onEditAttribute={vi.fn()}
        />,
      );

      // Open the dropdown by focusing the input.
      const input = screen.getByPlaceholderText(/add another/i);
      fireEvent.focus(input);
      // Type a fragment to commit a second pill via Enter.
      fireEvent.change(input, { target: { value: 'DRV-B2' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      // Second commit upgrades the value to an array — that's what
      // matchesFilter() OR-matches against.
      const lastCall = onUpdate.mock.calls.at(-1)?.[0];
      expect(Array.isArray(lastCall.value)).toBe(true);
      expect(lastCall.value).toEqual(expect.arrayContaining(['DRV-A1', 'DRV-B2']));
    });

    it('Backspace on empty input removes the last pill', () => {
      const onUpdate = vi.fn();
      const filterWithTwo: FilterCriterion = {
        ...baseFilter,
        value: ['DRV-A1', 'DRV-B2'],
      };
      render(
        <FilterChip
          filter={filterWithTwo}
          products={products}
          suggestedValues={['DRV-A1', 'DRV-B2', 'DRV-C3']}
          onUpdate={onUpdate}
          onRemove={vi.fn()}
          onEditAttribute={vi.fn()}
        />,
      );

      const input = screen.getByPlaceholderText(/add another/i);
      // Input is empty; Backspace should peel off the last value.
      fireEvent.keyDown(input, { key: 'Backspace' });

      const lastCall = onUpdate.mock.calls.at(-1)?.[0];
      // One pill left after removal — collapses back to plain string.
      expect(lastCall.value).toBe('DRV-A1');
    });
  });

  describe('slider × unit system (L7)', () => {
    // Numeric ValueUnit field. The slider gates on attributeType ∈
    // {'object', 'range'}, computes its range from products, and renders
    // labels / readout in the active display unit. Filter state stays
    // canonical metric regardless of display.
    const torqueAttribute: AttributeMetadata = {
      key: 'rated_torque',
      displayName: 'Rated Torque',
      type: 'range',
      applicableTypes: ['motor'],
      unit: 'Nm',
    };

    const torqueProducts = [
      { rated_torque: { value: 5,   unit: 'Nm' }, product_type: 'motor' as const, manufacturer: 'X' },
      { rated_torque: { value: 25,  unit: 'Nm' }, product_type: 'motor' as const, manufacturer: 'X' },
      { rated_torque: { value: 100, unit: 'Nm' }, product_type: 'motor' as const, manufacturer: 'X' },
    ] as any;

    const torqueFilter: FilterCriterion = {
      attribute: 'rated_torque',
      mode: 'include',
      operator: '>=',
      displayName: 'Rated Torque',
    };

    beforeEach(() => {
      window.localStorage.clear();
    });

    function renderTorqueChip(unitSystem: 'metric' | 'imperial', onUpdate = vi.fn()) {
      window.localStorage.setItem('unitSystem', unitSystem);
      const utils = render(
        <FilterChip
          filter={torqueFilter}
          attributeType="range"
          attributeMetadata={torqueAttribute}
          products={torqueProducts}
          allProducts={torqueProducts}
          suggestedValues={[]}
          onUpdate={onUpdate}
          onRemove={vi.fn()}
          onEditAttribute={vi.fn()}
        />,
      );
      return { ...utils, onUpdate };
    }

    it('renders slider min/max in metric (Nm) when unitSystem is metric', () => {
      renderTorqueChip('metric');
      // The two range labels are the smallest and largest data points.
      // In metric they render unconverted: 5 and 100.
      expect(screen.getByText('5')).toBeInTheDocument();
      expect(screen.getByText('100')).toBeInTheDocument();
      // Readout button shows "<value> Nm".
      const readout = screen.getByRole("button", { name: /type an exact value/i });
      expect(readout.textContent).toContain('Nm');
      expect(readout.textContent).not.toContain('in·lb');
    });

    it('renders slider min/max in imperial (in·lb) when unitSystem is imperial', () => {
      renderTorqueChip('imperial');
      // 5 Nm × 8.850746 ≈ 44.25, 100 Nm ≈ 885.07 — roundDisplay trims
      // to 4 sig figs (44.25 and 885.1).
      expect(screen.getByText('44.25')).toBeInTheDocument();
      expect(screen.getByText('885.1')).toBeInTheDocument();
      const readout = screen.getByRole("button", { name: /type an exact value/i });
      expect(readout.textContent).toContain('in·lb');
      expect(readout.textContent).not.toMatch(/\bNm\b/);
    });

    it('round-trips an imperial typed value back to canonical metric via onUpdate', () => {
      const { onUpdate } = renderTorqueChip('imperial');
      onUpdate.mockClear();   // discard the auto-seed call from useEffect

      // Open the value editor.
      fireEvent.click(screen.getByRole("button", { name: /type an exact value/i }));
      const input = screen.getByLabelText(/override slider with typed value/i) as HTMLInputElement;

      // User types 100 in·lb. Canonical = 100 / 8.850746 ≈ 11.30 Nm.
      fireEvent.change(input, { target: { value: '100' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      const lastCall = onUpdate.mock.calls.at(-1)?.[0];
      expect(typeof lastCall.value).toBe('number');
      expect(lastCall.value).toBeCloseTo(100 / 8.850746, 3);
      // Operator preserved through the round trip.
      expect(lastCall.operator).toBe('>=');
    });

    it('falls back gracefully when records carry no unit string', () => {
      // Same numeric values, but the per-record unit is missing — only
      // attributeMetadata.unit (Nm) supplies the display unit. The slider
      // should still render valid min/max labels (no NaN).
      const noUnitProducts = [
        { rated_torque: { value: 5   }, product_type: 'motor' as const, manufacturer: 'X' },
        { rated_torque: { value: 100 }, product_type: 'motor' as const, manufacturer: 'X' },
      ] as any;

      window.localStorage.setItem('unitSystem', 'metric');
      render(
        <FilterChip
          filter={torqueFilter}
          attributeType="range"
          attributeMetadata={torqueAttribute}
          products={noUnitProducts}
          allProducts={noUnitProducts}
          suggestedValues={[]}
          onUpdate={vi.fn()}
          onRemove={vi.fn()}
          onEditAttribute={vi.fn()}
        />,
      );

      // No NaN should leak into the rendered range labels or readout.
      expect(document.body.textContent).not.toMatch(/NaN/);
      // Range labels still resolve to the underlying numeric values.
      expect(screen.getByText('5')).toBeInTheDocument();
      expect(screen.getByText('100')).toBeInTheDocument();
    });
  });
});
