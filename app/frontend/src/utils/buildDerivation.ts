/**
 * Build's derivation engine — translates physically-intuitive
 * requirements ("100 mm in 200 ms") into the numeric constraints the
 * relations API needs ("peak force >= N", "peak velocity >= mm/s").
 * See todo/BUILD.md Part 2 "The derivation engine".
 *
 * Assumed motion profile: S-curve 1/3 trapezoidal. One-third of
 * `move_time` accelerates (0 -> peak velocity), one-third cruises,
 * one-third decelerates. At this fidelity the S-curve smoothing does
 * not change the peak numbers, only the jerk; Day 1 ignores jerk.
 *
 * Inputs are assumed to already be in canonical units (stroke mm,
 * times s, payload kg) — unit conversion happens at the form boundary,
 * not here. Any field that is missing, non-positive where positivity
 * is required, or non-finite yields a `null` derived value, matching
 * the "blank = no constraint applied" narrowing rule.
 */

import type { ValueUnit } from '../types/generated';
import type { BuildRequirements } from '../types/buildRequirements';

/** Standard gravity (m/s^2) applied to vertical orientations. */
export const GRAVITY = 9.81;

export interface DerivedMotion {
  /** Mean carriage velocity over the move. Unit: mm/s. */
  avg_velocity: ValueUnit | null;
  /** Peak (cruise) velocity = 1.5 x average. Unit: mm/s. */
  peak_velocity: ValueUnit | null;
  /** Peak acceleration during the accel third. Unit: m/s^2. */
  peak_acceleration: ValueUnit | null;
  /** Peak thrust the actuator must provide. Unit: N. */
  peak_force: ValueUnit | null;
  /** Move time / (move time + dwell time). Dimensionless, 0..1. */
  duty_cycle: number | null;
}

/** Extract a finite numeric value from a ValueUnit, else null. */
function value(vu: ValueUnit | null | undefined): number | null {
  if (!vu || typeof vu.value !== 'number' || !Number.isFinite(vu.value)) {
    return null;
  }
  return vu.value;
}

/**
 * Derive peak motion quantities from a BuildRequirements state.
 *
 * Day 1 reads only `motion_profiles[0]`; later phases sum across the
 * list for cumulative duty cycle (BUILD.md forward-compat hooks).
 */
export function deriveMotion(requirements: BuildRequirements): DerivedMotion {
  const empty: DerivedMotion = {
    avg_velocity: null,
    peak_velocity: null,
    peak_acceleration: null,
    peak_force: null,
    duty_cycle: null,
  };

  const profile = requirements.motion_profiles[0];
  if (!profile) {
    return empty;
  }

  const stroke = value(profile.stroke);
  const moveTime = value(profile.move_time);
  const dwellTime = value(profile.dwell_time);
  const payload = value(requirements.payload_mass);

  const result: DerivedMotion = { ...empty };

  // Velocity + acceleration need a real, forward move in finite time.
  if (stroke !== null && stroke > 0 && moveTime !== null && moveTime > 0) {
    const avgVelocity = stroke / moveTime; // mm/s
    const peakVelocity = 1.5 * avgVelocity; // mm/s
    // 4.5 x stroke / move_time^2 gives mm/s^2; /1000 -> m/s^2.
    const peakAccelMs2 = (4.5 * stroke) / (moveTime * moveTime) / 1000;

    result.avg_velocity = { value: avgVelocity, unit: 'mm/s' };
    result.peak_velocity = { value: peakVelocity, unit: 'mm/s' };
    result.peak_acceleration = { value: peakAccelMs2, unit: 'm/s²' };

    // Peak force needs payload and a known gravity contribution.
    // Orientation null = gravity unknown = no force filter (matches
    // BUILD.md's "set move time to compute peak force" narrowing copy,
    // extended to orientation).
    if (payload !== null && payload >= 0 && requirements.orientation !== null) {
      const gFactor = requirements.orientation === 'vertical' ? GRAVITY : 0;
      const peakForce = payload * (peakAccelMs2 + gFactor); // N
      result.peak_force = { value: peakForce, unit: 'N' };
    }
  }

  // Duty cycle: move time / (move time + dwell time).
  if (
    moveTime !== null &&
    moveTime > 0 &&
    dwellTime !== null &&
    dwellTime >= 0
  ) {
    result.duty_cycle = moveTime / (moveTime + dwellTime);
  }

  return result;
}
