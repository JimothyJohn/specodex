/**
 * ConfidenceChip — the confidence tier of a commercial figure, as a small
 * chip that fits inside a table cell (todo/COMMERCIAL.md Phase 3).
 *
 * Every tier gets a distinct muted style; `inferred` is deliberately the
 * visually weakest (dashed border, tertiary text) and `listed` the
 * strongest, so an estimate can never be mistaken for a listed price at
 * a glance. Clickable — the parent anchors a SourcePopover on it so the
 * figure's receipts are one click away.
 */

import type { MouseEventHandler, Ref } from 'react';
import type { Confidence } from '../../utils/commercial';
import './ConfidenceChip.css';

const TIER_TEXT: Record<Confidence, string> = {
  listed: 'LISTED',
  high: 'HIGH',
  medium: 'MED',
  low: 'LOW',
  inferred: 'EST',
};

export interface ConfidenceChipProps {
  confidence: Confidence;
  /**
   * Optional long-form description for assistive tech and the popover
   * subtitle (e.g. 'listed — catalog extraction'). The visible chip text
   * stays the compact tier so it fits in a cell.
   */
  detail?: string | null;
  onClick?: MouseEventHandler<HTMLButtonElement>;
  /** Anchor ref for the SourcePopover. */
  ref?: Ref<HTMLButtonElement>;
  /** True while the popover it anchors is open. */
  active?: boolean;
}

export default function ConfidenceChip({
  confidence,
  detail,
  onClick,
  ref,
  active = false,
}: ConfidenceChipProps) {
  const label = detail ?? `Confidence: ${confidence}`;
  return (
    <button
      ref={ref}
      type="button"
      className={`confidence-chip confidence-chip--${confidence}${active ? ' is-active' : ''}`}
      onClick={onClick}
      aria-label={`${label} — view sources`}
      aria-expanded={active}
      aria-haspopup="dialog"
    >
      {TIER_TEXT[confidence]}
    </button>
  );
}
