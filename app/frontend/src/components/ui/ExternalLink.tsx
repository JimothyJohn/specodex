/**
 * ExternalLink — themed `<a target="_blank">` with consistent affordances.
 *
 * Replaces bare `target="_blank"` anchors. STYLE.md Phase 6.
 *
 * What it does, in one place:
 * 1. Always sets `rel="noopener noreferrer"` (security: blocks the new tab
 *    from reaching `window.opener`).
 * 2. Appends a 12px arrow-up-right icon so the user sees they're leaving
 *    the SPA. Suppressible via `showIcon={false}` for icon-only links
 *    (e.g. GitHub icon).
 * 3. Wraps the link in a themed Tooltip explaining where it goes — the
 *    app-native equivalent of the OS "leaving site" affordance.
 */

import { CSSProperties, MouseEventHandler, ReactNode } from 'react';
import Tooltip, { TooltipPlacement } from './Tooltip';

export interface ExternalLinkProps {
  href: string;
  /** Tooltip body. Defaults to "Opens in a new tab". */
  tooltip?: ReactNode;
  /** Tooltip placement. Default "top". */
  tooltipPlacement?: TooltipPlacement;
  /**
   * Render the trailing arrow-up-right icon. Default true. Set to
   * false for icon-only anchors (e.g. a GitHub icon button) where an
   * extra arrow would be visual noise.
   */
  showIcon?: boolean;
  className?: string;
  style?: CSSProperties;
  /** Mirrored onto the `<a>`. Use for stopPropagation in modal contexts. */
  onClick?: MouseEventHandler<HTMLAnchorElement>;
  /** Override the auto-set aria-label (defaults to a generic "opens in new tab" suffix). */
  'aria-label'?: string;
  children: ReactNode;
}

/** 12px arrow-up-right icon. Inline-flex so it sits on the link's baseline. */
function ExternalIcon() {
  return (
    <svg
      className="external-link-icon"
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3.5 8.5l5-5" />
      <path d="M4 3.5h4.5V8" />
    </svg>
  );
}

export function ExternalLink({
  href,
  tooltip,
  tooltipPlacement = 'top',
  showIcon = true,
  className,
  style,
  onClick,
  'aria-label': ariaLabel,
  children,
}: ExternalLinkProps) {
  const tooltipContent = tooltip ?? 'Opens in a new tab';

  const anchor = (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={
        className
          ? `external-link ${className}`
          : 'external-link'
      }
      style={style}
      onClick={onClick}
      aria-label={ariaLabel}
    >
      {children}
      {showIcon ? <ExternalIcon /> : null}
    </a>
  );

  return (
    <Tooltip content={tooltipContent} placement={tooltipPlacement}>
      {anchor}
    </Tooltip>
  );
}

export default ExternalLink;
