/**
 * PriceCell + LeadTimeCell — the commercial band's table cells
 * (todo/COMMERCIAL.md Phase 3).
 *
 * Both cells resolve through the pure functions in utils/commercial.ts
 * (listed > estimate > absent; availability badge > listed lead_time >
 * estimate > absent) and render the figure with its ConfidenceChip. The
 * chip / badge is a click target that opens a SourcePopover with the
 * figure's receipts; clicks stop propagation so the row's detail modal
 * doesn't open underneath.
 */

import { useRef, useState } from 'react';
import type { Product } from '../types/models';
import {
  resolvePrice,
  resolveLeadTime,
  type CommercialFields,
} from '../utils/commercial';
import ConfidenceChip from './ui/ConfidenceChip';
import SourcePopover from './ui/SourcePopover';
import './CommercialCell.css';

const asCommercial = (p: Product): CommercialFields => p as unknown as CommercialFields;

export function PriceCell({ product }: { product: Product }) {
  const resolved = resolvePrice(asCommercial(product));
  const chipRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  if (resolved.kind === 'none') {
    return <span className="commercial-cell commercial-cell--absent">—</span>;
  }

  return (
    <span className="commercial-cell">
      <span
        className={
          resolved.kind === 'estimate'
            ? 'commercial-cell-value commercial-cell-value--estimate'
            : 'commercial-cell-value'
        }
      >
        {resolved.text}
      </span>
      <ConfidenceChip
        ref={chipRef}
        confidence={resolved.confidence ?? 'inferred'}
        detail={resolved.detail}
        active={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      />
      <SourcePopover
        open={open}
        anchorEl={chipRef.current}
        heading="Price"
        subheading={resolved.detail}
        citations={resolved.sources}
        onClose={() => setOpen(false)}
      />
    </span>
  );
}

export function LeadTimeCell({ product }: { product: Product }) {
  const resolved = resolveLeadTime(asCommercial(product));
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  if (resolved.kind === 'none') {
    return <span className="commercial-cell commercial-cell--absent">—</span>;
  }

  if (resolved.kind === 'badge') {
    return (
      <span className="commercial-cell">
        <button
          ref={anchorRef}
          type="button"
          className={`availability-badge availability-badge--${resolved.tone}`}
          aria-label={`Availability: ${resolved.text} — view source`}
          aria-expanded={open}
          aria-haspopup="dialog"
          onClick={(e) => {
            e.stopPropagation();
            setOpen((o) => !o);
          }}
        >
          {resolved.text}
        </button>
        <SourcePopover
          open={open}
          anchorEl={anchorRef.current}
          heading="Lead Time"
          subheading="availability snapshot"
          citations={resolved.sources}
          onClose={() => setOpen(false)}
        />
      </span>
    );
  }

  // 'value' (listed lead_time — no chip, no provenance fields exist) or
  // 'estimate' (chip + popover, same treatment as the price estimate).
  return (
    <span className="commercial-cell">
      <span
        className={
          resolved.kind === 'estimate'
            ? 'commercial-cell-value commercial-cell-value--estimate'
            : 'commercial-cell-value'
        }
      >
        {resolved.text}
      </span>
      {resolved.kind === 'estimate' && resolved.confidence && (
        <>
          <ConfidenceChip
            ref={anchorRef}
            confidence={resolved.confidence}
            active={open}
            onClick={(e) => {
              e.stopPropagation();
              setOpen((o) => !o);
            }}
          />
          <SourcePopover
            open={open}
            anchorEl={anchorRef.current}
            heading="Lead Time"
            citations={resolved.sources}
            onClose={() => setOpen(false)}
          />
        </>
      )}
    </span>
  );
}
