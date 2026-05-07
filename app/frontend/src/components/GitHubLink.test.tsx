/**
 * GitHubLink — Phase 4 of FRONTEND_TESTING.md.
 *
 * Trivial component, but the URL pin is worth a test: someone renaming the
 * repo or rebranding to a different host should see this fail before
 * shipping a stale link.
 *
 * Migrated to <ExternalLink> in STYLE.md Phase 6 (2026-05-04). The native
 * `title` attribute is gone — the link now uses the themed Tooltip
 * primitive instead. Tests assert href / target / rel passthrough plus
 * the aria-label; the tooltip-content contract is exercised in
 * ui/ExternalLink.test.tsx.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import GitHubLink from './GitHubLink';

describe('GitHubLink', () => {
  it('renders a link with the canonical repo URL', () => {
    render(<GitHubLink />);
    const link = screen.getByRole('link');
    expect(link.getAttribute('href')).toBe('https://github.com/JimothyJohn/specodex');
  });

  it('opens in a new tab with safe rel attributes', () => {
    render(<GitHubLink />);
    const link = screen.getByRole('link');
    expect(link.getAttribute('target')).toBe('_blank');
    const rel = link.getAttribute('rel') ?? '';
    expect(rel).toContain('noopener');
    expect(rel).toContain('noreferrer');
  });

  it('exposes an accessible label (no native title attribute post-Phase-6)', () => {
    render(<GitHubLink />);
    const link = screen.getByRole('link');
    expect(link.getAttribute('aria-label')).toBe('Source on GitHub');
    // `title` is intentionally absent — STYLE.md Phase 1 banned native
    // tooltips. The themed Tooltip is wired by ExternalLink and tested
    // independently in ui/ExternalLink.test.tsx.
    expect(link.getAttribute('title')).toBeNull();
  });
});
