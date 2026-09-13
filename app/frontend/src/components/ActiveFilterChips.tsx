/**
 * ActiveFilterChips — one chip per filter that actually constrains rows,
 * rendered in the catalog toolbar (UI_CLEANUP Phase 2, popover-per-
 * column). With the slider / value / operator controls folded into a
 * per-column popover, this row is the at-a-glance record of what's
 * applied — including columns scrolled off-screen or hidden — and the
 * one-click way to drop a single constraint.
 *
 * Seeded default chips are valueless and never appear here; "Clear"
 * next door still drops everything.
 */

import { useMemo } from 'react';
import type { FilterCriterion, AttributeMetadata } from '../types/filters';
import type { Product } from '../types/models';
import type { UnitSystem } from '../utils/unitConversion';
import { filterHasValue, summarizeFilter, sniffAttributeUnit } from '../utils/filterSummary';
import Tooltip from './ui/Tooltip';
import './ActiveFilterChips.css';

interface ActiveFilterChipsProps {
  filters: FilterCriterion[];
  attributes: AttributeMetadata[];
  /** Rows to sniff a unit from when the metadata carries none. */
  products: Product[];
  unitSystemFor: (key: string) => UnitSystem;
  onRemove: (filter: FilterCriterion) => void;
}

export default function ActiveFilterChips({
  filters,
  attributes,
  products,
  unitSystemFor,
  onRemove,
}: ActiveFilterChipsProps) {
  const active = useMemo(() => filters.filter(filterHasValue), [filters]);
  if (active.length === 0) return null;

  return (
    <div className="active-filter-chips" role="list" aria-label="Active filters">
      {active.map((f) => {
        const baseKey = f.attribute.split('.')[0];
        const meta = attributes.find((a) => a.key === baseKey);
        const unit = meta?.unit ?? sniffAttributeUnit(products, baseKey);
        const label = meta?.displayName ?? f.displayName ?? baseKey;
        const summary = summarizeFilter(f, { unit, unitSystem: unitSystemFor(baseKey) });
        return (
          <span
            key={`${f.attribute}:${f.mode}`}
            className={`active-filter-chip${f.mode === 'exclude' ? ' active-filter-chip--exclude' : ''}`}
            role="listitem"
          >
            <span className="active-filter-chip-label">{label}</span>
            <span className="active-filter-chip-value">{summary}</span>
            <Tooltip content={`Remove ${label} filter`}>
              <button
                type="button"
                className="active-filter-chip-remove"
                aria-label={`Remove ${label} filter`}
                onClick={() => onRemove(f)}
              >
                <svg width="9" height="9" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
                  <path d="M2 2 L8 8 M8 2 L2 8" />
                </svg>
              </button>
            </Tooltip>
          </span>
        );
      })}
    </div>
  );
}
