/**
 * Filter chip component with comparison operators
 * Minimal design with inline editing, value suggestions, and sliders for numeric fields
 */

import { useState, useRef, useEffect, useMemo } from 'react';
import { FilterCriterion, AttributeMetadata, ComparisonOperator, getAvailableOperators } from '../types/filters';
import { Product } from '../types/models';
import { useApp } from '../context/AppContext';
import {
  toCanonical,
  toDisplay,
  displayUnit,
  isIntegerUnit,
} from '../utils/unitConversion';
import Tooltip from './ui/Tooltip';

interface FilterChipProps {
  filter: FilterCriterion;
  attributeType?: AttributeMetadata['type'];
  products: Product[];
  onUpdate: (updatedFilter: FilterCriterion) => void;
  onRemove: () => void;
  onEditAttribute: (cursor: { x: number; y: number } | null) => void;
  suggestedValues?: Array<string | number>;
  attributeMetadata?: AttributeMetadata;
  allProducts?: Product[];
}

/**
 * Helper function to extract numeric value from nested objects
 */
const getNestedValue = (obj: any, path: string): any => {
  const keys = path.split('.');
  let value = obj;
  for (const key of keys) {
    if (value === undefined || value === null) return undefined;
    value = value[key];
  }
  return value;
};

/**
 * Helper function to extract numeric value from ValueUnit or MinMaxUnit
 */
const extractNumericValue = (value: any): number | null => {
  if (typeof value === 'number') return value;
  if (typeof value === 'object' && value !== null) {
    // ValueUnit: { value: number, unit: string }
    if ('value' in value && typeof value.value === 'number') return value.value;
    // MinMaxUnit: { min: number, max: number, unit: string }
    if ('min' in value && 'max' in value) {
      return (value.min + value.max) / 2;
    }
  }
  return null;
};

/**
 * Get unit string from ValueUnit or MinMaxUnit
 */
const getUnitString = (value: any): string | null => {
  if (typeof value === 'object' && value !== null && 'unit' in value) {
    return value.unit;
  }
  return null;
};

/**
 * Slider scaling — percentile / quantile mapping. The slider has SLIDER_RES+1
 * discrete positions; position p maps to the value at empirical CDF
 * position p/SLIDER_RES of the data. Outliers occupy a slice of the track
 * proportional to their rarity, so a single 10000 in a 0..100 distribution
 * sits in the top 0.5% rather than monopolizing 99% of the slider. Linear
 * range mapping was previously useless on long-tailed catalogs.
 */
const SLIDER_RES = 1000;

const valueToPosition = (value: number, sortedValues: number[]): number => {
  const n = sortedValues.length;
  if (n <= 1) return 0;
  // Binary search for first index whose value is >= the target.
  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sortedValues[mid] < value) lo = mid + 1;
    else hi = mid;
  }
  let idx = lo;
  if (lo > 0 && Math.abs(sortedValues[lo - 1] - value) < Math.abs(sortedValues[lo] - value)) {
    idx = lo - 1;
  }
  return Math.round((idx / (n - 1)) * SLIDER_RES);
};

export default function FilterChip({
  filter,
  attributeType,
  products,
  onUpdate,
  onRemove,
  onEditAttribute,
  suggestedValues = [],
  attributeMetadata,
  allProducts
}: FilterChipProps) {
  const { unitSystem } = useApp();
  const [editValue, setEditValue] = useState(() => {
    if (filter.value !== undefined && !Array.isArray(filter.value)) {
      return String(filter.value);
    }
    return '';
  });
  const [showDropdown, setShowDropdown] = useState(false);
  const [filteredSuggestions, setFilteredSuggestions] = useState(suggestedValues);
  const [localSliderValue, setLocalSliderValue] = useState<number>(0);
  // Slider override: when true, the value readout swaps in a small number
  // input pre-populated with the currently-displayed value. Hidden behind
  // a click on the readout so the slider stays the primary interaction
  // and a stray tap doesn't accidentally land in an editable field.
  const [editingSliderValue, setEditingSliderValue] = useState(false);
  const [sliderValueDraft, setSliderValueDraft] = useState<string>('');
  // Drag state for the rubber-band slider. `dragCursorT` is the raw cursor
  // position along the track (0..1) during an active drag; null otherwise.
  // Used purely for the thumb's visual stress curve — the underlying
  // filter value still snaps to the nearest data point on every move.
  const [isSliderDragging, setIsSliderDragging] = useState(false);
  const [dragCursorT, setDragCursorT] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const sliderValueInputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const sliderTrackRef = useRef<HTMLDivElement>(null);

  // Get available operators based on actual data values
  const availableOperators = useMemo(
    () => getAvailableOperators(products, filter.attribute),
    [products, filter.attribute]
  );

  // Slider/numeric fields cycle just '>=' and '<' — drag-from-zero with a
  // single click to flip into upper-bound mode. The lower bound includes
  // the equality so a row sitting exactly at the seeded percentile floor
  // isn't quietly excluded; the upper bound stays strict.
  const SLIDER_OPERATORS: ComparisonOperator[] = useMemo(() => ['>=', '<'], []);

  // Build slider scale from the actual value distribution. Stores the full
  // sorted array so position→value can map by empirical CDF (see
  // positionToValue). Outliers no longer dominate the track.
  const rangeInfo = useMemo(() => {
    if (attributeType !== 'object' && attributeType !== 'range') {
      return null;
    }

    const values: number[] = [];
    let unit: string | null = null;

    // Use allProducts if available to keep the slider scale stable across filters
    const productsToUse = allProducts || products;

    productsToUse.forEach(product => {
      const value = getNestedValue(product, filter.attribute);
      if (value !== undefined && value !== null) {
        const numValue = extractNumericValue(value);
        if (numValue !== null) {
          values.push(numValue);
          if (!unit) {
            unit = getUnitString(value);
          }
        }
      }
    });

    if (values.length === 0) return null;

    values.sort((a, b) => a - b);

    return {
      min: values[0],
      max: values[values.length - 1],
      sortedValues: values,
      unit: unit || attributeMetadata?.unit || ''
    };
  }, [products, allProducts, filter.attribute, attributeType, attributeMetadata]);

  // Sync local slider value with filter value, defaulting to 0. Starting at
  // zero matches the user mental model: "drag right to raise the floor"
  // with operator '>=' (or "drag right to raise the cap" with '<').
  useEffect(() => {
    if (typeof filter.value === 'number') {
      setLocalSliderValue(filter.value);
    } else {
      setLocalSliderValue(0);
    }
  }, [filter.value, rangeInfo]);

  // Determine if we should show a slider (must be stable)
  const showSlider = useMemo(() => {
    // Show slider for 'object' and 'range' types that have numeric values
    return (attributeType === 'object' || attributeType === 'range') && rangeInfo !== null;
  }, [attributeType, rangeInfo]);

  // Slider value defaults to undefined ("any") so the chip doesn't filter
  // anything until the user grabs the slider. Previously seeded at P10 to
  // exclude the bottom decile, but that confused users who wondered why
  // expected parts weren't showing up; the catalog now starts wide.

  // Determine if this is a multi-select string field
  // String fields have only '=' and '!=' operators (or just '=')
  const isMultiSelectField = useMemo(() => {
    return availableOperators.length <= 2 &&
           availableOperators.every(op => op === '=' || op === '!=');
  }, [availableOperators]);

  // For slider fields: cycle button toggles '>=' ↔ '<'. For other numeric/
  // comparison fields: cycle through whatever operators the data supports,
  // skipping multi-select (which uses the chip-list UI instead).
  // Slider fields don't render the standalone left button — the operator
  // glyph next to the value is itself the cycle button (see below).
  const cycleOperators = showSlider ? SLIDER_OPERATORS : availableOperators;
  const showOperatorButton =
    !showSlider && availableOperators.length > 1 && !isMultiSelectField;

  // Get current selected values (as array) - filter out booleans and tuples
  const selectedValues = useMemo(() => {
    if (!filter.value) return [];
    if (Array.isArray(filter.value)) {
      // Filter to only include string | number, not boolean or nested arrays
      return filter.value.filter((v): v is string | number =>
        typeof v === 'string' || typeof v === 'number'
      );
    }
    // Only include if it's string or number
    if (typeof filter.value === 'string' || typeof filter.value === 'number') {
      return [filter.value];
    }
    return [];
  }, [filter.value]);

  // Cycle through comparison operators. Slider fields toggle '>=' ↔ '<';
  // non-slider numeric fields cycle whatever the data supports.
  // Auto-sort on cycle was removed — flipping an operator no longer
  // re-orders the table. The user controls sort separately via the
  // column header arrows.
  const cycleOperator = () => {
    if (cycleOperators.length <= 1) return;
    const currentIndex = cycleOperators.indexOf(filter.operator || cycleOperators[0]);
    const nextIndex = currentIndex === -1 ? 0 : (currentIndex + 1) % cycleOperators.length;
    const nextOp = cycleOperators[nextIndex];
    onUpdate({ ...filter, operator: nextOp });
  };

  // The user's chosen index in sortedValues, kept alongside the value
  // it represents. Lets the thumb stay where the user dragged it even
  // when the value has many duplicates — otherwise valueToPosition's
  // leftmost-binary-search would snap the thumb back to the first
  // occurrence of the cluster, looking like a "round down" on release.
  const [thumbIdx, setThumbIdx] = useState<{ value: number; idx: number } | null>(null);

  // Slider position (0..SLIDER_RES) for the current value, by percentile rank.
  // Drives both the <input> thumb and the active-region overlay so the slice
  // of the track filled = the slice of products that match.
  const sliderPosition = useMemo(() => {
    if (!rangeInfo) return 0;
    const n = rangeInfo.sortedValues.length;
    if (n <= 1) return 0;
    if (
      thumbIdx &&
      thumbIdx.value === localSliderValue &&
      thumbIdx.idx >= 0 &&
      thumbIdx.idx < n &&
      rangeInfo.sortedValues[thumbIdx.idx] === thumbIdx.value
    ) {
      return Math.round((thumbIdx.idx / (n - 1)) * SLIDER_RES);
    }
    return valueToPosition(localSliderValue, rangeInfo.sortedValues);
  }, [thumbIdx, localSliderValue, rangeInfo]);

  const sliderPercent = (sliderPosition / SLIDER_RES) * 100;

  // Handle slider value change — update local state AND filter results immediately
  const handleSliderChange = (newValue: number) => {
    setLocalSliderValue(newValue);
    onUpdate({
      ...filter,
      value: newValue,
      operator: filter.operator || '>='
    });
  };

  // Rubber-band thumb position. While dragging, the cursor sits at
  // `dragCursorT` (0..1). The underlying value snaps to the nearest data
  // point each frame, but the thumb's *visual* position uses a bell-curve
  // stress: it stretches toward the cursor up to mid-zone, then returns
  // to the snap point as the cursor approaches the boundary. Crossing the
  // boundary commits the next/prev value, the snap anchor jumps, and the
  // bell starts over — so the user sees the thumb stress-and-snap-back
  // for every gap traversal instead of the slider sitting frozen.
  const RUBBER_BAND_AMPLITUDE = 0.65;
  const thumbPercent = useMemo(() => {
    if (!isSliderDragging || dragCursorT === null || !rangeInfo) {
      return sliderPercent;
    }
    const n = rangeInfo.sortedValues.length;
    if (n <= 1) return 0;

    const snapIdx = Math.round(dragCursorT * (n - 1));
    const snapT = snapIdx / (n - 1);
    const zoneHalfWidth = 0.5 / (n - 1);
    const zoneOffset = dragCursorT - snapT; // signed, in [-zoneHalfWidth, zoneHalfWidth]
    const phase = Math.min(1, Math.abs(zoneOffset) / zoneHalfWidth); // 0..1
    // Bell curve: 0 at snap, peaks at mid-zone, back to 0 at boundary.
    const stress = Math.sin(phase * Math.PI);
    const thumbT =
      snapT + Math.sign(zoneOffset) * stress * zoneHalfWidth * RUBBER_BAND_AMPLITUDE;
    return Math.max(0, Math.min(100, thumbT * 100));
  }, [isSliderDragging, dragCursorT, sliderPercent, rangeInfo]);

  // Translate a pointer event to a track-relative t in [0, 1] and commit
  // the snapped value. `dragCursorT` retains the raw cursor for the
  // rubber-band visual; the filter value updates only when the snap
  // index changes, so React doesn't churn on every pixel of mouse motion.
  const updateFromPointer = (clientX: number) => {
    const track = sliderTrackRef.current;
    if (!track || !rangeInfo) return;
    const rect = track.getBoundingClientRect();
    if (rect.width === 0) return;
    const t = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    setDragCursorT(t);
    const n = rangeInfo.sortedValues.length;
    if (n <= 1) return;
    const idx = Math.round(t * (n - 1));
    const newValue = rangeInfo.sortedValues[idx];
    setThumbIdx({ value: newValue, idx });
    if (newValue !== localSliderValue) {
      handleSliderChange(newValue);
    }
  };

  const handleSliderPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!rangeInfo) return;
    e.preventDefault();
    e.stopPropagation();
    sliderTrackRef.current?.setPointerCapture(e.pointerId);
    setIsSliderDragging(true);
    updateFromPointer(e.clientX);
  };

  const handleSliderPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isSliderDragging) return;
    updateFromPointer(e.clientX);
  };

  const handleSliderPointerEnd = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isSliderDragging) return;
    sliderTrackRef.current?.releasePointerCapture(e.pointerId);
    setIsSliderDragging(false);
    setDragCursorT(null);
  };

  // Keyboard nav — replaces the input[range] arrow-key affordance.
  const handleSliderKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!rangeInfo) return;
    const n = rangeInfo.sortedValues.length;
    if (n <= 1) return;
    const currentIdx = rangeInfo.sortedValues.indexOf(localSliderValue);
    const idx = currentIdx === -1 ? 0 : currentIdx;
    let next = idx;
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
    setThumbIdx({ value: rangeInfo.sortedValues[next], idx: next });
    handleSliderChange(rangeInfo.sortedValues[next]);
  };

  // Commit a typed slider override. Input is in the active *display*
  // system (imperial when toggled), so canonicalize before storing —
  // matches the text-input path. Out-of-range values are accepted as-is;
  // the slider thumb visually clamps but the underlying filter value
  // stays at the user's chosen number.
  const commitSliderOverride = () => {
    if (!rangeInfo) {
      setEditingSliderValue(false);
      return;
    }
    const trimmed = sliderValueDraft.trim();
    if (trimmed === '') {
      setEditingSliderValue(false);
      return;
    }
    const parsed = parseFloat(trimmed);
    if (Number.isNaN(parsed)) {
      setEditingSliderValue(false);
      return;
    }
    const canonical = rangeInfo.unit
      ? toCanonical(parsed, rangeInfo.unit, unitSystem)
      : parsed;
    handleSliderChange(canonical);
    setEditingSliderValue(false);
  };

  const cancelSliderOverride = () => {
    setEditingSliderValue(false);
    setSliderValueDraft('');
  };

  // Filter state stays canonical metric. When the user types a number
  // into a unit-bearing field with imperial display active, convert
  // their imperial input back to canonical before storing.
  const canonicalizeInput = (n: number): number => {
    if (attributeMetadata?.unit) {
      return toCanonical(n, attributeMetadata.unit, unitSystem);
    }
    return n;
  };

  // Update filter value on every keystroke for real-time filtering
  const handleValueChange = (newValue: string) => {
    setEditValue(newValue);

    // Filter suggestions based on input
    if (newValue.trim()) {
      let filtered = suggestedValues.filter(val =>
        String(val).toLowerCase().includes(newValue.toLowerCase())
      );

      // For multi-select, also filter out already selected values
      if (isMultiSelectField) {
        filtered = filtered.filter(val =>
          !selectedValues.map(v => String(v)).includes(String(val))
        );
      }

      setFilteredSuggestions(filtered);
      setShowDropdown(filtered.length > 0);

      // For multi-select fields, don't update immediately - wait for selection
      if (!isMultiSelectField) {
        // Try to parse as number
        const numValue = parseFloat(newValue);
        const finalValue = !isNaN(numValue) ? canonicalizeInput(numValue) : newValue.trim();
        onUpdate({ ...filter, value: finalValue });
      }
    } else {
      if (isMultiSelectField) {
        // Show available suggestions (minus already selected)
        const availableSuggestions = suggestedValues.filter(val =>
          !selectedValues.map(v => String(v)).includes(String(val))
        );
        setFilteredSuggestions(availableSuggestions);
        setShowDropdown(false);
      } else {
        setFilteredSuggestions(suggestedValues);
        setShowDropdown(false);
        onUpdate({ ...filter, value: undefined });
      }
    }
  };

  // Handle suggestion selection
  const handleSelectSuggestion = (value: string | number) => {
    if (isMultiSelectField) {
      // Add to array of selected values. After picking, keep the dropdown
      // open and refocus the input so the user can pile on more values
      // without re-clicking — this is the whole point of multi-select.
      const currentValues = selectedValues.map(v => String(v));
      if (!currentValues.includes(String(value))) {
        const newValues = [...currentValues, String(value)];
        onUpdate({ ...filter, value: newValues.length === 1 ? newValues[0] : newValues });
      }
      setEditValue('');
      const remaining = suggestedValues.filter(val =>
        !currentValues.includes(String(val)) && String(val) !== String(value)
      );
      setFilteredSuggestions(remaining);
      setShowDropdown(remaining.length > 0);
      inputRef.current?.focus();
    } else {
      // Single value - replace existing
      setEditValue(String(value));
      const numValue = parseFloat(String(value));
      // Suggestions come from canonical-metric records, so don't
      // re-canonicalize numeric suggestions even when imperial is active.
      const finalValue = !isNaN(numValue) ? numValue : value;
      onUpdate({ ...filter, value: finalValue });
      setShowDropdown(false);
    }
  };

  // Multi-select: commit whatever's typed (free-form). Lets the user add a
  // part number that isn't in the suggestion list — common, since the list
  // is capped and the catalog has hundreds of unique part numbers.
  const handleCommitTyped = () => {
    if (!isMultiSelectField) return;
    const trimmed = editValue.trim();
    if (!trimmed) return;
    handleSelectSuggestion(trimmed);
  };

  // Remove a value from multi-select
  const handleRemoveValue = (valueToRemove: string | number) => {
    const currentValues = selectedValues.map(v => String(v));
    const newValues = currentValues.filter(v => v !== String(valueToRemove));

    if (newValues.length === 0) {
      onUpdate({ ...filter, value: undefined });
    } else if (newValues.length === 1) {
      onUpdate({ ...filter, value: newValues[0] });
    } else {
      onUpdate({ ...filter, value: newValues });
    }
  };

  // Show dropdown when input is focused
  const handleFocus = () => {
    if (suggestedValues.length > 0) {
      // For multi-select, filter out already selected values
      if (isMultiSelectField) {
        const availableSuggestions = suggestedValues.filter(val =>
          !selectedValues.map(v => String(v)).includes(String(val))
        );
        setFilteredSuggestions(availableSuggestions);
        setShowDropdown(availableSuggestions.length > 0);
      } else {
        setFilteredSuggestions(suggestedValues);
        setShowDropdown(true);
      }
    }
  };

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node) &&
        inputRef.current &&
        !inputRef.current.contains(e.target as Node)
      ) {
        setShowDropdown(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div className={`filter-chip-minimal${filter.mode === 'exclude' ? ' filter-chip-exclude-mode' : ''}`} data-operator={filter.operator || '='}>
      <div className="filter-chip-header">
        <Tooltip content={filter.mode === 'exclude' ? 'Excluding — click to include' : 'Including — click to exclude'}>
          <button
            className={`filter-mode-toggle ${filter.mode === 'exclude' ? 'filter-mode-exclude' : 'filter-mode-include'}`}
            onClick={() => onUpdate({ ...filter, mode: filter.mode === 'exclude' ? 'include' : 'exclude' })}
            aria-label={filter.mode === 'exclude' ? 'Excluding these values — click to include instead' : 'Including these values — click to exclude instead'}
          >
            {filter.mode === 'exclude' ? '≠' : '='}
          </button>
        </Tooltip>
        <Tooltip content="Click to change spec">
          <span
            className="filter-attribute"
            onClick={(e) => onEditAttribute({ x: e.clientX, y: e.clientY })}
            style={{ cursor: 'pointer' }}
          >
            {filter.displayName}
          </span>
        </Tooltip>
        <Tooltip content="Remove spec">
          <button
            className="filter-remove"
            onClick={onRemove}
            aria-label="Remove spec"
          >
            ×
          </button>
        </Tooltip>
      </div>

      {/* Show selected values for multi-select fields (hide if slider is shown to avoid redundancy/glitch) */}
      {isMultiSelectField && selectedValues.length > 0 && !showSlider && (
        <div style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '0.25rem',
          marginBottom: '0.3rem',
          padding: '0.2rem 0'
        }}>
          {selectedValues.map((val, idx) => (
            <span
              key={idx}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.3rem',
                padding: '0.15rem 0.35rem',
                background: filter.mode === 'exclude'
                  ? 'var(--danger)'
                  : 'var(--accent-primary)',
                color: 'var(--bg-primary)',
                borderRadius: '3px',
                fontSize: '0.85rem',
                fontWeight: 600
              }}
            >
              {String(val)}
              <Tooltip content="Remove value">
                <button
                  onClick={() => handleRemoveValue(val)}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'white',
                    cursor: 'pointer',
                    padding: 0,
                    fontSize: '0.9rem',
                    lineHeight: 1,
                    opacity: 0.8
                  }}
                >
                  ×
                </button>
              </Tooltip>
            </span>
          ))}
        </div>
      )}

      <div className="filter-chip-controls">
        {/* Operator cycle button. Sliders toggle '>=' ↔ '<';
            other numeric fields walk whatever the data supports. */}
        {showOperatorButton && (
          <Tooltip content={`Click to cycle operator: ${cycleOperators.join(' → ')}`}>
            <button
              className="filter-operator"
              data-operator={filter.operator || cycleOperators[0] || '='}
              onClick={cycleOperator}
            >
              {filter.operator || cycleOperators[0] || '='}
            </button>
          </Tooltip>
        )}



        {/* Render slider for numeric ValueUnit/MinMaxUnit fields. The
            slider operates in *canonical* metric (filter state stays
            metric), while the labels and current-value readout convert
            to the active display system. This keeps comparators and
            sort logic untouched. */}
        {showSlider && rangeInfo ? (() => {
          const dispMin = toDisplay(rangeInfo.min, rangeInfo.unit, unitSystem);
          const dispMax = toDisplay(rangeInfo.max, rangeInfo.unit, unitSystem);
          const dispCurrent = toDisplay(localSliderValue, rangeInfo.unit, unitSystem);
          const dispUnit = displayUnit(rangeInfo.unit, unitSystem);
          return (
            <div className="filter-slider-wrapper">
              <div className="filter-slider-range-labels">
                <span>{dispMin}</span>
                <span>{dispMax}</span>
              </div>
              <div
                ref={sliderTrackRef}
                className={`filter-slider-track-container${isSliderDragging ? ' is-dragging' : ''}`}
                role="slider"
                tabIndex={0}
                aria-valuemin={rangeInfo.min}
                aria-valuemax={rangeInfo.max}
                aria-valuenow={localSliderValue}
                onPointerDown={handleSliderPointerDown}
                onPointerMove={handleSliderPointerMove}
                onPointerUp={handleSliderPointerEnd}
                onPointerCancel={handleSliderPointerEnd}
                onKeyDown={handleSliderKeyDown}
              >
                <div className="filter-slider-rail" />
                <div
                  className="filter-slider-active-region"
                  style={{
                    left: (filter.operator === '<' || filter.operator === '<=')
                      ? '0%'
                      : `${sliderPercent}%`,
                    right: (filter.operator === '<' || filter.operator === '<=')
                      ? `${100 - sliderPercent}%`
                      : '0%',
                  }}
                />
                <div
                  className="filter-slider-thumb"
                  style={{ left: `${thumbPercent}%` }}
                />
              </div>
              <div className="filter-slider-value">
                <Tooltip content={`Click to cycle operator: ${cycleOperators.join(' → ')}`}>
                  <button
                    type="button"
                    className="filter-slider-operator"
                    data-operator={filter.operator || '>='}
                    onClick={cycleOperator}
                    aria-label={`Operator ${filter.operator || '>='} — click to cycle`}
                  >
                    {filter.operator || '>='}
                  </button>
                </Tooltip>
                {' '}
                {editingSliderValue ? (
                  <span className="filter-slider-value-edit">
                    <input
                      ref={sliderValueInputRef}
                      type="number"
                      className="filter-slider-value-input"
                      value={sliderValueDraft}
                      step="any"
                      autoFocus
                      onChange={(e) => setSliderValueDraft(e.target.value)}
                      onBlur={commitSliderOverride}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          commitSliderOverride();
                        } else if (e.key === 'Escape') {
                          e.preventDefault();
                          cancelSliderOverride();
                        }
                      }}
                      onClick={(e) => e.stopPropagation()}
                      onMouseDown={(e) => e.stopPropagation()}
                      aria-label="Override slider with typed value"
                    />
                    {dispUnit && <span className="filter-slider-value-unit">{dispUnit}</span>}
                  </span>
                ) : (
                  <Tooltip content="Click to type an exact value">
                    <button
                      type="button"
                      className="filter-slider-value-readout"
                      aria-label="Click to type an exact value"
                      onClick={() => {
                        // Integer units (rpm, V) seed the override draft as
                        // a whole number; fractional units keep two decimals
                        // so the user sees what they're editing.
                        const draft = isIntegerUnit(rangeInfo.unit)
                          ? String(Math.round(dispCurrent))
                          : String(Number(dispCurrent.toFixed(2)));
                        setSliderValueDraft(draft);
                        setEditingSliderValue(true);
                      }}
                    >
                      <span>
                        {isIntegerUnit(rangeInfo.unit)
                          ? Math.round(dispCurrent)
                          : dispCurrent.toFixed(1)} {dispUnit}
                      </span>
                      <span className="filter-slider-value-edit-hint" aria-hidden="true">✎</span>
                    </button>
                  </Tooltip>
                )}
              </div>
            </div>
          );
        })() : (
          // Render text input for non-slider fields
          <div className="filter-input-wrapper" ref={wrapperRef}>
            <input
              ref={inputRef}
              type="text"
              className="filter-input"
              value={editValue}
              onChange={(e) => handleValueChange(e.target.value)}
              onFocus={handleFocus}
              onKeyDown={(e) => {
                if (!isMultiSelectField) return;
                if (e.key === 'Enter') {
                  e.preventDefault();
                  // Prefer the highlighted suggestion if there's exactly one
                  // good match; otherwise commit the typed text as-is so the
                  // user isn't trapped when their value isn't in the list.
                  if (filteredSuggestions.length === 1 && editValue.trim()) {
                    handleSelectSuggestion(filteredSuggestions[0]);
                  } else {
                    handleCommitTyped();
                  }
                } else if (e.key === 'Backspace' && editValue === '' && selectedValues.length > 0) {
                  // Empty input + Backspace = remove last pill. Standard
                  // tag-input convention; saves a trip to the × button.
                  e.preventDefault();
                  handleRemoveValue(selectedValues[selectedValues.length - 1]);
                }
              }}
              placeholder={
                isMultiSelectField
                  ? selectedValues.length > 0
                    ? 'add another (Enter to commit)…'
                    : 'type or pick — Enter to add'
                  : 'value…'
              }
              autoFocus={!filter.value}
            />

            {showDropdown && filteredSuggestions.length > 0 && (() => {
              const rect = wrapperRef.current?.getBoundingClientRect();
              const style: React.CSSProperties = rect ? {
                position: 'fixed',
                top: rect.bottom + 4,
                left: rect.left,
                width: rect.width,
              } : {};
              // Multi-select cataloged values can run into the hundreds (part
              // numbers, series codes); show more before truncating so users
              // don't lose visible options once they start typing.
              const displayCap = isMultiSelectField ? 30 : 10;
              return (
                <div ref={dropdownRef} className="filter-dropdown" style={style}>
                  {filteredSuggestions.slice(0, displayCap).map((value, index) => (
                    <div
                      key={index}
                      className="filter-dropdown-item"
                      // mousedown (not click) — fires before the input's blur,
                      // so the dropdown doesn't close-then-cancel mid-pick.
                      onMouseDown={(e) => {
                        e.preventDefault();
                        handleSelectSuggestion(value);
                      }}
                    >
                      {String(value)}
                    </div>
                  ))}
                  {filteredSuggestions.length > displayCap && (
                    <div className="filter-dropdown-item filter-dropdown-more" aria-hidden="true">
                      …{filteredSuggestions.length - displayCap} more — keep typing to narrow
                    </div>
                  )}
                </div>
              );
            })()}
          </div>
        )}
      </div>
    </div>
  );
}
