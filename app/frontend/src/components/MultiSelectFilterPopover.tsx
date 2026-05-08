/**
 * MultiSelectFilterPopover — portaled dropdown that lets the user pick
 * one or more values for a non-numeric column (string, number, array
 * type) and choose whether to *include* the selection or *exclude* it.
 *
 * Mode is per-filter (not per-value): the whole selection is either an
 * inclusion list or an exclusion list. The two-button mode pill at the
 * top toggles between them and rewrites the FilterCriterion's `mode`
 * field. The chip in the column header reflects this with a subtle
 * green (include) or red (exclude) tint — the popover is the only place
 * the actual selected values live, so the header stays compact.
 *
 * Why portaled: the column-header cell has overflow:hidden (resize
 * handle, narrow widths), so an inline popover would clip. createPortal
 * to document.body sidesteps every overflow ancestor.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { FilterCriterion, FilterValue } from '../types/filters';
import Tooltip from './ui/Tooltip';

interface MultiSelectFilterPopoverProps {
  open: boolean;
  /** Anchor element — popover positions itself just below this. */
  anchorEl: HTMLElement | null;
  /** All unique values for this attribute (computed by the caller). */
  options: Array<string | number>;
  /** Current filter for this attribute (or null if none). */
  filter: FilterCriterion | null;
  /** Display name shown in the popover header. */
  attributeLabel: string;
  /** Attribute key (used when constructing a fresh FilterCriterion). */
  attributeKey: string;
  onClose: () => void;
  /** Called with the new FilterCriterion (or null to remove). */
  onChange: (filter: FilterCriterion | null) => void;
}

const POPOVER_W = 240;
const POPOVER_GAP = 4;
const POPOVER_PAD = 8;

// Coerce filter.value (which can be scalar or array) into a Set of
// the canonical string forms used in the popover's value list. The Set
// is what we membership-check against for the row's selected state.
const filterValuesToSet = (filter: FilterCriterion | null): Set<string> => {
  if (!filter) return new Set();
  const v = filter.value;
  if (v == null) return new Set();
  if (Array.isArray(v)) {
    return new Set(v.filter((x): x is string | number =>
      typeof x === 'string' || typeof x === 'number',
    ).map(String));
  }
  if (typeof v === 'string' || typeof v === 'number') {
    return new Set([String(v)]);
  }
  return new Set();
};

export default function MultiSelectFilterPopover({
  open,
  anchorEl,
  options,
  filter,
  attributeLabel,
  attributeKey,
  onClose,
  onChange,
}: MultiSelectFilterPopoverProps) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<{ top: number; left: number; maxHeight: number } | null>(null);

  // Mode = 'include' (green, default) or 'exclude' (red). Pulled from
  // the existing filter, defaults to 'include' for fresh selections.
  const mode: 'include' | 'exclude' = filter?.mode === 'exclude' ? 'exclude' : 'include';
  const selected = useMemo(() => filterValuesToSet(filter), [filter]);

  useLayoutEffect(() => {
    if (!open || !anchorEl) return;
    const compute = () => {
      const r = anchorEl.getBoundingClientRect();
      const viewportH = window.innerHeight;
      const spaceBelow = viewportH - r.bottom - POPOVER_PAD;
      const top = r.bottom + POPOVER_GAP;
      // Center horizontally over the anchor, but clamp inside viewport.
      const idealLeft = r.left + r.width / 2 - POPOVER_W / 2;
      const left = Math.max(POPOVER_PAD, Math.min(window.innerWidth - POPOVER_W - POPOVER_PAD, idealLeft));
      setRect({ top, left, maxHeight: Math.max(180, Math.min(360, spaceBelow)) });
    };
    compute();
    window.addEventListener('resize', compute);
    window.addEventListener('scroll', compute, true);
    return () => {
      window.removeEventListener('resize', compute);
      window.removeEventListener('scroll', compute, true);
    };
  }, [open, anchorEl]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popoverRef.current?.contains(t)) return;
      if (anchorEl?.contains(t)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onClose, anchorEl]);

  if (!open || !rect) return null;

  // Build a new FilterCriterion from a values array + the active mode.
  // null result means "no filter" (the parent should remove this entry).
  // FilterValue's array variants are homogeneous (string[] OR number[],
  // not (string|number)[]); each attribute is one or the other in
  // practice, so we partition by the first element's type and narrow.
  const buildFilter = (values: Array<string | number>, nextMode: 'include' | 'exclude'): FilterCriterion | null => {
    if (values.length === 0) return null;
    let value: FilterValue;
    if (values.length === 1) {
      value = values[0];
    } else if (typeof values[0] === 'number') {
      value = values.filter((v): v is number => typeof v === 'number');
    } else {
      value = values.map(String);
    }
    return {
      attribute: attributeKey,
      mode: nextMode,
      operator: '=',
      value,
      displayName: filter?.displayName ?? attributeLabel,
    };
  };

  const toggleValue = (v: string | number) => {
    const key = String(v);
    const nextSet = new Set(selected);
    if (nextSet.has(key)) nextSet.delete(key);
    else nextSet.add(key);
    // Coerce back to original types — match against options to recover
    // numbers vs string identity (Set membership erased it).
    const restored = options.filter(o => nextSet.has(String(o)));
    onChange(buildFilter(restored, mode));
  };

  const setMode = (next: 'include' | 'exclude') => {
    const restored = options.filter(o => selected.has(String(o)));
    onChange(buildFilter(restored, next));
  };

  const clearAll = () => {
    onChange(null);
  };

  const popover = (
    <div
      ref={popoverRef}
      className={`multi-filter-popover multi-filter-popover--${mode}`}
      role="dialog"
      aria-label={`Filter ${attributeLabel}`}
      style={{
        position: 'fixed',
        top: rect.top,
        left: rect.left,
        width: POPOVER_W,
        maxHeight: rect.maxHeight,
      }}
    >
      <div className="multi-filter-popover-header">
        <Tooltip content={attributeLabel}>
          <span className="multi-filter-popover-title" tabIndex={0}>
            {attributeLabel}
          </span>
        </Tooltip>
        <div className="multi-filter-popover-mode" role="radiogroup" aria-label="Filter mode">
          <Tooltip content="Include selected values">
            <button
              type="button"
              role="radio"
              aria-checked={mode === 'include'}
              className={`multi-filter-popover-mode-btn multi-filter-popover-mode-btn--include${mode === 'include' ? ' is-active' : ''}`}
              onClick={() => setMode('include')}
            >
              +
            </button>
          </Tooltip>
          <Tooltip content="Exclude selected values">
            <button
              type="button"
              role="radio"
              aria-checked={mode === 'exclude'}
              className={`multi-filter-popover-mode-btn multi-filter-popover-mode-btn--exclude${mode === 'exclude' ? ' is-active' : ''}`}
              onClick={() => setMode('exclude')}
            >
              −
            </button>
          </Tooltip>
        </div>
      </div>
      <ul className="multi-filter-popover-list" role="listbox" aria-multiselectable="true">
        {options.length === 0 && (
          <li className="multi-filter-popover-empty">No values</li>
        )}
        {options.map((opt) => {
          const key = String(opt);
          const isSelected = selected.has(key);
          return (
            <li
              key={key}
              role="option"
              aria-selected={isSelected}
              className={`multi-filter-popover-item${isSelected ? ' is-selected' : ''}`}
              onClick={() => toggleValue(opt)}
            >
              <span className="multi-filter-popover-item-mark" aria-hidden="true">
                {isSelected ? (mode === 'include' ? '✓' : '✕') : ''}
              </span>
              <span className="multi-filter-popover-item-label">{String(opt)}</span>
            </li>
          );
        })}
      </ul>
      {selected.size > 0 && (
        <div className="multi-filter-popover-footer">
          <button
            type="button"
            className="multi-filter-popover-clear"
            onClick={clearAll}
          >
            Clear ({selected.size})
          </button>
        </div>
      )}
    </div>
  );

  return createPortal(popover, document.body);
}
