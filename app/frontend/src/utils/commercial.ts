/**
 * Commercial-figure resolution — pure functions behind the commercial-first
 * UI (todo/COMMERCIAL.md Phase 3).
 *
 * Every commercial cell (price, lead time) resolves through here so the
 * "listed beats estimate, estimate never masquerades as listed" rule lives
 * in exactly one place, unit-tested directly. Components (PriceCell,
 * LeadTimeCell, SourcePopover) render whatever these functions return —
 * they never re-derive confidence or provenance themselves.
 *
 * Ground rules mirrored from todo/COMMERCIAL.md:
 * - A displayed figure always carries its receipts (DisplayCitation[]).
 * - Listed and estimated values are never merged; kind is explicit.
 * - db_inference citations are described as "Estimated from N comparable
 *   products (method X)" — replicable, not hand-wavy.
 */

import type { SourcedFigure, SourceCitation, ValueUnit } from '../types/generated';
import { formatNumber } from './formatting';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type Confidence = SourcedFigure['confidence'];
export type Availability =
  | 'in_stock'
  | 'back_order'
  | 'out_of_stock'
  | 'pre_order'
  | 'limited'
  | 'discontinued';

/**
 * A citation flattened for display. Real SourceCitations map through
 * `toDisplayCitation`; provenance pairs that predate the SourcedFigure
 * schema (msrp_source_url/msrp_fetched_at, availability_source_url/
 * availability_fetched_at) are synthesized with honest labels instead of
 * fabricating a SourceKind they never carried.
 */
export interface DisplayCitation {
  kindLabel: string;
  url: string | null;
  retrievedAt: string | null;
  excerpt: string | null;
  /** db_inference only — comparable count + versioned method. */
  inference: { count: number; method: string } | null;
}

/** The subset of ProductBase the commercial resolvers read. */
export interface CommercialFields {
  msrp?: ValueUnit | null;
  msrp_source_url?: string | null;
  msrp_fetched_at?: string | null;
  availability?: Availability | null;
  availability_source_url?: string | null;
  availability_fetched_at?: string | null;
  price_estimate?: SourcedFigure | null;
  lead_time?: ValueUnit | null;
  lead_time_estimate?: SourcedFigure | null;
  warranty?: ValueUnit | null;
  datasheet_url?: string | null;
}

export interface ResolvedPrice {
  kind: 'listed' | 'estimate' | 'none';
  /** Cell text ('—' when kind === 'none'). */
  text: string;
  confidence: Confidence | null;
  /** Popover subtitle override (e.g. 'listed — catalog extraction'). */
  detail: string | null;
  sources: DisplayCitation[];
}

export interface ResolvedLeadTime {
  kind: 'badge' | 'value' | 'estimate' | 'none';
  /** Cell text — badge label for kind 'badge', value text otherwise. */
  text: string;
  /** 'positive' for stocked, 'muted' for the negative states. */
  tone: 'positive' | 'muted' | null;
  confidence: Confidence | null;
  sources: DisplayCitation[];
}

// ---------------------------------------------------------------------------
// Citation helpers
// ---------------------------------------------------------------------------

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
// Price resolution
// ---------------------------------------------------------------------------

/**
 * Money formatting for estimates (`~$1,240`). Unlike table-cell
 * formatNumber (which suppresses grouping under 10k to avoid comma
 * collisions in list cells), currency always groups thousands.
 */
const formatMoney = (n: number): string =>
  n.toLocaleString('en-US', { maximumFractionDigits: 2 });

const isValueUnit = (v: unknown): v is ValueUnit =>
  !!v &&
  typeof v === 'object' &&
  typeof (v as ValueUnit).value === 'number' &&
  Number.isFinite((v as ValueUnit).value);

/**
 * Listed price beats estimate; estimate renders distinctly (`~$X,XXX`);
 * neither → '—'. Listed prices without a source URL cite the datasheet
 * (catalog extraction) so even the weakest listed figure keeps a receipt.
 */
export const resolvePrice = (p: CommercialFields): ResolvedPrice => {
  if (isValueUnit(p.msrp)) {
    const hasSourceUrl = !!p.msrp_source_url;
    const sources: DisplayCitation[] = [
      {
        kindLabel: hasSourceUrl ? 'Price source' : 'Catalog extraction',
        url: hasSourceUrl ? p.msrp_source_url! : p.datasheet_url ?? null,
        retrievedAt: p.msrp_fetched_at ?? null,
        excerpt: null,
        inference: null,
      },
    ];
    return {
      kind: 'listed',
      text: formatNumber(p.msrp.value),
      confidence: 'listed',
      detail: hasSourceUrl ? null : 'listed — catalog extraction',
      sources,
    };
  }
  const est = p.price_estimate;
  if (est && isValueUnit(est.value)) {
    return {
      kind: 'estimate',
      text: `~$${formatMoney(est.value.value)}`,
      confidence: est.confidence,
      detail: null,
      sources: (est.sources ?? []).map(toDisplayCitation),
    };
  }
  return { kind: 'none', text: '—', confidence: null, detail: null, sources: [] };
};

// ---------------------------------------------------------------------------
// Lead-time resolution
// ---------------------------------------------------------------------------

export const STOCKED_AVAILABILITY_VALUES: readonly Availability[] = [
  'in_stock',
  'limited',
];

const AVAILABILITY_BADGES: Record<Availability, { label: string; tone: 'positive' | 'muted' }> = {
  in_stock: { label: 'STOCKED', tone: 'positive' },
  limited: { label: 'STOCKED', tone: 'positive' },
  back_order: { label: 'BACK-ORDER', tone: 'muted' },
  out_of_stock: { label: 'OUT OF STOCK', tone: 'muted' },
  pre_order: { label: 'PRE-ORDER', tone: 'muted' },
  discontinued: { label: 'DISCONTINUED', tone: 'muted' },
};

/**
 * Availability snapshot beats lead time (it's the honest, populated
 * signal); a listed numeric lead_time beats the estimate; else '—'.
 */
export const resolveLeadTime = (p: CommercialFields): ResolvedLeadTime => {
  const availability = p.availability ?? null;
  if (availability && AVAILABILITY_BADGES[availability]) {
    const badge = AVAILABILITY_BADGES[availability];
    const sources: DisplayCitation[] = p.availability_source_url
      ? [
          {
            kindLabel: 'Availability source',
            url: p.availability_source_url,
            retrievedAt: p.availability_fetched_at ?? null,
            excerpt: null,
            inference: null,
          },
        ]
      : [];
    return { kind: 'badge', text: badge.label, tone: badge.tone, confidence: null, sources };
  }
  if (isValueUnit(p.lead_time)) {
    return {
      kind: 'value',
      text: `${formatNumber(p.lead_time.value)} ${p.lead_time.unit}`.trim(),
      tone: null,
      confidence: null,
      sources: [],
    };
  }
  const est = p.lead_time_estimate;
  if (est && isValueUnit(est.value)) {
    return {
      kind: 'estimate',
      text: `~${formatNumber(est.value.value)} ${est.value.unit}`.trim(),
      tone: null,
      confidence: est.confidence,
      sources: (est.sources ?? []).map(toDisplayCitation),
    };
  }
  return { kind: 'none', text: '—', tone: null, confidence: null, sources: [] };
};

// ---------------------------------------------------------------------------
// Vendor-facts registry accessor (Phase 4 shell — data ships separately)
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
