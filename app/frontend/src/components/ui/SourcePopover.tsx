/**
 * SourcePopover — the receipts behind a commercial figure
 * (todo/COMMERCIAL.md Phase 3, ground rule 2: every figure carries its
 * citations).
 *
 * Anchored to a ConfidenceChip (or a stocked badge); lists each citation
 * with its kind label, publisher hostname, retrieval date, verbatim
 * excerpt (quoted), and an external link via the ExternalLink primitive.
 * db_inference citations render "Estimated from N comparable products
 * (method X)" plus the comparable count — there is no per-product route
 * to link the ids to, so the count is the honest rendering.
 *
 * App-native: portaled like MultiSelectFilterPopover, outside-click and
 * Escape dismissal, themed scrollbar via `.scrollable`.
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { DisplayCitation } from '../../utils/commercial';
import { hostnameOf, describeInference } from '../../utils/commercial';
import ExternalLink from './ExternalLink';
import './SourcePopover.css';

export interface SourcePopoverProps {
  open: boolean;
  /** Anchor element — popover positions itself just below this. */
  anchorEl: HTMLElement | null;
  /** Header, e.g. 'Price' or 'Lead Time'. */
  heading: string;
  /** Optional subheading, e.g. 'listed — catalog extraction'. */
  subheading?: string | null;
  citations: DisplayCitation[];
  onClose: () => void;
}

const POPOVER_W = 280;
const POPOVER_GAP = 4;
const POPOVER_PAD = 8;

/** ISO timestamp → date-only display; deterministic (no locale time). */
const formatRetrievedAt = (iso: string | null): string | null => {
  if (!iso) return null;
  const datePart = iso.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(datePart) ? datePart : iso;
};

export default function SourcePopover({
  open,
  anchorEl,
  heading,
  subheading,
  citations,
  onClose,
}: SourcePopoverProps) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<{ top: number; left: number; maxHeight: number } | null>(null);

  useLayoutEffect(() => {
    if (!open || !anchorEl) return;
    const compute = () => {
      const r = anchorEl.getBoundingClientRect();
      const viewportH = window.innerHeight;
      const spaceBelow = viewportH - r.bottom - POPOVER_PAD;
      const top = r.bottom + POPOVER_GAP;
      const idealLeft = r.left + r.width / 2 - POPOVER_W / 2;
      const left = Math.max(
        POPOVER_PAD,
        Math.min(window.innerWidth - POPOVER_W - POPOVER_PAD, idealLeft),
      );
      setRect({ top, left, maxHeight: Math.max(160, Math.min(400, spaceBelow)) });
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

  const popover = (
    <div
      ref={popoverRef}
      className="source-popover"
      role="dialog"
      aria-label={`Sources for ${heading}`}
      onClick={(e) => e.stopPropagation()}
      style={{
        position: 'fixed',
        top: rect.top,
        left: rect.left,
        width: POPOVER_W,
        maxHeight: rect.maxHeight,
      }}
    >
      <div className="source-popover-header">
        <span className="source-popover-heading">{heading}</span>
        {subheading ? <span className="source-popover-subheading">{subheading}</span> : null}
      </div>
      <ul className="source-popover-list scrollable">
        {citations.length === 0 && (
          <li className="source-popover-empty">No sources recorded</li>
        )}
        {citations.map((c, i) => {
          const host = hostnameOf(c.url);
          const date = formatRetrievedAt(c.retrievedAt);
          return (
            <li key={i} className="source-popover-item">
              <div className="source-popover-item-head">
                <span className="source-popover-kind">{c.kindLabel}</span>
                {host && <span className="source-popover-host">{host}</span>}
                {date && <span className="source-popover-date">{date}</span>}
              </div>
              {c.inference && (
                <p className="source-popover-inference">
                  {describeInference(c.inference.count, c.inference.method)}
                  <span className="source-popover-inference-count">
                    {' '}
                    · {c.inference.count} comparable{c.inference.count === 1 ? '' : 's'}
                  </span>
                </p>
              )}
              {c.excerpt && (
                <blockquote className="source-popover-excerpt">
                  &ldquo;{c.excerpt}&rdquo;
                </blockquote>
              )}
              {c.url && (
                <ExternalLink
                  href={c.url}
                  className="source-popover-link"
                  tooltip={`Open ${host ?? 'source'} in a new tab`}
                >
                  View source
                </ExternalLink>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );

  return createPortal(popover, document.body);
}
