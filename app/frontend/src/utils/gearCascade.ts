/**
 * Motor gearbox cascade — "what would this motor deliver behind a gearbox?"
 *
 * Rated Torque / Peak Torque / Rated Speed always show the motor's own
 * shaft values. Gearing lives in two computed, separately labelled
 * columns:
 *
 *   output_torque  = rated_torque × ratio   ("Output Torque (with gearbox)")
 *   output_speed   = rated_speed  ÷ ratio   ("Output Speed (with gearbox)")
 *
 * `ratio` is picked per row from a `>=` / `>` filter on `output_torque`:
 * the smallest discrete ratio that lifts rated torque to the target. With
 * no such filter every ratio is 1 and the output columns equal the rated
 * values.
 *
 * Why the split: before 2026-11-24 the cascade rewrote `rated_torque`
 * and `rated_speed` in place. The cells and the filter then read geared
 * values while the column header's histogram and slider read the raw
 * rows, so "Rated Torque ≥ 30000 Nm" returned motors whose datasheet
 * rated torque is ~300 Nm, labelled as rated torque. Every surface (cell,
 * header histogram, slider, filter, sort) now reads the same field, and
 * the field's name says which quantity it is.
 */

import type { FilterCriterion } from '../types/filters';
import type { Product, ProductType } from '../types/models';
import { numericFromValue } from './filterValues';

export const OUTPUT_TORQUE_KEY = 'output_torque';
export const OUTPUT_SPEED_KEY = 'output_speed';
/** Columns whose values are post-gear while the cascade is active. */
export const GEARED_KEYS: readonly string[] = [OUTPUT_TORQUE_KEY, OUTPUT_SPEED_KEY];

// Discrete gear ratios. Direct drive plus the ratios gearhead catalogs
// cluster around; a finer grid would give false precision. Snap *up*
// (next ratio that is enough to meet the target) rather than rounding,
// so the shown output torque never overstates what the motor delivers.
export const GEAR_OPTIONS: readonly number[] = [1, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 48, 64, 80, 100];

export function snapGearUp(raw: number): number {
  for (const g of GEAR_OPTIONS) {
    if (g >= raw) return g;
  }
  return GEAR_OPTIONS[GEAR_OPTIONS.length - 1];
}

/** Scale a ValueUnit / MinMaxUnit-shaped spec by `factor` (6 sig figs; factor 1 is identity). */
export function scaleSpec(value: unknown, factor: number): unknown {
  if (!value || typeof value !== 'object' || factor === 1) return value;
  const v = value as Record<string, unknown>;
  const scale = (n: unknown) =>
    typeof n === 'number' ? parseFloat((n * factor).toPrecision(6)) : n;
  if ('value' in v && typeof v.value === 'number') {
    return { ...v, value: scale(v.value) };
  }
  if ('min' in v || 'max' in v) {
    return { ...v, min: scale(v.min), max: scale(v.max) };
  }
  return value;
}

/** Output-torque targets from `>=` / `>` filters with a positive value. */
export function outputTorqueTargets(filters: readonly FilterCriterion[]): number[] {
  const out: number[] = [];
  for (const f of filters) {
    if (f.attribute !== OUTPUT_TORQUE_KEY) continue;
    if (f.operator !== '>=' && f.operator !== '>') continue;
    const v = numericFromValue(f.value);
    if (v == null || !Number.isFinite(v) || v <= 0) continue;
    out.push(v);
  }
  return out;
}

export interface GearCascadeResult {
  /** Rows with `output_torque` / `output_speed` attached (motor view only). */
  products: Product[];
  /** product_id → picked ratio; empty when no output-torque target is set. */
  gearMap: Map<string, number>;
  /** True while an output-torque target is driving ratios above 1. */
  active: boolean;
}

/**
 * Attach the computed output columns to every motor row.
 *
 * Never mutates or rewrites the input rows' own fields: `rated_torque`,
 * `peak_torque` and `rated_speed` come through untouched.
 */
export function applyGearCascade(
  products: readonly Product[],
  filters: readonly FilterCriterion[],
  productType: ProductType,
): GearCascadeResult {
  const gearMap = new Map<string, number>();
  if (productType !== 'motor') {
    return { products: products as Product[], gearMap, active: false };
  }
  const targets = outputTorqueTargets(filters);
  const active = targets.length > 0;
  const next = products.map(p => {
    const row = p as unknown as Record<string, unknown>;
    const motorTorque = numericFromValue(row.rated_torque);
    let ratio = 1;
    if (active && motorTorque != null && motorTorque > 0) {
      for (const target of targets) {
        if (motorTorque >= target) continue;
        const needed = snapGearUp(target / motorTorque);
        if (needed > ratio) ratio = needed;
      }
    }
    if (active) gearMap.set(p.product_id, ratio);
    return {
      ...row,
      [OUTPUT_TORQUE_KEY]: row.rated_torque ? scaleSpec(row.rated_torque, ratio) : undefined,
      [OUTPUT_SPEED_KEY]: row.rated_speed ? scaleSpec(row.rated_speed, 1 / ratio) : undefined,
    } as unknown as Product;
  });
  return { products: next, gearMap, active };
}
