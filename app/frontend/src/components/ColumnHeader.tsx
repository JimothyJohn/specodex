/**
 * Column header that combines the histogram, sortable label, and inline
 * slider for one product attribute. Replaces the separate filter pane:
 * every filter lives in the column it filters.
 *
 * Layout (fixed-height rows so the grid lines up across columns):
 *
 *   ┌───────────────────────────┐
 *   │ RATED TORQUE  ↑       [X] │  TOP_H — label + close X share one row
 *   │ ▁▂▅▇▅▂▁ histogram         │  HIST_H
 *   │ ━━━━●━━━━━━━━━━━━         │  SLIDER_H — sits tight under the histogram
 *   │       0.3                 │  VALUE_H — value box, full width, prominent
 *   │ ≥                    Nm   │  BOTTOM_H — operator left, unit right
 *   └───────────────────────────┘
 *
 * Histogram and slider scale are anchored to `allProducts` (the
 * unfiltered, linearized source) so the visual reference stays put as
 * the user dials in filters. Bar heights still come from the filtered
 * `products` so the user can see the slice they've selected.
 *
 * Pristine state: when no filter exists for this column, the slider is
 * parked at 0% with operator '>=' and the readout reads 'any'. The first
 * pointer-down on the track promotes it into a real filter.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AttributeMetadata,
  ComparisonOperator,
  FilterCriterion,
  SortConfig,
  applyFilters,
} from '../types/filters';
import { Product } from '../types/models';
import {
  UnitSystem,
  displayUnit,
  isIntegerUnit,
  toCanonical,
  toDisplay,
} from '../utils/unitConversion';
import { useApp } from '../context/AppContext';
import DistributionChart from './DistributionChart';
import MultiSelectFilterPopover from './MultiSelectFilterPopover';
import Tooltip from './ui/Tooltip';

interface ColumnHeaderProps {
  attribute: AttributeMetadata;
  label: string;
  /** Filtered, post-linearization rows. Drives the histogram bar heights. */
  products: Product[];
  /** Unfiltered linearized source. Anchors the slider scale and the
   *  histogram x-range so the visual reference doesn't jump. */
  allProducts: Product[];
  filter: FilterCriterion | null;
  /** Every active filter on the page. Used to narrow the multi-select
   *  popover's option list to combinations that actually exist — selecting
   *  Manufacturer=ABB shouldn't leave dead IP-Rating choices on the table. */
  allFilters: FilterCriterion[];
  sortConfig: SortConfig | null;
  sortIndex: number;
  totalSorts: number;
  width: number;
  /** Effective unit system for this column. Per-column, not global —
   *  flipping one column doesn't drag its neighbors. */
  unitSystem: UnitSystem;
  /** Flip just this column between metric and imperial. Wired to a
   *  per-column override map in the parent. */
  onUnitToggle: () => void;
  onFilterChange: (filter: FilterCriterion | null) => void;
  onSort: () => void;
  onRemove: () => void;
  onResizeStart: (e: React.MouseEvent) => void;
}

const SLIDER_OPERATORS: ComparisonOperator[] = ['>=', '<'];

const getNested = (obj: unknown, path: string): unknown => {
  if (!obj || typeof obj !== 'object') return undefined;
  const parts = path.split('.');
  let v: any = obj;
  for (const p of parts) {
    if (v == null) return undefined;
    v = v[p];
  }
  return v;
};

const numericFromValue = (val: unknown): number | null => {
  if (typeof val === 'number') return val;
  if (val && typeof val === 'object') {
    const o = val as { value?: unknown; min?: unknown; max?: unknown };
    if (typeof o.value === 'number') return o.value;
    if (typeof o.min === 'number' && typeof o.max === 'number') {
      return (o.min + o.max) / 2;
    }
  }
  return null;
};

const sniffUnit = (val: unknown): string | null => {
  if (val && typeof val === 'object' && 'unit' in val) {
    const u = (val as { unit?: unknown }).unit;
    if (typeof u === 'string') return u;
  }
  return null;
};

export default function ColumnHeader({
  attribute,
  label,
  products,
  allProducts,
  filter,
  allFilters,
  sortConfig,
  sortIndex,
  totalSorts,
  width,
  unitSystem,
  onUnitToggle,
  onFilterChange,
  onSort,
  onRemove,
  onResizeStart,
}: ColumnHeaderProps) {
  const { rowDensity } = useApp();
  const isCompact = rowDensity === 'compact';
  const sliderTrackRef = useRef<HTMLDivElement>(null);
  const sliderInputRef = useRef<HTMLInputElement>(null);
  const multiTriggerRef = useRef<HTMLButtonElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [editingValue, setEditingValue] = useState(false);
  const [valueDraft, setValueDraft] = useState('');
  const [multiOpen, setMultiOpen] = useState(false);

  // Slider scale comes from the *unfiltered* source so the track doesn't
  // shrink as the user dials in other filters. Lets the user always
  // drag the thumb back out — and matches the histogram's anchored
  // x-range below.
  const rangeInfo = useMemo(() => {
    const values: number[] = [];
    let attrUnit: string | null = null;
    for (const p of allProducts) {
      const raw = getNested(p, attribute.key);
      if (raw == null) continue;
      const n = numericFromValue(raw);
      if (n != null && Number.isFinite(n)) {
        values.push(n);
        if (!attrUnit) attrUnit = sniffUnit(raw);
      }
    }
    if (values.length === 0) return null;
    values.sort((a, b) => a - b);
    return {
      min: values[0],
      max: values[values.length - 1],
      sortedValues: values,
      unit: attrUnit ?? attribute.unit ?? '',
    };
  }, [allProducts, attribute.key, attribute.unit]);

  const isSliderEligible =
    (attribute.type === 'object' || attribute.type === 'range') && rangeInfo !== null;

  // For non-slider columns (string, number, array) — the values the
  // multi-select popover offers. Strings/numbers come straight off the
  // record; arrays get flattened so a `fieldbus: ['EtherCAT','Modbus']`
  // surfaces 'EtherCAT' and 'Modbus' as separate selectable options.
  //
  // Source set: allProducts narrowed by every active filter EXCEPT this
  // column's own. That way picking Manufacturer=ABB hides IP-Rating
  // choices that no ABB drive carries (no dead-end selections), while
  // leaving the column's own already-selected values visible (otherwise
  // the chosen value would erase its peers from its own popover).
  const multiSelectOptions = useMemo(() => {
    if (isSliderEligible) return [];
    if (attribute.type !== 'string' && attribute.type !== 'number' && attribute.type !== 'array') {
      return [];
    }
    const otherFilters = allFilters.filter(f => f !== filter);
    const source = otherFilters.length === 0 ? allProducts : applyFilters(allProducts, otherFilters);
    const seen = new Set<string>();
    const out: Array<string | number> = [];
    for (const p of source) {
      const raw = getNested(p, attribute.key);
      if (raw == null) continue;
      const push = (v: unknown) => {
        if (typeof v !== 'string' && typeof v !== 'number') return;
        const key = String(v);
        if (seen.has(key)) return;
        seen.add(key);
        out.push(v);
      };
      if (Array.isArray(raw)) raw.forEach(push);
      else push(raw);
    }
    out.sort((a, b) => {
      if (typeof a === 'number' && typeof b === 'number') return a - b;
      return String(a).localeCompare(String(b), undefined, { numeric: true });
    });
    return out;
  }, [allProducts, attribute.key, attribute.type, isSliderEligible, allFilters, filter]);

  // Filter polarity drives the chip's tint. Slider filters always read
  // as 'include' (you can't exclude with `>=`); multi-select filters
  // can be either, hence the explicit branch.
  const filterMode: 'include' | 'exclude' | null =
    filter == null ? null : (filter.mode === 'exclude' ? 'exclude' : 'include');
  const hasActiveFilter =
    filter != null && (
      isSliderEligible
        ? typeof filter.value === 'number'
        : (Array.isArray(filter.value)
            ? filter.value.length > 0
            : filter.value != null && filter.value !== '')
    );

  const operator: ComparisonOperator =
    (filter?.operator as ComparisonOperator) ?? '>=';
  const filterValue =
    typeof filter?.value === 'number' ? (filter.value as number) : null;

  const sliderPercent = useMemo(() => {
    if (!rangeInfo) return 0;
    if (filterValue == null) {
      return operator === '<' || operator === '<=' ? 100 : 0;
    }
    const n = rangeInfo.sortedValues.length;
    if (n <= 1) return 0;
    let lo = 0;
    let hi = n - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (rangeInfo.sortedValues[mid] < filterValue) lo = mid + 1;
      else hi = mid;
    }
    let idx = lo;
    if (
      lo > 0 &&
      Math.abs(rangeInfo.sortedValues[lo - 1] - filterValue) <
        Math.abs(rangeInfo.sortedValues[lo] - filterValue)
    ) {
      idx = lo - 1;
    }
    return (idx / (n - 1)) * 100;
  }, [rangeInfo, filterValue, operator]);

  const updateFromPointer = (clientX: number) => {
    if (!rangeInfo) return;
    const track = sliderTrackRef.current;
    if (!track) return;
    const rect = track.getBoundingClientRect();
    if (rect.width === 0) return;
    const t = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const n = rangeInfo.sortedValues.length;
    if (n <= 1) return;
    const idx = Math.round(t * (n - 1));
    const newValue = rangeInfo.sortedValues[idx];
    if (filter) {
      if (filter.value !== newValue) {
        onFilterChange({ ...filter, value: newValue });
      }
    } else {
      onFilterChange({
        attribute: attribute.key,
        displayName: label,
        mode: 'include',
        operator: '>=',
        value: newValue,
      });
    }
  };

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!rangeInfo) return;
    e.preventDefault();
    e.stopPropagation();
    sliderTrackRef.current?.setPointerCapture(e.pointerId);
    setIsDragging(true);
    updateFromPointer(e.clientX);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isDragging) return;
    updateFromPointer(e.clientX);
  };

  const handlePointerEnd = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isDragging) return;
    sliderTrackRef.current?.releasePointerCapture(e.pointerId);
    setIsDragging(false);
  };

  const handleSliderKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!rangeInfo) return;
    const n = rangeInfo.sortedValues.length;
    if (n <= 1) return;
    const currentIdx =
      filterValue != null ? rangeInfo.sortedValues.indexOf(filterValue) : -1;
    const idx = currentIdx === -1 ? 0 : currentIdx;
    let next: number;
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowUp':
        next = Math.min(n - 1, idx + 1);
        break;
      case 'ArrowLeft':
      case 'ArrowDown':
        next = Math.max(0, idx - 1);
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = n - 1;
        break;
      case 'PageUp':
        next = Math.min(n - 1, idx + Math.max(1, Math.round(n / 10)));
        break;
      case 'PageDown':
        next = Math.max(0, idx - Math.max(1, Math.round(n / 10)));
        break;
      default:
        return;
    }
    e.preventDefault();
    const newValue = rangeInfo.sortedValues[next];
    if (filter) {
      onFilterChange({ ...filter, value: newValue });
    } else {
      onFilterChange({
        attribute: attribute.key,
        displayName: label,
        mode: 'include',
        operator: '>=',
        value: newValue,
      });
    }
  };

  const cycleOperator = () => {
    const idx = SLIDER_OPERATORS.indexOf(operator);
    const next = SLIDER_OPERATORS[(idx + 1) % SLIDER_OPERATORS.length];
    if (filter) {
      onFilterChange({ ...filter, operator: next });
    } else if (rangeInfo) {
      const seedIdx = next === '<' ? rangeInfo.sortedValues.length - 1 : 0;
      onFilterChange({
        attribute: attribute.key,
        displayName: label,
        mode: 'include',
        operator: next,
        value: rangeInfo.sortedValues[seedIdx],
      });
    }
  };

  const commitOverride = () => {
    if (!rangeInfo) {
      setEditingValue(false);
      return;
    }
    const trimmed = valueDraft.trim();
    if (trimmed === '') {
      setEditingValue(false);
      return;
    }
    const parsed = parseFloat(trimmed);
    if (Number.isNaN(parsed)) {
      setEditingValue(false);
      return;
    }
    const canonical = rangeInfo.unit
      ? toCanonical(parsed, rangeInfo.unit, unitSystem)
      : parsed;
    if (filter) {
      onFilterChange({ ...filter, value: canonical });
    } else {
      onFilterChange({
        attribute: attribute.key,
        displayName: label,
        mode: 'include',
        operator: '>=',
        value: canonical,
      });
    }
    setEditingValue(false);
  };

  const cancelOverride = () => {
    setEditingValue(false);
    setValueDraft('');
  };

  useEffect(() => {
    if (editingValue) sliderInputRef.current?.focus();
  }, [editingValue]);

  // Click the unit text to flip *this* column's unit system. Other
  // columns keep whatever unit they had — overrides are per-column.
  const handleUnitClick = () => {
    onUnitToggle();
  };

  const headerClasses =
    'product-grid-header-item column-header'
    + (hasActiveFilter ? ' has-filter' : '')
    + (hasActiveFilter && filterMode === 'include' ? ' has-include-filter' : '')
    + (hasActiveFilter && filterMode === 'exclude' ? ' has-exclude-filter' : '');

  // Selected count for the multi-select trigger label.
  const multiSelectedCount = (() => {
    if (!filter) return 0;
    const v = filter.value;
    if (Array.isArray(v)) return v.length;
    if (v == null || v === '') return 0;
    return 1;
  })();

  const dispCurrent =
    rangeInfo && filterValue != null
      ? toDisplay(filterValue, rangeInfo.unit, unitSystem)
      : null;
  const dispUnit = rangeInfo?.unit ? displayUnit(rangeInfo.unit, unitSystem) : '';
  const intLikeUnit = rangeInfo ? isIntegerUnit(rangeInfo.unit) : false;

  // Compact-only: substring text filter for categorical columns. The
  // existing applyFilters logic already does case-insensitive substring
  // match when filter.value is a string (filters.ts:matchesFilter),
  // so we just wire an <input> straight through filter.value. Empty
  // string clears the filter rather than persisting a no-op that
  // would also confuse the cozy multi-select popover.
  const setSubstring = (raw: string) => {
    const next = raw;
    if (next.trim() === '') {
      if (filter) onFilterChange(null);
      return;
    }
    if (filter) {
      onFilterChange({ ...filter, value: next });
    } else {
      onFilterChange({
        attribute: attribute.key,
        displayName: label,
        mode: 'include',
        value: next,
      });
    }
  };

  const flipCategoricalMode = () => {
    if (!filter) return;
    onFilterChange({
      ...filter,
      mode: filter.mode === 'exclude' ? 'include' : 'exclude',
    });
  };

  // Compact path: single-row layout with no histogram and no slider.
  // Strips the column header down to the affordances the user can
  // act on (sort label, operator/mode, value/text, unit, close) so
  // the table gets the vertical pixels back. The cozy path below
  // keeps the full histogram + slider stack.
  if (isCompact) {
    return (
      <div className={headerClasses + ' compact-column-header'} style={{ width }}>
        <Tooltip content="Click to sort • click again to reverse, again to clear">
          <button
            type="button"
            className="column-header-sort compact-column-sort"
            onClick={onSort}
          >
            <span className="column-header-label-text">{label}</span>
            <span className="sort-indicator">
              {sortConfig?.direction === 'asc' && '↑'}
              {sortConfig?.direction === 'desc' && '↓'}
              {sortConfig && totalSorts > 1 && (
                <span className="sort-order">{sortIndex + 1}</span>
              )}
            </span>
          </button>
        </Tooltip>

        {isSliderEligible && rangeInfo && (
          <>
            <Tooltip content={`Operator ${operator} — click to flip (>= ↔ <)`}>
              <button
                type="button"
                className="readout-operator compact-readout-operator"
                onClick={cycleOperator}
                aria-label={`Filter operator ${operator}`}
              >
                {operator === '>=' ? '≥' : operator === '<=' ? '≤' : operator}
              </button>
            </Tooltip>
            {editingValue ? (
              <input
                ref={sliderInputRef}
                type="number"
                className="readout-value-input compact-readout-value-input"
                value={valueDraft}
                step="any"
                onChange={(e) => setValueDraft(e.target.value)}
                onBlur={commitOverride}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    commitOverride();
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    cancelOverride();
                  }
                }}
                aria-label="Override threshold value"
              />
            ) : (
              <Tooltip content="Click to type a threshold">
                <button
                  type="button"
                  className="readout-value compact-readout-value"
                  onClick={() => {
                    if (dispCurrent != null) {
                      setValueDraft(
                        intLikeUnit
                          ? String(Math.round(dispCurrent))
                          : String(Number(dispCurrent.toFixed(2))),
                      );
                    } else {
                      setValueDraft('');
                    }
                    setEditingValue(true);
                  }}
                >
                  {dispCurrent != null
                    ? intLikeUnit
                      ? Math.round(dispCurrent).toLocaleString()
                      : dispCurrent.toFixed(1)
                    : 'any'}
                </button>
              </Tooltip>
            )}
            {dispUnit && (
              <Tooltip content={`Click to switch units (currently ${unitSystem})`}>
                <button
                  type="button"
                  className="readout-unit compact-readout-unit"
                  onClick={handleUnitClick}
                  aria-label={`Unit ${dispUnit} — click to swap unit system`}
                >
                  {dispUnit}
                </button>
              </Tooltip>
            )}
          </>
        )}

        {!isSliderEligible && multiSelectOptions.length > 0 && (
          <>
            <Tooltip
              content={
                !filter
                  ? 'Type to filter, then click to flip include ↔ exclude'
                  : `${filterMode === 'exclude' ? 'Exclude' : 'Include'} matches — click to flip`
              }
            >
              <button
                type="button"
                className="readout-operator compact-readout-operator"
                onClick={flipCategoricalMode}
                disabled={!filter}
                aria-label={`Filter mode ${filterMode ?? 'include'}`}
              >
                {filterMode === 'exclude' ? '⊖' : '⊕'}
              </button>
            </Tooltip>
            <input
              type="text"
              className="compact-substring-input"
              value={typeof filter?.value === 'string' ? filter.value : ''}
              placeholder="contains…"
              onChange={(e) => setSubstring(e.target.value)}
              aria-label={`${label} substring filter`}
            />
          </>
        )}

        <Tooltip content="Hide column">
          <button
            className="column-remove-btn compact-column-remove-btn"
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
          >
            <svg
              width="10"
              height="10"
              viewBox="0 0 10 10"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M2 2 L8 8 M8 2 L2 8" />
            </svg>
          </button>
        </Tooltip>

        <div
          className="col-resize-handle"
          onMouseDown={(e) => {
            e.stopPropagation();
            onResizeStart(e);
          }}
          onClick={(e) => e.stopPropagation()}
        />
      </div>
    );
  }

  return (
    <div className={headerClasses} style={{ width }}>
      {/* Top row: sortable label on the left, close-X on the right. The
       * label is the click target for sort; the X is the click target for
       * hide-column. They share a flex row so the X no longer floats over
       * the histogram. */}
      <div className="column-header-top">
        <Tooltip content="Click to sort • click again to reverse, again to clear">
          <button
            type="button"
            className="column-header-sort"
            onClick={onSort}
          >
            <span className="column-header-label-text">{label}</span>
            <span className="sort-indicator">
              {sortConfig?.direction === 'asc' && '↑'}
              {sortConfig?.direction === 'desc' && '↓'}
              {sortConfig && totalSorts > 1 && (
                <span className="sort-order">{sortIndex + 1}</span>
              )}
            </span>
          </button>
        </Tooltip>
        <Tooltip content="Hide column">
        <button
          className="column-remove-btn"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
        >
          <svg
            width="10"
            height="10"
            viewBox="0 0 10 10"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M2 2 L8 8 M8 2 L2 8" />
          </svg>
        </button>
        </Tooltip>
      </div>

      {/* Histogram strip — anchored to allProducts for a stable x-range,
       * but the bar heights come from the filtered set so the user sees
       * which slice their filter has selected. Sits directly above the
       * slider with no gap so the relationship between the distribution
       * and the slider thumb is obvious. */}
      {isSliderEligible && (
        <div className="column-header-histogram">
          <DistributionChart
            products={products}
            attribute={attribute.key}
            attributeType={attribute.type}
            allProducts={allProducts}
          />
        </div>
      )}

      {isSliderEligible && rangeInfo && (
        <>
          <div className="column-header-slider">
            <div
              ref={sliderTrackRef}
              className={`filter-slider-track-container${
                isDragging ? ' is-dragging' : ''
              }`}
              role="slider"
              tabIndex={0}
              aria-valuemin={rangeInfo.min}
              aria-valuemax={rangeInfo.max}
              aria-valuenow={filterValue ?? rangeInfo.min}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerEnd}
              onPointerCancel={handlePointerEnd}
              onKeyDown={handleSliderKeyDown}
            >
              <div className="filter-slider-rail" />
              <div
                className="filter-slider-active-region"
                style={{
                  left:
                    operator === '<' || operator === '<=' ? '0%' : `${sliderPercent}%`,
                  right:
                    operator === '<' || operator === '<='
                      ? `${100 - sliderPercent}%`
                      : '0%',
                }}
              />
              <div
                className="filter-slider-thumb"
                style={{ left: `${sliderPercent}%` }}
              />
            </div>
          </div>

          {/* Value box — its own row, full width, big enough that the
           * current threshold reads at a glance. Click to type an exact
           * override. */}
          <div className="column-header-value-row">
            {editingValue ? (
              <input
                ref={sliderInputRef}
                type="number"
                className="readout-value-input"
                value={valueDraft}
                step="any"
                onChange={(e) => setValueDraft(e.target.value)}
                onBlur={commitOverride}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    commitOverride();
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    cancelOverride();
                  }
                }}
                aria-label="Override slider with typed value"
              />
            ) : (
              <Tooltip content="Click to type an exact value">
                <button
                  type="button"
                  className="readout-value"
                  onClick={() => {
                    if (dispCurrent != null) {
                      setValueDraft(
                        intLikeUnit
                          ? String(Math.round(dispCurrent))
                          : String(Number(dispCurrent.toFixed(2))),
                      );
                    } else {
                      setValueDraft('');
                    }
                    setEditingValue(true);
                  }}
                >
                  {dispCurrent != null
                    ? intLikeUnit
                      ? Math.round(dispCurrent).toLocaleString()
                      : dispCurrent.toFixed(1)
                    : 'any'}
                </button>
              </Tooltip>
            )}
          </div>

          {/* Bottom row: operator on the left, unit on the right. The two
           * smallest controls sit at the very bottom of the header so the
           * value box above them gets the full visual weight. */}
          <div className="column-header-bottom">
            <Tooltip content={`Operator ${operator} — click to flip (>= ↔ <)`}>
              <button
                type="button"
                className="readout-operator"
                onClick={cycleOperator}
                aria-label={`Filter operator ${operator}`}
              >
                {operator === '>=' ? '≥' : operator === '<=' ? '≤' : operator}
              </button>
            </Tooltip>
            {dispUnit && (
              <Tooltip content={`Click to switch units (currently ${unitSystem})`}>
                <button
                  type="button"
                  className="readout-unit"
                  onClick={handleUnitClick}
                  aria-label={`Unit ${dispUnit} — click to swap unit system`}
                >
                  {dispUnit}
                </button>
              </Tooltip>
            )}
          </div>
        </>
      )}

      {/* Non-slider columns (string / number / array): replace the
       * histogram + slider stack with a single "filter values" trigger
       * that opens a multi-select popover. The polarity (include vs
       * exclude) lives inside the popover; the trigger here just shows
       * the selected count and inherits the green/red tint from the
       * column-header's has-include/has-exclude class. */}
      {!isSliderEligible && multiSelectOptions.length > 0 && (
        <>
          {/* Top-3 + Other breakdown of the *currently visible* rows for
           * this column — the categorical analogue of the slider-column
           * histogram above. Lets the user eyeball the dominant values
           * before clicking into the multi-select. DistributionChart auto-
           * routes string/array attributes through its categorical path
           * and falls back gracefully when the filtered set is empty. */}
          <DistributionChart
            products={products}
            attribute={attribute.key}
            attributeType={attribute.type}
            allProducts={allProducts}
          />
          <Tooltip
            content={
              multiSelectedCount === 0
                ? 'Click to filter values'
                : `${multiSelectedCount} ${filterMode === 'exclude' ? 'excluded' : 'included'} — click to edit`
            }
          >
          <button
            ref={multiTriggerRef}
            type="button"
            className="column-header-multi-trigger"
            onClick={(e) => {
              e.stopPropagation();
              setMultiOpen(o => !o);
            }}
            aria-haspopup="listbox"
            aria-expanded={multiOpen}
          >
            {multiSelectedCount === 0
              ? 'any'
              : `${multiSelectedCount} ${filterMode === 'exclude' ? 'excluded' : 'selected'}`}
          </button>
          </Tooltip>
          <MultiSelectFilterPopover
            open={multiOpen}
            anchorEl={multiTriggerRef.current}
            options={multiSelectOptions}
            filter={filter}
            attributeLabel={label}
            attributeKey={attribute.key}
            onClose={() => setMultiOpen(false)}
            onChange={(next) => onFilterChange(next)}
          />
        </>
      )}

      <div
        className="col-resize-handle"
        onMouseDown={(e) => {
          e.stopPropagation();
          onResizeStart(e);
        }}
        onClick={(e) => e.stopPropagation()}
      />
    </div>
  );
}
