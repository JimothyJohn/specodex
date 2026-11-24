/**
 * Search service — pure functions for text search, spec filtering, and sorting.
 * Port of cli/query.py search capabilities to TypeScript.
 */

import { Product } from '../types/models';

// Key specs shown in summary views per product type
const SUMMARY_SPECS: Record<string, string[]> = {
  motor: [
    'type', 'rated_power', 'rated_voltage', 'rated_current',
    'rated_speed', 'rated_torque', 'peak_torque',
  ],
  drive: [
    'type', 'rated_power', 'input_voltage', 'rated_current',
    'peak_current', 'fieldbus',
  ],
  gearhead: [
    'gear_ratio', 'gear_type', 'stages', 'rated_torque',
    'backlash', 'efficiency',
  ],
  robot_arm: [
    'payload', 'reach', 'degrees_of_freedom', 'max_tcp_speed',
    'pose_repeatability',
  ],
  inductive_sensor: [
    'type', 'housing_size', 'sensing_distance', 'mounting',
    'output_type', 'output_function', 'supply_voltage',
    'switching_frequency', 'ip_rating',
  ],
};

// Searchable fields with weighted scoring: [field, exactScore, containsScore]
const SEARCHABLE_FIELDS: [string, number, number][] = [
  ['part_number', 100, 80],
  ['product_name', 90, 70],
  ['manufacturer', 85, 60],
  ['series', 50, 40],
  ['product_family', 50, 40],
  ['type', 30, 20],
];

export interface SearchParams {
  products: Product[];
  query?: string;
  manufacturer?: string;
  where?: string[];
  sort?: string[];
  limit?: number;
}

export interface ProductSummary {
  product_id: string;
  product_type: string;
  manufacturer: string;
  product_name?: string;
  part_number?: string;
  relevance?: number;
  [key: string]: unknown;
}

export interface SearchResult {
  count: number;
  products: ProductSummary[];
}

/** Safe field access on the Product union type. */
export function getProductField(product: Product, field: string): unknown {
  return (product as unknown as Record<string, unknown>)[field];
}

/** Pull a comparable number from ValueUnit, MinMaxUnit, plain numbers, or null. */
export function extractNumeric(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') return value;
  if (typeof value === 'string') {
    const parsed = parseFloat(value);
    return isNaN(parsed) ? null : parsed;
  }
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if ('value' in obj && typeof obj.value === 'number') return obj.value;
    if ('min' in obj && typeof obj.min === 'number') return obj.min;
  }
  return null;
}

/** Score a product against a text query. Higher = better. 0 = no match. */
export function textScore(product: Product, query: string): number {
  const q = query.toLowerCase();
  let score = 0;

  for (const [field, exactBonus, containsBonus] of SEARCHABLE_FIELDS) {
    const val = getProductField(product, field);
    if (!val) continue;
    const valLower = String(val).toLowerCase();
    if (valLower === q) {
      score = Math.max(score, exactBonus);
    } else if (valLower.includes(q)) {
      score = Math.max(score, containsBonus);
    } else if (q.includes(valLower)) {
      score = Math.max(score, containsBonus - 10);
    }
  }
  return score;
}

/** Parse "rated_power>=100" into {field, op, value}. */
export function parseWhere(expr: string): { field: string; op: string; value: string } {
  const operators = ['>=', '<=', '!=', '>', '<', '='];
  for (const op of operators) {
    const idx = expr.indexOf(op);
    if (idx > 0) {
      return {
        field: expr.slice(0, idx).trim(),
        op,
        value: expr.slice(idx + op.length).trim(),
      };
    }
  }
  throw new Error(`Cannot parse filter: '${expr}'. Use format: field>value`);
}

/** Parse "rated_power:desc" into {field, reverse}. Default is ascending. */
export function parseSort(expr: string): { field: string; reverse: boolean } {
  if (expr.includes(':')) {
    const [field, direction] = expr.split(':');
    return { field: field.trim(), reverse: direction.trim().toLowerCase().startsWith('d') };
  }
  return { field: expr.trim(), reverse: false };
}

/** Test whether a product passes a single where clause. */
export function applyWhere(product: Product, field: string, op: string, value: string): boolean {
  const productVal = getProductField(product, field);
  if (productVal === null || productVal === undefined) return false;

  const numProduct = extractNumeric(productVal);
  const numFilter = parseFloat(value);
  const isNumeric = !isNaN(numFilter);

  if (numProduct !== null && isNumeric) {
    if (op === '>') return numProduct > numFilter;
    if (op === '<') return numProduct < numFilter;
    if (op === '>=') return numProduct >= numFilter;
    if (op === '<=') return numProduct <= numFilter;
    if (op === '=') return numProduct === numFilter;
    if (op === '!=') return numProduct !== numFilter;
  }

  // String comparison (case-insensitive substring)
  const strProduct = String(productVal).toLowerCase();
  const strFilter = value.toLowerCase();
  if (op === '=') return strProduct.includes(strFilter);
  if (op === '!=') return !strProduct.includes(strFilter);

  return false;
}

/** Multi-level sort. Nulls always sort last. */
export function sortProducts(products: Product[], sortKeys: string[]): Product[] {
  if (!sortKeys.length) return products;
  const parsed = sortKeys.map(parseSort);

  return [...products].sort((a, b) => {
    for (const { field, reverse } of parsed) {
      const aVal = getProductField(a, field);
      const bVal = getProductField(b, field);
      if (aVal == null && bVal == null) continue;
      if (aVal == null) return 1;
      if (bVal == null) return -1;

      const aNum = extractNumeric(aVal);
      const bNum = extractNumeric(bVal);
      let cmp = 0;

      if (aNum !== null && bNum !== null) {
        cmp = aNum - bNum;
      } else {
        const aStr = String(aVal).toLowerCase();
        const bStr = String(bVal).toLowerCase();
        cmp = aStr < bStr ? -1 : aStr > bStr ? 1 : 0;
      }

      if (cmp !== 0) return reverse ? -cmp : cmp;
    }
    return 0;
  });
}

/** Compact summary with identification + key specs for the product type. */
export function productSummary(product: Product, relevance?: number): ProductSummary {
  const ptype = getProductField(product, 'product_type') as string;
  const summary: ProductSummary = {
    product_id: (getProductField(product, 'product_id') as string) || (getProductField(product, 'datasheet_id') as string) || '',
    product_type: ptype,
    manufacturer: (getProductField(product, 'manufacturer') as string) || '',
    product_name: getProductField(product, 'product_name') as string | undefined,
    part_number: getProductField(product, 'part_number') as string | undefined,
  };

  if (relevance !== undefined && relevance > 0) {
    summary.relevance = relevance;
  }

  // Add type-specific key specs
  const specFields = SUMMARY_SPECS[ptype] || [];
  for (const key of specFields) {
    const val = getProductField(product, key);
    if (val !== null && val !== undefined) {
      summary[key] = val;
    }
  }

  return summary;
}

/** Orchestrator: text score + filter + sort + limit. */
export function searchProducts(params: SearchParams): SearchResult {
  let { products } = params;
  const { query, manufacturer, where, sort, limit = 20 } = params;

  // Filter by manufacturer
  if (manufacturer) {
    const mfg = manufacturer.toLowerCase();
    products = products.filter(p => {
      const val = getProductField(p, 'manufacturer');
      return val && String(val).toLowerCase().includes(mfg);
    });
  }

  // Apply where clauses
  if (where?.length) {
    for (const expr of where) {
      const { field, op, value } = parseWhere(expr);
      products = products.filter(p => applyWhere(p, field, op, value));
    }
  }

  // Text search scoring
  let scored: Array<{ product: Product; score: number }>;
  if (query) {
    scored = products
      .map(p => ({ product: p, score: textScore(p, query) }))
      .filter(s => s.score > 0)
      .sort((a, b) => b.score - a.score);
  } else {
    scored = products.map(p => ({ product: p, score: 0 }));
  }

  // Sort (applied after text scoring if both present)
  if (sort?.length) {
    const sortedProducts = sortProducts(scored.map(s => s.product), sort);
    scored = sortedProducts.map(p => ({
      product: p,
      score: scored.find(s => s.product === p)?.score || 0,
    }));
  }

  // Limit
  const clamped = Math.min(Math.max(limit, 1), 100);
  const limited = scored.slice(0, clamped);

  return {
    count: limited.length,
    products: limited.map(s => productSummary(s.product, query ? s.score : undefined)),
  };
}
