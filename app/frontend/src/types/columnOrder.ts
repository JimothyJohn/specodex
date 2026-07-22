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
//   `msrp` (Price) and `availability` (Lead Time) lead every type right
//   after `manufacturer`. They're the two buyer-facing commercial columns
//   and are forced default-visible in filters.ts (commercialAttributes).
//   `availability` is the honest lead-time signal — the numeric `lead_time`
//   field has no populator, so the populated stock snapshot stands in for it.
//
// =============================================================================

import type { ProductType } from './models';
import type { AttributeMetadata } from './filters';

export const COLUMN_ORDER: Partial<
  Record<Exclude<ProductType, null | 'all'>, string[]>
> = {
  motor: [
    'manufacturer',
    'msrp',
    'availability',
    'rated_power',
    'rated_torque',
    'rated_speed',
    'rated_voltage',
    'rated_current',
  ],
  drive: [
    'manufacturer',
    'msrp',
    'availability',
    'rated_power',
    'input_voltage',
    'input_voltage_phases',
    'rated_current',
    'peak_current',
  ],
  robot_arm: [
    'manufacturer',
    'msrp',
    'availability',
    // e.g. 'payload', 'reach', 'degrees_of_freedom', 'pose_repeatability', 'max_tcp_speed',
  ],
  gearhead: [
    'manufacturer',
    'msrp',
    'availability',
    // e.g. 'gear_ratio', 'gear_type', 'rated_torque', 'peak_torque', 'backlash', 'efficiency',
  ],
  contactor: [
    'manufacturer',
    'msrp',
    'availability',
    // e.g. 'ie_ac3_400v', 'motor_power_ac3_400v_kw', 'motor_power_ac3_480v_hp',
  ],
  electric_cylinder: [
    'manufacturer',
    'msrp',
    'availability',
    // e.g. 'stroke', 'max_push_force', 'continuous_force', 'max_linear_speed', 'rated_voltage',
  ],
  linear_actuator: [
    'manufacturer',
    'msrp',
    'availability',
    // Derivation-only type (no static getXxxAttributes list) — its spec
    // columns auto-populate from records. The leading three still pin
    // here so Price + Lead Time stay far-left, not alphabetized adrift.
  ],
  datasheet: [
    'manufacturer',
    // e.g. 'product_name', 'product_family', 'component_type',
  ],
};

// Fallback leading order for any concrete product type that has NO
// explicit COLUMN_ORDER entry above (e.g. a future type added by dropping
// a model file + records, per the "auto-populate" convention). Without
// this, an unlisted type alphabetizes every column and Price + Lead Time
// scatter — exactly the linear_actuator bug caught on 2026-06-13. Keeps
// the two commercial columns pinned far-left universally.
//
// Note `?? DEFAULT_LEADING_ORDER` only fires on `undefined` (a missing
// key). An *explicitly empty* entry (`type: []`) still means "pure
// alphabetical" and is preserved.
export const DEFAULT_LEADING_ORDER = ['manufacturer', 'msrp', 'availability'];

// =============================================================================
// Commercial ⇄ Technical balance (todo/COMMERCIAL.md Phase 3)
// =============================================================================
//
// The balance control reorders the *commercial band* relative to the
// technical columns; it extends the PR #285 pinning mechanism above
// rather than introducing a parallel ordering path.
//
//   commercial: band first (price, lead time, warranty), then
//               manufacturer, then the type's authored technical order.
//               ProductList also seeds a price-ascending default sort.
//   balanced:   today's default — manufacturer, msrp, availability, ….
//   technical:  technical-first; the commercial band trails last, after
//               even the unlisted alphabetical spill.

export type ColumnBalance = 'commercial' | 'balanced' | 'technical';

export const isColumnBalance = (v: string): v is ColumnBalance =>
  v === 'commercial' || v === 'balanced' || v === 'technical';

/**
 * The buyer-facing commercial columns, in their pinned order. `msrp`
 * renders as Price, `availability` as Lead Time (the honest stock
 * signal); `lead_time` / `warranty` join the band whenever records
 * carry them (they're derivation-only today).
 */
export const COMMERCIAL_BAND_KEYS = [
  'msrp',
  'availability',
  'lead_time',
  'warranty',
] as const;

/**
 * Order attributes for table rendering: authored COLUMN_ORDER keys first
 * (in declared order), then unlisted keys alphabetical by displayName.
 * `balance` shifts the commercial band per the table above.
 */
export const orderColumnAttributes = (
  attrs: AttributeMetadata[],
  productType: ProductType,
  balance: ColumnBalance = 'balanced',
): AttributeMetadata[] => {
  const authored =
    productType && productType !== 'all'
      ? COLUMN_ORDER[productType] ?? DEFAULT_LEADING_ORDER
      : [];
  const band = new Set<string>(COMMERCIAL_BAND_KEYS);

  let order: string[];
  let trailing: string[] = [];
  if (balance === 'commercial') {
    order = [
      ...COMMERCIAL_BAND_KEYS,
      'manufacturer',
      ...authored.filter((k) => !band.has(k) && k !== 'manufacturer'),
    ];
  } else if (balance === 'technical') {
    order = authored.filter((k) => !band.has(k));
    trailing = [...COMMERCIAL_BAND_KEYS];
  } else {
    order = [...authored];
  }

  const indexOf = new Map(order.map((k, i) => [k, i] as const));
  const trailIndexOf = new Map(trailing.map((k, i) => [k, i] as const));
  // Rank tiers: 0 = explicitly ordered, 1 = unlisted (alphabetical),
  // 2 = trailing commercial band (technical mode only).
  const rankOf = (key: string): number =>
    trailIndexOf.has(key) ? 2 : indexOf.has(key) ? 0 : 1;

  return [...attrs].sort((a, b) => {
    const ar = rankOf(a.key);
    const br = rankOf(b.key);
    if (ar !== br) return ar - br;
    if (ar === 0) return indexOf.get(a.key)! - indexOf.get(b.key)!;
    if (ar === 2) return trailIndexOf.get(a.key)! - trailIndexOf.get(b.key)!;
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
 *   5. `nested === true` → in (ValueUnit/MinMaxUnit default)
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
export const computeVisibleColumnAttributes = (
  columnAttributes: AttributeMetadata[],
  userHiddenKeys: readonly string[],
  userRestoredKeys: readonly string[],
  maxVisible: number,
): AttributeMetadata[] => {
  const hiddenSet = new Set(userHiddenKeys);
  const restoredSet = new Set(userRestoredKeys);

  const wouldBeShown = columnAttributes.filter(a => {
    if (hiddenSet.has(a.key)) return false;
    if (restoredSet.has(a.key)) return true;
    if (a.defaultVisible === true) return true;
    if (a.defaultVisible === false) return false;
    return a.nested === true;
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
