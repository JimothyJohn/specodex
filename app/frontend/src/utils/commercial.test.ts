/**
 * Contract tests for the vendor-facts + citation display helpers. The
 * price / lead-time resolver tests left with the resolvers themselves
 * (commercial UI removal, PR #341); what stays pins the VendorDrawer's
 * data path: registry accessor tolerance and citation flattening.
 */

import { describe, it, expect } from 'vitest';
import type { SourceCitation } from '../types/generated';
import {
  toDisplayCitation,
  describeInference,
  hostnameOf,
  vendorSlug,
  getVendorFacts,
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
