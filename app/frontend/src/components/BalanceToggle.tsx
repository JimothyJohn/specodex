/**
 * BalanceToggle — segmented Commercial / Balanced / Technical control
 * (todo/COMMERCIAL.md Phase 3).
 *
 * Sits in the header options band next to DensityToggle and follows the
 * same pattern: state lives in AppContext, persists to localStorage
 * ('specodex.columnBalance' via safeLoadString + validator), and every
 * balance-aware surface (column ordering, default sort) reads the same
 * value.
 */

import { useApp } from '../context/AppContext';
import type { ColumnBalance } from '../types/columnOrder';
import Tooltip from './ui/Tooltip';
import './BalanceToggle.css';

const SEGMENTS: Array<{ value: ColumnBalance; label: string; tooltip: string }> = [
  {
    value: 'commercial',
    label: 'COMM',
    tooltip: 'Commercial first — price, lead time, warranty lead; sorted by price',
  },
  {
    value: 'balanced',
    label: 'BAL',
    tooltip: 'Balanced — commercial band beside the headline specs (default)',
  },
  {
    value: 'technical',
    label: 'TECH',
    tooltip: 'Technical first — specs lead, commercial band trails',
  },
];

export default function BalanceToggle() {
  const { columnBalance, setColumnBalance } = useApp();

  return (
    <div
      className="balance-toggle"
      role="radiogroup"
      aria-label="Column balance: commercial vs technical"
    >
      {SEGMENTS.map((seg) => (
        <Tooltip key={seg.value} content={seg.tooltip} placement="bottom">
          <button
            type="button"
            role="radio"
            aria-checked={columnBalance === seg.value}
            aria-label={seg.tooltip}
            className={`balance-toggle-segment${
              columnBalance === seg.value ? ' is-active' : ''
            }`}
            onClick={() => setColumnBalance(seg.value)}
          >
            {seg.label}
          </button>
        </Tooltip>
      ))}
    </div>
  );
}
