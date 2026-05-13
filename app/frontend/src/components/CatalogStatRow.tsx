/**
 * CatalogStatRow — Bauhaus phase 2b.
 *
 * A 4-cell summary strip beneath the catalog page-toolbar that
 * surfaces a few high-signal aggregate stats over the currently
 * filtered product set. Mirrors the mock's `.stat-row` at
 * docs/design/bauhaus-catalog.html (hard ink borders + 8x8 corner
 * marks + Oswald uppercase labels + tabular numerals).
 *
 * Stat selection is static-per-product-type — declared inline below
 * — rather than computed dynamically by attribute spread. Predictable
 * beats clever here; integrators land on the catalog page expecting
 * the same four signals.
 *
 * Collapsible: persists in localStorage as `bauhaus-stat-row-open`.
 * Defaults to open (the row is the distinctive Bauhaus element of
 * the catalog top). Users who care about table density can collapse
 * the strip; the toggle stays visible.
 */
import { useEffect, useMemo, useState } from 'react';

import type { Product, ProductType } from '../types/models';
import type { ProductTypeLiteral } from '../types/generated';

import './CatalogStatRow.css';

type StatKind =
  | 'numeric-range'
  | 'numeric-median'
  | 'distinct-count'
  | 'distinct-list-count';

interface StatDef {
  label: string;
  field: string;
  kind: StatKind;
  unit?: string;
  accent?: boolean;
}

// Stats are scoped to the two highest-volume product types. Adding a
// new ProductType doesn't break the component — it just doesn't render
// the row (see `STAT_CONFIGS[productType] ?? null` below). Extend this
// map when a type needs its own four signals.
const STAT_CONFIGS: Partial<Record<ProductTypeLiteral, StatDef[]>> = {
  drive: [
    { label: 'Voltage range', field: 'input_voltage', kind: 'numeric-range', unit: 'V' },
    { label: 'Median power', field: 'rated_power', kind: 'numeric-median', unit: 'W' },
    { label: 'Encoder kinds', field: 'encoder_feedback_support', kind: 'distinct-list-count' },
    { label: 'Fieldbus protocols', field: 'fieldbus', kind: 'distinct-list-count', accent: true },
  ],
  motor: [
    { label: 'Torque range', field: 'rated_torque', kind: 'numeric-range', unit: 'Nm' },
    { label: 'Median speed', field: 'rated_speed', kind: 'numeric-median', unit: 'rpm' },
    { label: 'Frame sizes', field: 'frame_size', kind: 'distinct-count' },
    { label: 'Manufacturers', field: 'manufacturer', kind: 'distinct-count', accent: true },
  ],
};

const STORAGE_KEY = 'bauhaus-stat-row-open';

/**
 * Walk a field path against a product record. Supports plain field
 * names (`manufacturer`) and nested paths (`encoder.device`) — the
 * pipeline emits both shapes via the Pydantic models.
 */
function readField(product: Product, field: string): unknown {
  const parts = field.split('.');
  let cur: unknown = product;
  for (const p of parts) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur;
}

/**
 * Pull a numeric "representative" value out of a product field. Field
 * is either:
 *   - a bare number / numeric string,
 *   - a ValueUnit `{value, unit}` — uses `value`,
 *   - a MinMaxUnit `{min, max, unit}` — uses the midpoint (decent
 *     proxy for per-product representative; range-aggregates below
 *     fold min/max separately).
 *
 * Returns null when no usable number is present (placeholder strings,
 * empty objects, etc.).
 */
function numericValue(raw: unknown): number | null {
  if (raw == null) return null;
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (typeof raw === 'string') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  }
  if (typeof raw === 'object') {
    const o = raw as Record<string, unknown>;
    if (typeof o.value === 'number' && Number.isFinite(o.value)) return o.value;
    if (typeof o.min === 'number' && typeof o.max === 'number') {
      return (o.min + o.max) / 2;
    }
  }
  return null;
}

/**
 * For numeric-range stats we want the catalog's true extremes, which
 * means honoring MinMaxUnit's full span (not the midpoint). This
 * yields `{min, max}` across the entire products array; nulls dropped.
 */
function numericExtremes(raw: unknown): { min: number; max: number } | null {
  if (raw == null) return null;
  if (typeof raw === 'number' && Number.isFinite(raw)) return { min: raw, max: raw };
  if (typeof raw === 'object') {
    const o = raw as Record<string, unknown>;
    if (typeof o.min === 'number' && typeof o.max === 'number') {
      return { min: o.min, max: o.max };
    }
    if (typeof o.value === 'number' && Number.isFinite(o.value)) {
      return { min: o.value, max: o.value };
    }
  }
  return null;
}

function formatRange(min: number, max: number, unit?: string): string {
  // Trim trailing zeros from short decimals so "5.00 kW" reads as "5 kW".
  const fmt = (n: number) => {
    if (Number.isInteger(n)) return n.toString();
    return n.toFixed(2).replace(/\.?0+$/, '');
  };
  const range = min === max ? fmt(min) : `${fmt(min)}–${fmt(max)}`;
  return unit ? `${range} ${unit}` : range;
}

function formatScalar(n: number, unit?: string): string {
  const v = Number.isInteger(n) ? n.toString() : n.toFixed(2).replace(/\.?0+$/, '');
  return unit ? `${v} ${unit}` : v;
}

function median(nums: number[]): number {
  const s = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

interface ComputedStat {
  label: string;
  display: string;
  accent: boolean;
}

function computeStat(products: Product[], def: StatDef): ComputedStat | null {
  const values = products
    .map((p) => readField(p, def.field))
    .filter((v) => v !== undefined && v !== null);

  if (values.length === 0) {
    return { label: def.label, display: '—', accent: !!def.accent };
  }

  switch (def.kind) {
    case 'numeric-range': {
      const spans = values.map(numericExtremes).filter((x): x is { min: number; max: number } => x !== null);
      if (spans.length === 0) return { label: def.label, display: '—', accent: !!def.accent };
      const min = Math.min(...spans.map((s) => s.min));
      const max = Math.max(...spans.map((s) => s.max));
      return { label: def.label, display: formatRange(min, max, def.unit), accent: !!def.accent };
    }
    case 'numeric-median': {
      const nums = values.map(numericValue).filter((n): n is number => n !== null);
      if (nums.length === 0) return { label: def.label, display: '—', accent: !!def.accent };
      return { label: def.label, display: formatScalar(median(nums), def.unit), accent: !!def.accent };
    }
    case 'distinct-count': {
      const set = new Set(values.map((v) => String(v).toLowerCase().trim()).filter(Boolean));
      return { label: def.label, display: String(set.size), accent: !!def.accent };
    }
    case 'distinct-list-count': {
      // Field is expected to be array-of-string on each product. Flatten,
      // lowercase, drop empties, count distinct.
      const flat: string[] = [];
      for (const v of values) {
        if (Array.isArray(v)) {
          for (const item of v) {
            if (typeof item === 'string' && item.trim()) flat.push(item.toLowerCase().trim());
          }
        } else if (typeof v === 'string' && v.trim()) {
          flat.push(v.toLowerCase().trim());
        }
      }
      return { label: def.label, display: String(new Set(flat).size), accent: !!def.accent };
    }
  }
}

interface Props {
  products: Product[];
  productType: ProductType | null;
}

export default function CatalogStatRow({ products, productType }: Props) {
  // STAT_CONFIGS is keyed by the seven Pydantic product types only —
  // 'datasheet' / 'all' / null fall through to nothing.
  const config =
    productType && productType !== 'datasheet' && productType !== 'all'
      ? STAT_CONFIGS[productType as ProductTypeLiteral] ?? null
      : null;

  // Persisted open/closed state — read once on mount, write on toggle.
  // Empty string from `localStorage.getItem` means "never set" → default open.
  const [open, setOpen] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored === null ? true : stored === '1';
    } catch {
      return true;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, open ? '1' : '0');
    } catch {
      // localStorage unavailable (private mode, quota, etc.) — non-fatal.
    }
  }, [open]);

  const stats = useMemo(() => {
    if (!config) return [];
    return config.map((def) => computeStat(products, def)).filter((s): s is ComputedStat => s !== null);
  }, [config, products]);

  if (!config) return null;

  return (
    <div className={`catalog-stat-row${open ? '' : ' is-collapsed'}`}>
      <button
        type="button"
        className="catalog-stat-row-toggle"
        aria-expanded={open}
        aria-label={open ? 'Collapse summary statistics' : 'Expand summary statistics'}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="catalog-stat-row-toggle-chev" aria-hidden="true" />
        <span className="catalog-stat-row-toggle-label">
          {open ? 'Summary' : 'Show summary'}
        </span>
      </button>

      {open && (
        <div className="catalog-stat-row-cells" role="group" aria-label="Catalog summary statistics">
          {stats.map((s) => (
            <div className="catalog-stat-cell" key={s.label}>
              <div className="catalog-stat-label">{s.label}</div>
              <div
                className={`catalog-stat-value${s.accent ? ' is-accent' : ''}`}
              >
                {s.display}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
