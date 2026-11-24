/**
 * AnchoredPopover — the app-native floating panel primitive.
 *
 * Portaled to document.body (so an overflow:hidden / sticky ancestor
 * can't clip it), positioned just below its anchor and clamped inside
 * the viewport, re-measured on scroll and resize, dismissed by an
 * outside pointer-down or Escape. The anchor itself is exempt from the
 * outside-click rule so a toggle button doesn't close-then-reopen.
 *
 * Extracted from the positioning code MultiSelectFilterPopover and
 * SourcePopover each carried; the column-header filter popover
 * (UI_CLEANUP Phase 2) is the first consumer. Keep this dumb: it owns
 * placement and dismissal, nothing about what's inside.
 */

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import './AnchoredPopover.css';

export interface AnchoredPopoverProps {
  open: boolean;
  /** Element the panel hangs under. Null while the anchor hasn't mounted. */
  anchorEl: HTMLElement | null;
  /** Panel width in px. Height is content-driven, capped to the viewport. */
  width: number;
  onClose: () => void;
  children: ReactNode;
  /** Extra class on the panel for consumer styling. */
  className?: string;
  ariaLabel: string;
  /** Horizontal alignment relative to the anchor. */
  align?: 'center' | 'start';
}

const GAP = 4;
const PAD = 8;

export default function AnchoredPopover({
  open,
  anchorEl,
  width,
  onClose,
  children,
  className,
  ariaLabel,
  align = 'center',
}: AnchoredPopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<{ top: number; left: number; maxHeight: number } | null>(null);

  useLayoutEffect(() => {
    if (!open || !anchorEl) return;
    const compute = () => {
      const r = anchorEl.getBoundingClientRect();
      const top = r.bottom + GAP;
      const ideal = align === 'center' ? r.left + r.width / 2 - width / 2 : r.left;
      const left = Math.max(PAD, Math.min(window.innerWidth - width - PAD, ideal));
      const spaceBelow = window.innerHeight - top - PAD;
      setRect({ top, left, maxHeight: Math.max(160, spaceBelow) });
    };
    compute();
    window.addEventListener('resize', compute);
    window.addEventListener('scroll', compute, true);
    return () => {
      window.removeEventListener('resize', compute);
      window.removeEventListener('scroll', compute, true);
    };
  }, [open, anchorEl, width, align]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      const t = e.target as Node;
      if (panelRef.current?.contains(t)) return;
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

  return createPortal(
    <div
      ref={panelRef}
      className={`anchored-popover scrollable${className ? ' ' + className : ''}`}
      role="dialog"
      aria-label={ariaLabel}
      style={{ position: 'fixed', top: rect.top, left: rect.left, width, maxHeight: rect.maxHeight }}
    >
      {children}
    </div>,
    document.body,
  );
}
