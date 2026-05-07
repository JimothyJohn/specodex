/**
 * ExternalLink — STYLE.md Phase 6.
 *
 * Locks down the security + accessibility contract that justified
 * replacing 3 bare `target="_blank"` anchors:
 *   - target="_blank" + rel="noopener noreferrer" always (security
 *     regression guard — the rel attr was missing on 2/3 of the
 *     replaced sites).
 *   - href / className / style / onClick passthrough.
 *   - Trailing arrow icon by default; suppressible via showIcon={false}.
 *   - Wrapped in a themed Tooltip; tooltip content defaults sensibly.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  act,
  cleanup,
  within,
} from '@testing-library/react';
import { ExternalLink } from './ExternalLink';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

const advance = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });

describe('ExternalLink', () => {
  it('renders an anchor with target=_blank and rel=noopener noreferrer', () => {
    render(<ExternalLink href="https://example.com/x">label</ExternalLink>);
    const link = screen.getByRole('link', { name: /label/ });
    expect(link.getAttribute('href')).toBe('https://example.com/x');
    expect(link.getAttribute('target')).toBe('_blank');
    // Single rel attribute carries both — tabnabbing protection + no
    // referer leak. Both must be present on every external link.
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('renders the trailing arrow icon by default', () => {
    render(<ExternalLink href="https://example.com">label</ExternalLink>);
    const link = screen.getByRole('link', { name: /label/ });
    expect(link.querySelector('.external-link-icon')).not.toBeNull();
  });

  it('omits the icon when showIcon=false (icon-only links)', () => {
    render(
      <ExternalLink href="https://example.com" showIcon={false}>
        <span>icon-only</span>
      </ExternalLink>,
    );
    const link = screen.getByRole('link', { name: /icon-only/ });
    expect(link.querySelector('.external-link-icon')).toBeNull();
  });

  it('passes className, style, and aria-label through', () => {
    render(
      <ExternalLink
        href="https://example.com"
        className="custom"
        style={{ color: 'red' }}
        aria-label="Source on GitHub"
      >
        link
      </ExternalLink>,
    );
    const link = screen.getByRole('link', { name: 'Source on GitHub' });
    expect(link.className).toContain('external-link');
    expect(link.className).toContain('custom');
    expect(link.getAttribute('style')).toContain('color');
  });

  it('invokes onClick (used in modals to stopPropagation)', () => {
    const onClick = vi.fn();
    render(
      <ExternalLink href="https://example.com" onClick={onClick}>
        label
      </ExternalLink>,
    );
    fireEvent.click(screen.getByRole('link', { name: /label/ }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('shows the tooltip on hover with the provided content', () => {
    render(
      <ExternalLink href="https://example.com" tooltip="View datasheet PDF">
        label
      </ExternalLink>,
    );
    const link = screen.getByRole('link', { name: /label/ });
    fireEvent.mouseEnter(link);
    advance(400);
    const tooltip = screen.getByRole('tooltip');
    expect(within(tooltip).getByText('View datasheet PDF')).toBeTruthy();
  });

  it('falls back to "Opens in a new tab" when no tooltip is provided', () => {
    render(<ExternalLink href="https://example.com">label</ExternalLink>);
    const link = screen.getByRole('link', { name: /label/ });
    fireEvent.mouseEnter(link);
    advance(400);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Opens in a new tab');
  });
});
