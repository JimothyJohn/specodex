/**
 * MultiSelectFilterPopover — column-header value picker.
 *
 * Locks down two regressions that bit users:
 *
 *   1. Toggling exclude (−) BEFORE picking any value used to be silently
 *      dropped — buildFilter([], 'exclude') returned null, the parent
 *      received no update, the next pick re-defaulted to include. Now the
 *      mode is tracked locally and applied to the first picked value.
 *
 *   2. The popover anchors to a trigger via `anchorEl`. If the trigger
 *      is wrapped in `<Tooltip>`, the trigger ref must still receive the
 *      DOM node (covered in Tooltip.test.tsx). Here we only exercise the
 *      popover's own contract; positioning math (jsdom returns zeros)
 *      belongs in a Playwright smoke.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { useState } from 'react';
import MultiSelectFilterPopover from './MultiSelectFilterPopover';
import { FilterCriterion } from '../types/filters';

afterEach(() => cleanup());

interface HarnessProps {
  options: Array<string | number>;
  initialFilter?: FilterCriterion | null;
  onChangeSpy: (next: FilterCriterion | null) => void;
}

function Harness({ options, initialFilter = null, onChangeSpy }: HarnessProps) {
  const [anchorEl, setAnchorEl] = useState<HTMLButtonElement | null>(null);
  const [filter, setFilter] = useState<FilterCriterion | null>(initialFilter);
  return (
    <>
      <button ref={setAnchorEl} type="button">anchor</button>
      <MultiSelectFilterPopover
        open
        anchorEl={anchorEl}
        options={options}
        filter={filter}
        attributeLabel="Manufacturer"
        attributeKey="manufacturer"
        onClose={() => {}}
        onChange={(next) => {
          onChangeSpy(next);
          setFilter(next);
        }}
      />
    </>
  );
}

beforeEach(() => {
  // jsdom getBoundingClientRect returns zeros, but the popover only needs
  // anchorEl to be a non-null Node for its useLayoutEffect to compute a
  // (zeroed) rect and render. That's enough for behavioral tests.
});

describe('MultiSelectFilterPopover', () => {
  describe('options list', () => {
    it('renders every option with role=option', () => {
      const onChange = vi.fn();
      render(
        <Harness
          options={['ABB', 'Mitsubishi', 'Siemens']}
          onChangeSpy={onChange}
        />,
      );
      const opts = screen.getAllByRole('option');
      expect(opts.map((o) => o.textContent?.trim())).toEqual(['ABB', 'Mitsubishi', 'Siemens']);
    });

    it('shows "No values" when options is empty', () => {
      render(
        <Harness options={[]} onChangeSpy={vi.fn()} />,
      );
      expect(screen.getByText('No values')).toBeDefined();
    });

    it('marks options aria-selected when present in the filter value', () => {
      render(
        <Harness
          options={['ABB', 'Mitsubishi', 'Siemens']}
          initialFilter={{
            attribute: 'manufacturer',
            displayName: 'Manufacturer',
            mode: 'include',
            operator: '=',
            value: 'Mitsubishi',
          }}
          onChangeSpy={vi.fn()}
        />,
      );
      const opts = screen.getAllByRole('option');
      expect(opts[0].getAttribute('aria-selected')).toBe('false');
      expect(opts[1].getAttribute('aria-selected')).toBe('true');
      expect(opts[2].getAttribute('aria-selected')).toBe('false');
    });
  });

  describe('selection', () => {
    it('builds a single-value include filter on first click', () => {
      const onChange = vi.fn();
      render(
        <Harness
          options={['ABB', 'Mitsubishi', 'Siemens']}
          onChangeSpy={onChange}
        />,
      );
      fireEvent.click(screen.getByText('Mitsubishi'));
      expect(onChange).toHaveBeenCalledTimes(1);
      expect(onChange).toHaveBeenCalledWith({
        attribute: 'manufacturer',
        displayName: 'Manufacturer',
        mode: 'include',
        operator: '=',
        value: 'Mitsubishi',
      });
    });

    it('promotes value to an array when a second value is added', () => {
      const onChange = vi.fn();
      render(
        <Harness
          options={['ABB', 'Mitsubishi', 'Siemens']}
          initialFilter={{
            attribute: 'manufacturer',
            displayName: 'Manufacturer',
            mode: 'include',
            operator: '=',
            value: 'Mitsubishi',
          }}
          onChangeSpy={onChange}
        />,
      );
      fireEvent.click(screen.getByText('ABB'));
      expect(onChange).toHaveBeenLastCalledWith(
        expect.objectContaining({
          mode: 'include',
          value: ['ABB', 'Mitsubishi'],
        }),
      );
    });

    it('toggling a selected value removes it', () => {
      const onChange = vi.fn();
      render(
        <Harness
          options={['ABB', 'Mitsubishi']}
          initialFilter={{
            attribute: 'manufacturer',
            displayName: 'Manufacturer',
            mode: 'include',
            operator: '=',
            value: ['ABB', 'Mitsubishi'],
          }}
          onChangeSpy={onChange}
        />,
      );
      fireEvent.click(screen.getByText('ABB'));
      expect(onChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ value: 'Mitsubishi' }),
      );
    });

    it('toggling the last selected value clears the filter (null)', () => {
      const onChange = vi.fn();
      render(
        <Harness
          options={['ABB']}
          initialFilter={{
            attribute: 'manufacturer',
            displayName: 'Manufacturer',
            mode: 'include',
            operator: '=',
            value: 'ABB',
          }}
          onChangeSpy={onChange}
        />,
      );
      fireEvent.click(screen.getByText('ABB'));
      expect(onChange).toHaveBeenCalledWith(null);
    });

    it('preserves number type when options are numeric (no string coercion)', () => {
      const onChange = vi.fn();
      render(
        <Harness options={[120, 240, 480]} onChangeSpy={onChange} />,
      );
      fireEvent.click(screen.getByText('240'));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ value: 240 }),
      );
    });
  });

  describe('mode toggle', () => {
    it('REGRESSION: clicking exclude before any selection persists, then applies to first pick', () => {
      // Pre-fix: setMode('exclude') with no selection called
      // buildFilter([], 'exclude') → null → onChange got no update, the
      // next click reverted to include. The fix tracks mode locally so
      // the first picked value carries the user's chosen mode.
      const onChange = vi.fn();
      render(
        <Harness
          options={['ABB', 'Mitsubishi']}
          onChangeSpy={onChange}
        />,
      );

      // Click '−' (exclude) before any value is selected.
      const excludeBtn = screen.getByRole('radio', { name: /exclude/i });
      fireEvent.click(excludeBtn);
      // No onChange yet — there's nothing to filter.
      expect(onChange).not.toHaveBeenCalled();
      // But the radio reflects the chosen mode.
      expect(excludeBtn.getAttribute('aria-checked')).toBe('true');
      expect(
        screen.getByRole('radio', { name: /include/i }).getAttribute('aria-checked'),
      ).toBe('false');

      // Now pick a value — it should land in EXCLUDE mode.
      fireEvent.click(screen.getByText('ABB'));
      expect(onChange).toHaveBeenCalledTimes(1);
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ mode: 'exclude', value: 'ABB' }),
      );
    });

    it('flipping mode while a selection exists immediately re-emits the filter', () => {
      const onChange = vi.fn();
      render(
        <Harness
          options={['ABB']}
          initialFilter={{
            attribute: 'manufacturer',
            displayName: 'Manufacturer',
            mode: 'include',
            operator: '=',
            value: 'ABB',
          }}
          onChangeSpy={onChange}
        />,
      );
      fireEvent.click(screen.getByRole('radio', { name: /exclude/i }));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ mode: 'exclude', value: 'ABB' }),
      );
    });

    it('hydrates the local mode from an existing exclude filter', () => {
      render(
        <Harness
          options={['ABB']}
          initialFilter={{
            attribute: 'manufacturer',
            displayName: 'Manufacturer',
            mode: 'exclude',
            operator: '=',
            value: 'ABB',
          }}
          onChangeSpy={vi.fn()}
        />,
      );
      expect(
        screen.getByRole('radio', { name: /exclude/i }).getAttribute('aria-checked'),
      ).toBe('true');
    });

    it('flipping mode without a selection does NOT call onChange', () => {
      const onChange = vi.fn();
      render(
        <Harness options={['ABB']} onChangeSpy={onChange} />,
      );
      fireEvent.click(screen.getByRole('radio', { name: /exclude/i }));
      fireEvent.click(screen.getByRole('radio', { name: /include/i }));
      fireEvent.click(screen.getByRole('radio', { name: /exclude/i }));
      expect(onChange).not.toHaveBeenCalled();
    });
  });

  describe('clear', () => {
    it('Clear button calls onChange(null) and shows the selected count', () => {
      const onChange = vi.fn();
      render(
        <Harness
          options={['ABB', 'Mitsubishi']}
          initialFilter={{
            attribute: 'manufacturer',
            displayName: 'Manufacturer',
            mode: 'include',
            operator: '=',
            value: ['ABB', 'Mitsubishi'],
          }}
          onChangeSpy={onChange}
        />,
      );
      const clearBtn = screen.getByRole('button', { name: /Clear \(2\)/ });
      fireEvent.click(clearBtn);
      expect(onChange).toHaveBeenCalledWith(null);
    });

    it('hides the Clear button when nothing is selected', () => {
      render(
        <Harness options={['ABB']} onChangeSpy={vi.fn()} />,
      );
      expect(screen.queryByRole('button', { name: /Clear/ })).toBeNull();
    });
  });

  describe('lifecycle', () => {
    it('renders nothing when open=false', () => {
      function ClosedHarness() {
        const [anchorEl, setAnchorEl] = useState<HTMLButtonElement | null>(null);
        return (
          <>
            <button ref={setAnchorEl} type="button">anchor</button>
            <MultiSelectFilterPopover
              open={false}
              anchorEl={anchorEl}
              options={['ABB']}
              filter={null}
              attributeLabel="Manufacturer"
              attributeKey="manufacturer"
              onClose={() => {}}
              onChange={() => {}}
            />
          </>
        );
      }
      render(<ClosedHarness />);
      expect(screen.queryByRole('listbox')).toBeNull();
      expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('mousedown outside the popover invokes onClose', () => {
      const onClose = vi.fn();
      function CloseHarness() {
        const [anchorEl, setAnchorEl] = useState<HTMLButtonElement | null>(null);
        return (
          <>
            <button ref={setAnchorEl} type="button">anchor</button>
            <button type="button" data-testid="outside">outside</button>
            <MultiSelectFilterPopover
              open
              anchorEl={anchorEl}
              options={['ABB']}
              filter={null}
              attributeLabel="Manufacturer"
              attributeKey="manufacturer"
              onClose={onClose}
              onChange={() => {}}
            />
          </>
        );
      }
      render(<CloseHarness />);
      fireEvent.mouseDown(screen.getByTestId('outside'));
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('Escape invokes onClose', () => {
      const onClose = vi.fn();
      function EscHarness() {
        const [anchorEl, setAnchorEl] = useState<HTMLButtonElement | null>(null);
        return (
          <>
            <button ref={setAnchorEl} type="button">anchor</button>
            <MultiSelectFilterPopover
              open
              anchorEl={anchorEl}
              options={['ABB']}
              filter={null}
              attributeLabel="Manufacturer"
              attributeKey="manufacturer"
              onClose={onClose}
              onChange={() => {}}
            />
          </>
        );
      }
      render(<EscHarness />);
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });
});
