/**
 * PriceCell + LeadTimeCell rendering (todo/COMMERCIAL.md Phase 3):
 * listed vs estimate vs absent, and the stocked-badge mapping for every
 * availability value.
 */

import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PriceCell, LeadTimeCell } from './CommercialCell';
import type { Product } from '../types/models';

const product = (over: Record<string, unknown>): Product =>
  ({
    product_id: 'p1',
    product_type: 'motor',
    product_name: 'M',
    manufacturer: 'ACME',
    ...over,
  }) as unknown as Product;

const estimate = {
  value: { value: 1240, unit: 'USD' },
  confidence: 'inferred',
  sources: [
    {
      url: null,
      kind: 'db_inference',
      retrieved_at: '2026-07-02T00:00:00Z',
      comparable_ids: ['a', 'b', 'c'],
      method_version: 'price-comps-v1',
    },
  ],
};

describe('PriceCell', () => {
  it('renders a listed msrp with a LISTED chip', () => {
    render(
      <PriceCell
        product={product({
          msrp: { value: 999, unit: 'USD' },
          msrp_source_url: 'https://shop.example.com/p',
        })}
      />,
    );
    expect(screen.getByText('999')).toBeTruthy();
    expect(screen.getByRole('button').textContent).toBe('LISTED');
  });

  it('listed without a source url carries the catalog-extraction detail', () => {
    render(
      <PriceCell
        product={product({
          msrp: { value: 999, unit: 'USD' },
          datasheet_url: 'https://vendor.example.com/cat.pdf',
        })}
      />,
    );
    const chip = screen.getByRole('button');
    expect(chip.getAttribute('aria-label')).toContain('listed — catalog extraction');
  });

  it('renders an estimate distinctly with ~$ formatting and an EST chip', () => {
    render(<PriceCell product={product({ price_estimate: estimate })} />);
    const value = screen.getByText('~$1,240');
    expect(value.className).toContain('commercial-cell-value--estimate');
    expect(screen.getByRole('button').textContent).toBe('EST');
  });

  it('renders an em dash when neither is present', () => {
    render(<PriceCell product={product({})} />);
    expect(screen.getByText('—')).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('chip click opens the source popover with the db_inference wording', () => {
    render(<PriceCell product={product({ price_estimate: estimate })} />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(
      screen.getByText(/Estimated from 3 comparable products \(price-comps-v1\)/),
    ).toBeTruthy();
  });
});

describe('LeadTimeCell', () => {
  const badgeCases: Array<[string, string, string]> = [
    ['in_stock', 'STOCKED', 'availability-badge--positive'],
    ['limited', 'STOCKED', 'availability-badge--positive'],
    ['back_order', 'BACK-ORDER', 'availability-badge--muted'],
    ['out_of_stock', 'OUT OF STOCK', 'availability-badge--muted'],
    ['pre_order', 'PRE-ORDER', 'availability-badge--muted'],
    ['discontinued', 'DISCONTINUED', 'availability-badge--muted'],
  ];

  it.each(badgeCases)('availability=%s renders a "%s" badge', (availability, label, cls) => {
    render(<LeadTimeCell product={product({ availability })} />);
    const badge = screen.getByRole('button');
    expect(badge.textContent).toBe(label);
    expect(badge.className).toContain(cls);
  });

  it('badge click opens a popover citing the availability source', () => {
    render(
      <LeadTimeCell
        product={product({
          availability: 'in_stock',
          availability_source_url: 'https://dist.example.com/p/1',
          availability_fetched_at: '2026-06-30T00:00:00Z',
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('dist.example.com')).toBeTruthy();
    expect(screen.getByText('2026-06-30')).toBeTruthy();
  });

  it('renders a listed lead_time value without a chip', () => {
    render(<LeadTimeCell product={product({ lead_time: { value: 6, unit: 'weeks' } })} />);
    expect(screen.getByText('6 weeks')).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders a lead-time estimate with its confidence chip', () => {
    render(
      <LeadTimeCell
        product={product({
          lead_time_estimate: {
            value: { value: 4, unit: 'weeks' },
            confidence: 'medium',
            sources: [
              {
                url: 'https://vendor.example.com/lead-times',
                kind: 'vendor_doc',
                retrieved_at: '2026-07-01T00:00:00Z',
                excerpt: 'Motors ship in 4 weeks',
              },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText('~4 weeks')).toBeTruthy();
    expect(screen.getByRole('button').textContent).toBe('MED');
  });

  it('renders an em dash when nothing is known', () => {
    render(<LeadTimeCell product={product({})} />);
    expect(screen.getByText('—')).toBeTruthy();
  });
});
