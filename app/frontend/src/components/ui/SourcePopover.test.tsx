/**
 * SourcePopover — citation rendering incl. db_inference wording
 * (todo/COMMERCIAL.md Phase 3).
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import SourcePopover from './SourcePopover';
import type { DisplayCitation } from '../../utils/commercial';

const distributorCitation: DisplayCitation = {
  kindLabel: 'Distributor',
  url: 'https://shop.example.com/p/abc',
  retrievedAt: '2026-07-01T12:00:00Z',
  excerpt: 'Price: $1,240.00 — in stock',
  inference: null,
};

const inferenceCitation: DisplayCitation = {
  kindLabel: 'Database inference',
  url: null,
  retrievedAt: '2026-07-02T00:00:00Z',
  excerpt: null,
  inference: { count: 5, method: 'price-comps-v1' },
};

const renderPopover = (
  citations: DisplayCitation[],
  over: Partial<Parameters<typeof SourcePopover>[0]> = {},
) => {
  const anchor = document.createElement('button');
  document.body.appendChild(anchor);
  const onClose = vi.fn();
  render(
    <SourcePopover
      open
      anchorEl={anchor}
      heading="Price"
      citations={citations}
      onClose={onClose}
      {...over}
    />,
  );
  return { anchor, onClose };
};

describe('SourcePopover', () => {
  it('renders kind label, publisher hostname, retrieval date, quoted excerpt, and external link', () => {
    renderPopover([distributorCitation]);
    expect(screen.getByText('Distributor')).toBeTruthy();
    expect(screen.getByText('shop.example.com')).toBeTruthy();
    expect(screen.getByText('2026-07-01')).toBeTruthy();
    expect(screen.getByText(/Price: \$1,240\.00 — in stock/)).toBeTruthy();
    const link = screen.getByText('View source').closest('a');
    expect(link?.getAttribute('href')).toBe('https://shop.example.com/p/abc');
    expect(link?.getAttribute('rel')).toContain('noopener');
  });

  it('renders the db_inference sentence with comparable count and method', () => {
    renderPopover([inferenceCitation]);
    expect(
      screen.getByText(/Estimated from 5 comparable products \(price-comps-v1\)/),
    ).toBeTruthy();
    expect(screen.getByText(/5 comparables/)).toBeTruthy();
    // No URL → no external link for this citation.
    expect(screen.queryByText('View source')).toBeNull();
  });

  it('shows the subheading (catalog-extraction wording) when provided', () => {
    renderPopover([distributorCitation], { subheading: 'listed — catalog extraction' });
    expect(screen.getByText('listed — catalog extraction')).toBeTruthy();
  });

  it('renders an honest empty state when there are no citations', () => {
    renderPopover([]);
    expect(screen.getByText('No sources recorded')).toBeTruthy();
  });

  it('dismisses on outside click but not on inside click', () => {
    const { onClose } = renderPopover([distributorCitation]);
    fireEvent.mouseDown(screen.getByText('Distributor'));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('dismisses on Escape', () => {
    const { onClose } = renderPopover([distributorCitation]);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('renders nothing when closed', () => {
    const anchor = document.createElement('button');
    document.body.appendChild(anchor);
    render(
      <SourcePopover
        open={false}
        anchorEl={anchor}
        heading="Price"
        citations={[distributorCitation]}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
