/**
 * Feedback mailto helper — builds a structured `mailto:` URL so the
 * FeedbackModal stays a thin shell over the user's mail client.
 *
 * No backend / API call. Submitted feedback opens the user's default
 * mail composer pre-populated with the category, free-text message,
 * and any app context the call site captured. If the user wants to,
 * they edit the body before sending; if they don't, what arrives in
 * Nick's inbox already reads like a small bug report.
 *
 * Stay-in-mailto rationale: zero infra, no auth, no PII to design,
 * no rate-limiting / abuse surface. A POST endpoint to S3/SES is the
 * obvious follow-up if volume warrants — see CLAUDE.md "Per-PR doc
 * convention" for the trail.
 */
import type { FilterCriterion } from '../types/filters';

export const FEEDBACK_TO = 'nick@advin.io';

export type FeedbackCategory =
  | 'missing_product'
  | 'wrong_info'
  | 'no_match'
  | 'general';

export const FEEDBACK_CATEGORY_LABELS: Record<FeedbackCategory, string> = {
  missing_product: 'A product is missing',
  wrong_info: 'A spec is wrong',
  no_match: "I can't find what I need",
  general: 'General feedback',
};

export interface FeedbackContext {
  /** Active route — usually `window.location.pathname + search`. */
  route?: string;
  /** Currently-selected product type, if any. */
  productType?: string | null;
  /** Active filter criteria; serialised inline so the email is readable. */
  filters?: FilterCriterion[];
  /** Optional anchor product reference (manufacturer / part_number). */
  product?: { manufacturer?: string; part_number?: string };
  /** App build identifier — `import.meta.env.VITE_APP_VERSION` if set. */
  appVersion?: string;
}

export interface BuildFeedbackMailtoInput {
  category: FeedbackCategory;
  message: string;
  context?: FeedbackContext;
}

/**
 * Render a filter criterion as a one-line `attribute op value` string
 * — readable in a plain-text email body without forcing the reader
 * to mentally parse JSON. Returns null for empty/no-op filters so the
 * caller can drop them. `mode: 'exclude'` is rendered with a leading
 * `NOT ` so triage doesn't lose the inversion.
 */
function formatFilter(f: FilterCriterion): string | null {
  if (f.mode === 'neutral') return null;
  const op = f.operator ?? '=';
  const value = (() => {
    if (f.value === undefined || f.value === null || f.value === '') return null;
    if (Array.isArray(f.value)) {
      return f.value.length > 0 ? f.value.join(', ') : null;
    }
    if (typeof f.value === 'object') {
      try {
        return JSON.stringify(f.value);
      } catch {
        return null;
      }
    }
    return String(f.value);
  })();
  if (value === null) return null;
  const prefix = f.mode === 'exclude' ? 'NOT ' : '';
  const label = f.displayName || f.attribute;
  return `${prefix}${label} ${op} ${value}`;
}

function buildBody(input: BuildFeedbackMailtoInput): string {
  const lines: string[] = [];
  const message = input.message.trim();
  lines.push(message.length > 0 ? message : '(No additional message)');
  lines.push('');
  lines.push('---');
  lines.push('Sent from the Specodex feedback form.');
  lines.push(`Category: ${FEEDBACK_CATEGORY_LABELS[input.category]}`);

  const ctx = input.context;
  if (ctx) {
    if (ctx.route) lines.push(`Route: ${ctx.route}`);
    if (ctx.productType) lines.push(`Product type: ${ctx.productType}`);
    if (ctx.product) {
      const p = ctx.product;
      const refBits = [p.manufacturer, p.part_number].filter(Boolean).join(' / ');
      if (refBits) lines.push(`Product: ${refBits}`);
    }
    if (ctx.filters && ctx.filters.length > 0) {
      const formatted = ctx.filters
        .map(formatFilter)
        .filter((s): s is string => s !== null);
      if (formatted.length > 0) {
        lines.push('Active filters:');
        for (const f of formatted) lines.push(`  - ${f}`);
      }
    }
    if (ctx.appVersion) lines.push(`App version: ${ctx.appVersion}`);
  }
  return lines.join('\n');
}

function buildSubject(category: FeedbackCategory, productType?: string | null): string {
  const label = FEEDBACK_CATEGORY_LABELS[category];
  const typeBit = productType ? ` (${productType})` : '';
  return `[Specodex] ${label}${typeBit}`;
}

export function buildFeedbackMailto(input: BuildFeedbackMailtoInput): string {
  const subject = buildSubject(input.category, input.context?.productType);
  const body = buildBody(input);
  // encodeURIComponent handles newlines (%0A), spaces, and unicode
  // exactly as RFC 6068 expects for mailto bodies.
  return `mailto:${FEEDBACK_TO}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
