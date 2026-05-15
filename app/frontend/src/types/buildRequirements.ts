/**
 * Build's requirement vocabulary — the single load-bearing schema for
 * the Build page (todo/BUILD.md Part 2).
 *
 * Form inputs, URL state, localStorage saves, and the future Wizard
 * handoff target all bind to this shape. It is never sent to the
 * relations API directly — `buildDerivation.ts` translates it into the
 * numeric constraints the API consumes.
 *
 * Note on nullability: BUILD.md's Part 2 code block types the
 * MotionProfile fields as bare `ValueUnit`, but the field-by-field
 * table and the "blank = no constraint applied" narrowing rule both
 * require an unfilled (null) starting state. They are modelled as
 * `ValueUnit | null` here to match the empty-form behaviour the rest
 * of the doc describes.
 */

import type { ValueUnit } from './generated';

export type MotionClass = 'linear' | 'rotary';
export type LinearOrientation = 'horizontal' | 'vertical';
export type UnitsPreference = 'metric' | 'imperial';

/**
 * One discrete move plus its trailing dwell. Day 1 ships with exactly
 * one entry; the schema is a list from the start so the future
 * "stack profiles to compute cumulative duty cycle" feature is
 * additive rather than a re-shape.
 */
export interface MotionProfile {
  /** Distance the carriage must travel in this move. Canonical unit: mm. */
  stroke: ValueUnit | null;
  /** How long the move takes (start of motion to end). Canonical unit: s. */
  move_time: ValueUnit | null;
  /** Idle time between this move and the next. Canonical unit: s. */
  dwell_time: ValueUnit | null;
}

export interface BuildRequirements {
  /** null = user hasn't picked yet. */
  motion_class: MotionClass | null;
  /** Linear-only. null when motion_class !== "linear". */
  orientation: LinearOrientation | null;
  /**
   * Strongly suggested for linear (not enforced). The form labels it
   * required; the validator allows null. 0 is a legal value and emits
   * a warning rather than blocking. Canonical unit: kg.
   */
  payload_mass: ValueUnit | null;
  /** Day 1: length 1 enforced. */
  motion_profiles: MotionProfile[];
  /** Display-only; persisted in localStorage. Default "metric". */
  units_preference: UnitsPreference;
}

/** Empty-form defaults — the fallback state on a fresh `/build` load. */
export function emptyBuildRequirements(): BuildRequirements {
  return {
    motion_class: null,
    orientation: null,
    payload_mass: null,
    motion_profiles: [{ stroke: null, move_time: null, dwell_time: null }],
    units_preference: 'metric',
  };
}
