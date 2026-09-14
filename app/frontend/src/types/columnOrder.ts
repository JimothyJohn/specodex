// =============================================================================
//
//                    >>>  EDIT THIS FILE TO REORDER COLUMNS  <<<
//
//   Single source of truth for the **default initial** column order in the
//   results table. Every visitor's first paint shows exactly this order.
//   Users can drag column headers to rearrange within a session — those
//   tweaks are session-only (no localStorage) so refreshing the page
//   restores this canonical view. Edit this file to change what every
//   visitor sees on first load.
//
//   How to use:
//     1. Find the product type below (motor, drive, contactor, ...).
//     2. Edit its array — keys appear in the table left-to-right in this order.
//     3. Save. The dev server hot-reloads. Ship via the usual deploy.
//
//   What the keys are:
//     They're the snake_case attribute keys from the product schemas in
//     specodex/models/<type>.py — same keys the API returns.
//     E.g. for motors: 'rated_power', 'rated_torque', 'rated_speed', ...
//     Look at app/frontend/src/types/filters.ts (getMotorAttributes etc.)
//     for the full list per type.
//
//   What happens to keys you DON'T list:
//     They fall through to alphabetical at the end of the row, so a new
//     field added to a schema never silently disappears — you just see it
//     trailing the authored columns until you decide where it belongs.
//
//   `part_number` is pinned as the leading column by ProductList.tsx and
//   is excluded from this list — don't put it here.
//
// =============================================================================

import type { ProductType } from './models';
import type { AttributeMetadata } from './filters';

export const COLUMN_ORDER: Partial<
  Record<Exclude<ProductType, null | 'all'>, string[]>
> = {
  motor: [
    'manufacturer',
    'rated_power',
    'rated_torque',
    'rated_speed',
    'rated_voltage',
    'rated_current',
  ],
  drive: [
    'manufacturer',
    'rated_power',
    'input_voltage',
    'input_voltage_phases',
    'rated_current',
    'peak_current',
  ],
  robot_arm: [
    'manufacturer',
    // e.g. 'payload', 'reach', 'degrees_of_freedom', 'pose_repeatability', 'max_tcp_speed',
  ],
  gearhead: [
    'manufacturer',
    // e.g. 'gear_ratio', 'gear_type', 'rated_torque', 'peak_torque', 'backlash', 'efficiency',
  ],
  contactor: [
    'manufacturer',
    // e.g. 'ie_ac3_400v', 'motor_power_ac3_400v_kw', 'motor_power_ac3_480v_hp',
  ],
  electric_cylinder: [
    'manufacturer',
    // e.g. 'stroke', 'max_push_force', 'continuous_force', 'max_linear_speed', 'rated_voltage',
  ],
  linear_actuator: [
    'manufacturer',
    // Derivation-only type (no static getXxxAttributes list) — its spec
    // columns auto-populate from records. `manufacturer` still pins here
    // so it stays far-left, not alphabetized adrift.
  ],
  datasheet: [
    'manufacturer',
    // e.g. 'product_name', 'product_family', 'component_type',
  ],
};

// Fallback leading order for any concrete product type that has NO
// explicit COLUMN_ORDER entry above (e.g. a future type added by dropping
// a model file + records, per the "auto-populate" convention). Without
// this, an unlisted type alphabetizes every column — exactly the
// linear_actuator bug caught on 2026-06-13.
//
// Note `?? DEFAULT_LEADING_ORDER` only fires on `undefined` (a missing
// key). An *explicitly empty* entry (`type: []`) still means "pure
// alphabetical" and is preserved.
export const DEFAULT_LEADING_ORDER = ['manufacturer'];

/**
 * Order attributes for table rendering: authored COLUMN_ORDER keys first
 * (in declared order), then unlisted keys alphabetical by displayName.
 */
export const orderColumnAttributes = (
  attrs: AttributeMetadata[],
  productType: ProductType,
): AttributeMetadata[] => {
  const order =
    productType && productType !== 'all'
      ? COLUMN_ORDER[productType] ?? DEFAULT_LEADING_ORDER
      : [];

  const indexOf = new Map(order.map((k, i) => [k, i] as const));

  return [...attrs].sort((a, b) => {
    const aListed = indexOf.has(a.key);
    const bListed = indexOf.has(b.key);
    if (aListed !== bListed) return aListed ? -1 : 1;
    if (aListed) return indexOf.get(a.key)! - indexOf.get(b.key)!;
    return a.displayName.localeCompare(b.displayName);
  });
};

/**
 * Compute the columns the table actually renders, given the full attribute
 * list (pre-ordered by `orderColumnAttributes`), the user's hide/restore
 * choices, and the cap.
 *
 * Visibility rules (per-attribute, applied in order):
 *   1. user explicitly hid it → out
 *   2. user explicitly restored it → in (always — explicit-add contract)
 *   3. `defaultVisible === true`  → in (per-attribute expert override)
 *   4. `defaultVisible === false` → out (per-attribute expert override)
 *   5. `nested === true` → in (ValueUnit/MinMaxUnit default) — **but only
 *      when the column is actually populated**: with `fillRates`
 *      supplied, a nested column whose fill rate over the loaded rows is
 *      below `minFillRate` is out. Axial Load Force Rating was ~90 %
 *      empty on motors and still took the widest default slot
 *      (UI_CLEANUP S2). Explicit `defaultVisible: true` (rule 3) and
 *      user restores (rule 2) are unaffected — curation and intent both
 *      outrank the heuristic. A key missing from `fillRates` counts as
 *      fully populated so the rule can't hide something it hasn't
 *      measured.
 *   6. otherwise → out (strings, booleans, arrays, bare numbers are
 *      hidden by default; user has to opt into them)
 *
 * Then a cap: at most `maxVisible` columns render. **User-restored
 * columns always render, even when they exceed the cap.** Without this
 * carve-out, "Add spec" silently drops the user's column on the floor
 * whenever the default-visible set already fills the cap — which is the
 * common case (motor has 8 default-visibles, cap is 10 cozy). Bug
 * reported 2026-05-23.
 */
export const DEFAULT_MIN_FILL_RATE = 0.25;

export const computeVisibleColumnAttributes = (
  columnAttributes: AttributeMetadata[],
  userHiddenKeys: readonly string[],
  userRestoredKeys: readonly string[],
  maxVisible: number,
  fillRates?: ReadonlyMap<string, number>,
  minFillRate: number = DEFAULT_MIN_FILL_RATE,
): AttributeMetadata[] => {
  const hiddenSet = new Set(userHiddenKeys);
  const restoredSet = new Set(userRestoredKeys);
  const fillOf = (key: string): number => fillRates?.get(key) ?? 1;

  const wouldBeShown = columnAttributes.filter(a => {
    if (hiddenSet.has(a.key)) return false;
    if (restoredSet.has(a.key)) return true;
    if (a.defaultVisible === true) return true;
    if (a.defaultVisible === false) return false;
    return a.nested === true && fillOf(a.key) >= minFillRate;
  });

  // Partition: explicit restores always render; default-visibles fill
  // remaining slots up to the cap.
  const explicit = wouldBeShown.filter(a => restoredSet.has(a.key));
  const others = wouldBeShown.filter(a => !restoredSet.has(a.key));
  const remaining = Math.max(0, maxVisible - explicit.length);
  const survivingKeys = new Set<string>([
    ...explicit.map(a => a.key),
    ...others.slice(0, remaining).map(a => a.key),
  ]);

  // Preserve the input order — callers rely on `orderColumnAttributes`
  // having already sorted by COLUMN_ORDER.
  return wouldBeShown.filter(a => survivingKeys.has(a.key));
};

/**
 * Fraction of `records` carrying a non-empty value for each key in
 * `keys`. Empty = `null` / `undefined` / `''` / `[]` / a ValueUnit or
 * MinMaxUnit object with no numeric `value` / `min` / `max`. With no
 * records every key reports 1 (nothing measured → nothing hidden).
 *
 * One pass over the rows regardless of key count; called from a memo in
 * ProductList whenever the loaded set changes.
 */
export const computeFillRates = (
  records: readonly Record<string, unknown>[],
  keys: readonly string[],
): Map<string, number> => {
  const out = new Map<string, number>();
  if (records.length === 0) {
    for (const k of keys) out.set(k, 1);
    return out;
  }
  const counts = new Map<string, number>(keys.map(k => [k, 0]));
  for (const r of records) {
    for (const k of keys) {
      if (isFilled(r[k])) counts.set(k, (counts.get(k) ?? 0) + 1);
    }
  }
  for (const k of keys) out.set(k, (counts.get(k) ?? 0) / records.length);
  return out;
};

const isFilled = (v: unknown): boolean => {
  if (v === null || v === undefined || v === '') return false;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === 'object') {
    const o = v as { value?: unknown; min?: unknown; max?: unknown };
    if ('value' in o || 'min' in o || 'max' in o) {
      return (
        typeof o.value === 'number' || typeof o.min === 'number' || typeof o.max === 'number'
      );
    }
    return Object.keys(o).length > 0;
  }
  return true;
};
