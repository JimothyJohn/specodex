/**
 * Tooltip — themed, portaled hover/focus tooltip primitive.
 *
 * Replaces the native `title` attribute (OS-styled, 1.5s delay, no
 * theming). Wrap any focusable element to attach a tooltip that follows
 * the field-manual palette in both light and dark modes. STYLE.md
 * Phase 1.
 *
 * Why portaled: the table cells, header buttons, and chip controls all
 * live inside `overflow:hidden` ancestors. Portal to `document.body`
 * sidesteps every clipping boundary, same pattern as
 * MultiSelectFilterPopover and ProductDetailModal.
 *
 * No `@floating-ui/react` dependency — placement is hand-rolled with
 * `getBoundingClientRect` against the four cardinal placements with a
 * viewport-edge clamp. Good enough for a tooltip; reach for a real
 * positioning library only if we add menu / autocomplete primitives
 * that need richer collision logic.
 */

import {
  cloneElement,
  isValidElement,
  ReactElement,
  ReactNode,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';

export type TooltipPlacement = 'top' | 'bottom' | 'left' | 'right';

export interface TooltipProps {
  /** Tooltip body — short, plain string preferred; ReactNode supported. */
  content: ReactNode;
  /** Cardinal placement. Auto-flips to opposite side when clipped. */
  placement?: TooltipPlacement;
  /** Open delay in ms. Mirrors a native title's roughly-1.5s delay,
   *  dialed down to 300ms (see STYLE.md). */
  delay?: number;
  /**
   * Single child element. Tooltip clones it to attach event handlers
   * and `aria-describedby`; non-element children fall back to a span
   * wrapper.
   */
  children: ReactNode;
  /** Optional className applied to the portaled tooltip root. */
  className?: string;
}

const VIEWPORT_GAP = 4; // px between tooltip and anchor
const VIEWPORT_PAD = 4; // px from viewport edge before clamping

interface Position {
  top: number;
  left: number;
  resolvedPlacement: TooltipPlacement;
}

/**
 * Compute a `position: fixed` (top, left) for the tooltip relative to
 * the anchor + chosen placement, then clamp inside the viewport. If the
 * preferred side has insufficient room, flip to the opposite side.
 */
function computePosition(
  anchor: DOMRect,
  tooltip: DOMRect,
  preferred: TooltipPlacement,
): Position {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  const fits = (placement: TooltipPlacement): boolean => {
    switch (placement) {
      case 'top':
        return anchor.top - tooltip.height - VIEWPORT_GAP >= VIEWPORT_PAD;
      case 'bottom':
        return anchor.bottom + tooltip.height + VIEWPORT_GAP <= vh - VIEWPORT_PAD;
      case 'left':
        return anchor.left - tooltip.width - VIEWPORT_GAP >= VIEWPORT_PAD;
      case 'right':
        return anchor.right + tooltip.width + VIEWPORT_GAP <= vw - VIEWPORT_PAD;
    }
  };

  // Auto-flip if preferred side doesn't fit but the opposite does.
  const opposite: Record<TooltipPlacement, TooltipPlacement> = {
    top: 'bottom',
    bottom: 'top',
    left: 'right',
    right: 'left',
  };
  const placement = !fits(preferred) && fits(opposite[preferred]) ? opposite[preferred] : preferred;

  let top = 0;
  let left = 0;
  switch (placement) {
    case 'top':
      top = anchor.top - tooltip.height - VIEWPORT_GAP;
      left = anchor.left + (anchor.width - tooltip.width) / 2;
      break;
    case 'bottom':
      top = anchor.bottom + VIEWPORT_GAP;
      left = anchor.left + (anchor.width - tooltip.width) / 2;
      break;
    case 'left':
      top = anchor.top + (anchor.height - tooltip.height) / 2;
      left = anchor.left - tooltip.width - VIEWPORT_GAP;
      break;
    case 'right':
      top = anchor.top + (anchor.height - tooltip.height) / 2;
      left = anchor.right + VIEWPORT_GAP;
      break;
  }

  // Clamp inside viewport so the tooltip never escapes the visible area.
  left = Math.max(VIEWPORT_PAD, Math.min(left, vw - tooltip.width - VIEWPORT_PAD));
  top = Math.max(VIEWPORT_PAD, Math.min(top, vh - tooltip.height - VIEWPORT_PAD));

  return { top, left, resolvedPlacement: placement };
}

export function Tooltip({
  content,
  placement = 'top',
  delay = 300,
  children,
  className,
}: TooltipProps) {
  const id = useId();
  const anchorRef = useRef<HTMLElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const openTimerRef = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<Position | null>(null);

  const clearOpenTimer = useCallback(() => {
    if (openTimerRef.current !== null) {
      window.clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
  }, []);

  const show = useCallback(() => {
    clearOpenTimer();
    if (delay > 0) {
      openTimerRef.current = window.setTimeout(() => setOpen(true), delay);
    } else {
      setOpen(true);
    }
  }, [delay, clearOpenTimer]);

  const hide = useCallback(() => {
    clearOpenTimer();
    setOpen(false);
  }, [clearOpenTimer]);

  // Position once the tooltip mounts, then on scroll/resize while open.
  useLayoutEffect(() => {
    if (!open || !anchorRef.current || !tooltipRef.current) return;
    const update = () => {
      if (!anchorRef.current || !tooltipRef.current) return;
      const next = computePosition(
        anchorRef.current.getBoundingClientRect(),
        tooltipRef.current.getBoundingClientRect(),
        placement,
      );
      setPosition(next);
    };
    update();
    window.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    return () => {
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
    };
  }, [open, placement]);

  // Esc closes; click outside closes.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') hide();
    };
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (anchorRef.current?.contains(target)) return;
      if (tooltipRef.current?.contains(target)) return;
      hide();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onPointer);
    };
  }, [open, hide]);

  // Cleanup the open-delay timer on unmount.
  useEffect(() => clearOpenTimer, [clearOpenTimer]);

  const handlers = {
    onMouseEnter: show,
    onMouseLeave: hide,
    onFocus: show,
    onBlur: hide,
  };

  // Clone the child to attach event handlers + the ref + aria-describedby.
  // Non-element children get a span wrapper so the same affordances work.
  const child = isValidElement(children) ? (
    cloneElement(
      children as ReactElement<{
        ref?: (node: HTMLElement | null) => void;
        onMouseEnter?: (e: React.MouseEvent) => void;
        onMouseLeave?: (e: React.MouseEvent) => void;
        onFocus?: (e: React.FocusEvent) => void;
        onBlur?: (e: React.FocusEvent) => void;
        'aria-describedby'?: string;
      }>,
      {
        ref: (node: HTMLElement | null) => {
          anchorRef.current = node;
        },
        ...handlers,
        'aria-describedby': open ? id : undefined,
      },
    )
  ) : (
    <span
      ref={(node) => {
        anchorRef.current = node;
      }}
      aria-describedby={open ? id : undefined}
      {...handlers}
    >
      {children}
    </span>
  );

  // Tooltip itself renders into document.body via portal so it can never
  // be clipped by an `overflow:hidden` ancestor.
  const tooltip =
    open && typeof document !== 'undefined'
      ? createPortal(
          <div
            ref={tooltipRef}
            id={id}
            role="tooltip"
            data-placement={position?.resolvedPlacement ?? placement}
            className={className ? `app-tooltip ${className}` : 'app-tooltip'}
            style={{
              position: 'fixed',
              top: position?.top ?? -9999,
              left: position?.left ?? -9999,
              // Hide off-screen until first measurement to avoid a flash
              // at (0,0) before the layout effect fires.
              visibility: position ? 'visible' : 'hidden',
              pointerEvents: 'none',
            }}
          >
            {content}
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      {child}
      {tooltip}
    </>
  );
}

export default Tooltip;
