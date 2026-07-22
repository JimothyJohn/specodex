/**
 * Contract tests for the pure commercial resolvers (todo/COMMERCIAL.md
 * Phase 3). Written before the cell components — the resolution order
 * (listed > estimate > absent; availability > lead_time > estimate) is
 * the load-bearing rule and it's pinned here, not in JSX.
 */

import { describe, it, expect } from 'vitest';
import type { SourceCitation, SourcedFigure } from '../types/generated';
import {
  resolvePrice,
  resolveLeadTime,
  toDisplayCitation,
  describeInference,
  hostnameOf,
  vendorSlug,
  getVendorFacts,
  STOCKED_AVAILABILITY_VALUES,
  type Availability,
} from './commercial';

const listedCitation: SourceCitation = {
  url: 'https://shop.example.com/p/abc',
  kind: 'distributor',
  retrieved_at: '2026-07-01T12:00:00Z',
  excerpt: 'Price: $1,240.00',
};

const inferenceCitation: SourceCitation = {
  url: null,
  kind: 'db_inference',
  retrieved_at: '2026-07-02T00:00:00Z',
  excerpt: null,
  comparable_ids: ['id-a', 'id-b', 'id-c'],
  method_version: 'price-comps-v1',
};

const estimateFigure: SourcedFigure = {
  value: { value: 1240, unit: 'USD' },
  confidence: 'inferred',
  sources: [inferenceCitation],
};

describe('resolvePrice', () => {
  it('prefers listed msrp with its source url', () => {
    const r = resolvePrice({
      msrp: { value: 999, unit: 'USD' },
      msrp_source_url: 'https://shop.example.com/p/abc',
      msrp_fetched_at: '2026-07-01T12:00:00Z',
      price_estimate: estimateFigure,
    });
    expect(r.kind).toBe('listed');
    expect(r.confidence).toBe('listed');
    expect(r.text).toBe('999');
    expect(r.detail).toBeNull();
    expect(r.sources).toHaveLength(1);
    expect(r.sources[0].url).toBe('https://shop.example.com/p/abc');
    expect(r.sources[0].retrievedAt).toBe('2026-07-01T12:00:00Z');
  });

  it('listed msrp without a source url cites the datasheet as catalog extraction', () => {
    const r = resolvePrice({
      msrp: { value: 1500, unit: 'USD' },
      datasheet_url: 'https://vendor.example.com/catalog.pdf',
    });
    expect(r.kind).toBe('listed');
    expect(r.detail).toBe('listed — catalog extraction');
    expect(r.sources[0].kindLabel).toBe('Catalog extraction');
    expect(r.sources[0].url).toBe('https://vendor.example.com/catalog.pdf');
  });

  it('falls back to the estimate, rendered distinctly with its confidence', () => {
    const r = resolvePrice({ price_estimate: estimateFigure });
    expect(r.kind).toBe('estimate');
    expect(r.text).toBe('~$1,240');
    expect(r.confidence).toBe('inferred');
    expect(r.sources).toHaveLength(1);
    expect(r.sources[0].inference).toEqual({ count: 3, method: 'price-comps-v1' });
  });

  it('neither listed nor estimate renders an em dash', () => {
    const r = resolvePrice({});
    expect(r.kind).toBe('none');
    expect(r.text).toBe('—');
    expect(r.sources).toHaveLength(0);
  });

  it('never lets a malformed msrp mask the estimate', () => {
    const r = resolvePrice({
      msrp: { value: Number.NaN, unit: 'USD' },
      price_estimate: estimateFigure,
    });
    expect(r.kind).toBe('estimate');
  });
});

describe('resolveLeadTime', () => {
  const badgeCases: Array<[Availability, string, 'positive' | 'muted']> = [
    ['in_stock', 'STOCKED', 'positive'],
    ['limited', 'STOCKED', 'positive'],
    ['back_order', 'BACK-ORDER', 'muted'],
    ['out_of_stock', 'OUT OF STOCK', 'muted'],
    ['pre_order', 'PRE-ORDER', 'muted'],
    ['discontinued', 'DISCONTINUED', 'muted'],
  ];

  it.each(badgeCases)('maps availability=%s to a %s badge', (availability, label, tone) => {
    const r = resolveLeadTime({
      availability,
      availability_source_url: 'https://dist.example.com/p/1',
      availability_fetched_at: '2026-06-30T00:00:00Z',
    });
    expect(r.kind).toBe('badge');
    expect(r.text).toBe(label);
    expect(r.tone).toBe(tone);
    expect(r.sources[0].url).toBe('https://dist.example.com/p/1');
    expect(r.sources[0].retrievedAt).toBe('2026-06-30T00:00:00Z');
  });

  it('the stocked set is exactly in_stock + limited', () => {
    expect([...STOCKED_AVAILABILITY_VALUES]).toEqual(['in_stock', 'limited']);
  });

  it('renders a listed numeric lead_time when no availability snapshot exists', () => {
    const r = resolveLeadTime({ lead_time: { value: 6, unit: 'weeks' } });
    expect(r.kind).toBe('value');
    expect(r.text).toBe('6 weeks');
    expect(r.confidence).toBeNull();
  });

  it('falls back to the lead-time estimate with chip-worthy confidence', () => {
    const r = resolveLeadTime({
      lead_time_estimate: {
        value: { value: 4, unit: 'weeks' },
        confidence: 'medium',
        sources: [listedCitation],
      },
    });
    expect(r.kind).toBe('estimate');
    expect(r.text).toBe('~4 weeks');
    expect(r.confidence).toBe('medium');
    expect(r.sources).toHaveLength(1);
  });

  it('renders an em dash when nothing is known', () => {
    const r = resolveLeadTime({});
    expect(r.kind).toBe('none');
    expect(r.text).toBe('—');
  });

  it('availability wins over lead_time and the estimate', () => {
    const r = resolveLeadTime({
      availability: 'in_stock',
      lead_time: { value: 6, unit: 'weeks' },
      lead_time_estimate: estimateFigure,
    });
    expect(r.kind).toBe('badge');
  });
});

describe('toDisplayCitation', () => {
  it('maps a plain citation to kind label, url, date, excerpt', () => {
    const d = toDisplayCitation(listedCitation);
    expect(d.kindLabel).toBe('Distributor');
    expect(d.url).toBe('https://shop.example.com/p/abc');
    expect(d.retrievedAt).toBe('2026-07-01T12:00:00Z');
    expect(d.excerpt).toBe('Price: $1,240.00');
    expect(d.inference).toBeNull();
  });

  it('carries comparable count + method for db_inference', () => {
    const d = toDisplayCitation(inferenceCitation);
    expect(d.kindLabel).toBe('Database inference');
    expect(d.inference).toEqual({ count: 3, method: 'price-comps-v1' });
  });
});

describe('describeInference', () => {
  it('pins the exact wording', () => {
    expect(describeInference(3, 'price-comps-v1')).toBe(
      'Estimated from 3 comparable products (price-comps-v1)',
    );
    expect(describeInference(1, 'price-comps-v1')).toBe(
      'Estimated from 1 comparable product (price-comps-v1)',
    );
  });
});

describe('hostnameOf', () => {
  it('derives the publisher hostname', () => {
    expect(hostnameOf('https://shop.example.com/p/abc?x=1')).toBe('shop.example.com');
  });
  it('returns null for garbage', () => {
    expect(hostnameOf('not a url')).toBeNull();
    expect(hostnameOf(null)).toBeNull();
    expect(hostnameOf('')).toBeNull();
  });
});

describe('vendor facts accessor', () => {
  const registry = {
    'allen-bradley': {
      facts: [
        {
          label: 'Standard warranty',
          value: '12 months from installation',
          sources: [listedCitation],
        },
        // Citation-required: fact without sources must be dropped.
        { label: 'Bogus', value: 'no receipts', sources: [] },
      ],
    },
  };

  it('slugifies manufacturer names', () => {
    expect(vendorSlug('Allen-Bradley ')).toBe('allen-bradley');
    expect(vendorSlug('Oriental Motor')).toBe('oriental-motor');
    expect(vendorSlug('ABB')).toBe('abb');
  });

  it('returns cited facts for a known vendor and drops citation-less ones', () => {
    const facts = getVendorFacts(registry, 'Allen-Bradley');
    expect(facts).toHaveLength(1);
    expect(facts[0].label).toBe('Standard warranty');
  });

  it('tolerates the empty registry and missing keys', () => {
    expect(getVendorFacts({}, 'Siemens')).toEqual([]);
    expect(getVendorFacts(null, 'Siemens')).toEqual([]);
    expect(getVendorFacts({ siemens: 'garbage' }, 'Siemens')).toEqual([]);
    expect(getVendorFacts({ siemens: { facts: 'nope' } }, 'Siemens')).toEqual([]);
  });
});
