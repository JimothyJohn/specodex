/**
 * Vendor-facts + citation display helpers.
 *
 * Trimmed 2026-07-24: the price / lead-time resolvers (resolvePrice,
 * resolveLeadTime, STOCKED_AVAILABILITY_VALUES) left with the commercial
 * UI removal (PR #341) — the per-product commercial data wasn't reliable
 * enough to show. What stays is the manufacturer-level surface: the
 * VendorDrawer reads the citation-required vendor-facts registry
 * (src/data/vendors.json) through `getVendorFacts`, and SourcePopover
 * renders citations flattened by `toDisplayCitation`.
 *
 * Ground rules mirrored from todo/COMMERCIAL.md:
 * - A displayed fact always carries its receipts (DisplayCitation[]).
 * - db_inference citations are described as "Estimated from N comparable
 *   products (method X)" — replicable, not hand-wavy.
 */

import type { SourceCitation } from '../types/generated';

// ---------------------------------------------------------------------------
// Citation display
// ---------------------------------------------------------------------------

/** A citation flattened for display. */
export interface DisplayCitation {
  kindLabel: string;
  url: string | null;
  retrievedAt: string | null;
  excerpt: string | null;
  /** db_inference only — comparable count + versioned method. */
  inference: { count: number; method: string } | null;
}

const KIND_LABELS: Record<SourceCitation['kind'], string> = {
  oem: 'OEM',
  distributor: 'Distributor',
  aggregator: 'Aggregator',
  price_book: 'Price book',
  shopping: 'Shopping',
  article: 'Article',
  vendor_doc: 'Vendor doc',
  db_inference: 'Database inference',
};

/** Hostname of a URL for the "publisher" line; null when unparseable. */
export const hostnameOf = (url: string | null | undefined): string | null => {
  if (!url || typeof url !== 'string') return null;
  try {
    return new URL(url).hostname || null;
  } catch {
    return null;
  }
};

/** "Estimated from N comparable products (method X)" — exact wording pinned by test. */
export const describeInference = (count: number, method: string): string =>
  `Estimated from ${count} comparable product${count === 1 ? '' : 's'} (${method})`;

export const toDisplayCitation = (c: SourceCitation): DisplayCitation => {
  const inference =
    c.kind === 'db_inference'
      ? {
          count: Array.isArray(c.comparable_ids) ? c.comparable_ids.length : 0,
          method: c.method_version ?? 'unknown method',
        }
      : null;
  return {
    kindLabel: KIND_LABELS[c.kind] ?? c.kind,
    url: c.url ?? null,
    retrievedAt: c.retrieved_at || null,
    excerpt: c.excerpt ?? null,
    inference,
  };
};

// ---------------------------------------------------------------------------
// Vendor-facts registry accessor
// ---------------------------------------------------------------------------

export interface VendorFact {
  label: string;
  value: string;
  sources: SourceCitation[];
}

/** Manufacturer name → registry slug ("Allen-Bradley " → "allen-bradley"). */
export const vendorSlug = (manufacturer: string): string =>
  manufacturer
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');

const isVendorFact = (v: unknown): v is VendorFact => {
  if (!v || typeof v !== 'object') return false;
  const f = v as Record<string, unknown>;
  return (
    typeof f.label === 'string' &&
    typeof f.value === 'string' &&
    Array.isArray(f.sources) &&
    // Citation-required: a fact without at least one source is dropped.
    f.sources.length > 0 &&
    f.sources.every(
      (s) => !!s && typeof s === 'object' && typeof (s as SourceCitation).kind === 'string',
    )
  );
};

/**
 * Read a manufacturer's facts out of the vendors.json registry. Tolerant
 * of every missing-shape case (empty registry, absent slug, malformed
 * entry): all of them return [] — the drawer renders the honest empty
 * state, never a crash and never invented copy.
 */
export const getVendorFacts = (data: unknown, manufacturer: string): VendorFact[] => {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return [];
  const entry = (data as Record<string, unknown>)[vendorSlug(manufacturer)];
  if (!entry || typeof entry !== 'object') return [];
  const facts = (entry as Record<string, unknown>).facts;
  if (!Array.isArray(facts)) return [];
  return facts.filter(isVendorFact);
};
