/**
 * One-line, human-readable summary of a FilterCriterion — the text on a
 * column header's filter trigger at rest and on the active-filter chips
 * above the table (UI_CLEANUP Phase 2, popover-per-column).
 *
 * Pure: no React, no DOM. Numeric thresholds are rendered in the
 * column's display unit system via the same unit helpers the header
 * uses, so "≥ 10 Nm" and "≥ 88.5 lbf·in" describe the same stored
 * canonical value.
 */

import type { FilterCriterion, ComparisonOperator } from '../types/filters';
import type { Product } from '../types/models';
import { type UnitSystem, toDisplay, displayUnit, isIntegerUnit } from './unitConversion';

const OP_SYMBOL: Record<ComparisonOperator, string> = {
  '>=': '≥',
  '<=': '≤',
  '>': '>',
  '<': '<',
  '=': '=',
  '!=': '≠',
};

/** True when the criterion actually constrains rows. Seeded default
 *  chips land valueless ("any") and must not read as active. */
export function filterHasValue(f: FilterCriterion): boolean {
  const v = f.value;
  if (v === undefined || v === null) return false;
  if (typeof v === 'string') return v !== '';
  if (Array.isArray(v)) return v.length > 0;
  return true;
}

/**
 * Compact numeric formatting for thresholds. Integer-like units (rpm,
 * V, W) round; otherwise precision steps down with magnitude so a 0.037
 * Nm torque floor doesn't collapse to "0.0" the way a flat toFixed(1)
 * did. Non-finite input renders as "—" rather than "NaN".
 */
export function formatCompactNumber(v: number, unit = ''): string {
  if (!Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  if ((unit && isIntegerUnit(unit)) || abs >= 100) return Math.round(v).toLocaleString();
  if (abs >= 10) return trimZeros(v.toFixed(1));
  if (abs >= 1) return trimZeros(v.toFixed(2));
  if (abs === 0) return '0';
  return trimZeros(v.toPrecision(2));
}

const trimZeros = (s: string): string =>
  s.includes('.') ? s.replace(/\.?0+$/, '') : s;

/** Render a canonical numeric value in the display unit system with its
 *  unit suffix, e.g. (10, 'Nm', 'imperial') → "88.5 lbf·in". */
export function formatThreshold(
  canonical: number,
  unit: string | undefined,
  unitSystem: UnitSystem,
): string {
  if (!unit) return formatCompactNumber(canonical);
  const shown = toDisplay(canonical, unit, unitSystem);
  const u = displayUnit(unit, unitSystem);
  return `${formatCompactNumber(shown, unit)}${u ? ' ' + u : ''}`;
}

export interface SummaryOptions {
  /** Canonical unit of the attribute (from metadata or sniffed off the
   *  rows). Omit for unitless / categorical attributes. */
  unit?: string;
  unitSystem: UnitSystem;
  /** Max listed categorical values before collapsing to "N values". */
  maxListed?: number;
}

/**
 * Summarise a criterion. Returns "any" for a valueless entry so the
 * header trigger can render it directly.
 */
export function summarizeFilter(f: FilterCriterion, opts: SummaryOptions): string {
  if (!filterHasValue(f)) return 'any';
  const v = f.value as NonNullable<FilterCriterion['value']>;
  const exclude = f.mode === 'exclude';
  const maxListed = opts.maxListed ?? 2;

  if (typeof v === 'number') {
    const op = OP_SYMBOL[(f.operator ?? '>=') as ComparisonOperator] ?? f.operator ?? '';
    const shown = formatThreshold(v, opts.unit, opts.unitSystem);
    return exclude ? `not ${op} ${shown}` : `${op} ${shown}`;
  }
  if (typeof v === 'boolean') {
    return exclude ? (v ? 'no' : 'yes') : (v ? 'yes' : 'no');
  }
  if (typeof v === 'string') {
    return exclude ? `≠ ${v}` : v;
  }
  // Arrays: numeric pair tuple or categorical list.
  if (Array.isArray(v)) {
    if (
      v.length === 2 &&
      typeof v[0] === 'number' &&
      typeof v[1] === 'number' &&
      (f.operator === undefined || f.operator === '=')
    ) {
      // [lo, hi] range tuple — only when the operator doesn't say otherwise.
      const lo = formatThreshold(v[0], opts.unit, opts.unitSystem);
      const hi = formatThreshold(v[1], opts.unit, opts.unitSystem);
      return `${exclude ? 'not ' : ''}${lo} – ${hi}`;
    }
    const items = v.map(String);
    const body = items.length <= maxListed ? items.join(', ') : `${items.length} values`;
    return exclude ? `not ${body}` : body;
  }
  return String(v);
}

/** Unit of an attribute as the rows actually carry it — the first
 *  `{value|min, unit}` object found on `key`. Bounded scan. */
export function sniffAttributeUnit(products: Product[], key: string, limit = 200): string | undefined {
  const n = Math.min(products.length, limit);
  for (let i = 0; i < n; i++) {
    const raw = (products[i] as unknown as Record<string, unknown>)[key];
    if (raw && typeof raw === 'object' && 'unit' in raw) {
      const u = (raw as { unit?: unknown }).unit;
      if (typeof u === 'string' && u) return u;
    }
  }
  return undefined;
}
