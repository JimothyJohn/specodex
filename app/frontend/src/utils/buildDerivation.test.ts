/**
 * Tests for the Build derivation engine (todo/BUILD.md Part 2).
 */

import { describe, it, expect } from 'vitest';
import { deriveMotion, GRAVITY } from './buildDerivation';
import {
  emptyBuildRequirements,
  type BuildRequirements,
} from '../types/buildRequirements';

/** Build a requirements object with a single motion profile. */
function reqs(overrides: Partial<BuildRequirements> = {}): BuildRequirements {
  return { ...emptyBuildRequirements(), ...overrides };
}

describe('deriveMotion — empty form', () => {
  it('derives nothing when every field is unset', () => {
    const d = deriveMotion(emptyBuildRequirements());
    expect(d.avg_velocity).toBeNull();
    expect(d.peak_velocity).toBeNull();
    expect(d.peak_acceleration).toBeNull();
    expect(d.peak_force).toBeNull();
    expect(d.duty_cycle).toBeNull();
  });

  it('returns all-null when motion_profiles is empty', () => {
    const d = deriveMotion(reqs({ motion_profiles: [] }));
    expect(d.peak_velocity).toBeNull();
    expect(d.duty_cycle).toBeNull();
  });
});

describe('deriveMotion — velocity and acceleration', () => {
  it('derives velocity/acceleration from stroke + move_time alone', () => {
    const d = deriveMotion(
      reqs({
        motion_profiles: [
          {
            stroke: { value: 200, unit: 'mm' },
            move_time: { value: 0.2, unit: 's' },
            dwell_time: null,
          },
        ],
      }),
    );
    // avg = 200 / 0.2 = 1000 mm/s; peak = 1.5 x avg = 1500 mm/s.
    expect(d.avg_velocity).toEqual({ value: 1000, unit: 'mm/s' });
    expect(d.peak_velocity).toEqual({ value: 1500, unit: 'mm/s' });
    // 4.5 x 200 / 0.2^2 = 22500 mm/s^2 = 22.5 m/s^2.
    expect(d.peak_acceleration?.value).toBeCloseTo(22.5, 6);
    expect(d.peak_acceleration?.unit).toBe('m/s²');
  });

  it('omits velocity/acceleration without move_time', () => {
    const d = deriveMotion(
      reqs({
        motion_profiles: [
          { stroke: { value: 200, unit: 'mm' }, move_time: null, dwell_time: null },
        ],
      }),
    );
    expect(d.avg_velocity).toBeNull();
    expect(d.peak_acceleration).toBeNull();
  });

  it('omits velocity for non-positive move_time (no division by zero)', () => {
    const d = deriveMotion(
      reqs({
        motion_profiles: [
          {
            stroke: { value: 200, unit: 'mm' },
            move_time: { value: 0, unit: 's' },
            dwell_time: null,
          },
        ],
      }),
    );
    expect(d.avg_velocity).toBeNull();
    expect(d.peak_velocity).toBeNull();
  });

  it('ignores non-finite input values', () => {
    const d = deriveMotion(
      reqs({
        motion_profiles: [
          {
            stroke: { value: Number.NaN, unit: 'mm' },
            move_time: { value: 0.2, unit: 's' },
            dwell_time: null,
          },
        ],
      }),
    );
    expect(d.avg_velocity).toBeNull();
  });
});

describe('deriveMotion — peak force', () => {
  const profile = [
    {
      stroke: { value: 200, unit: 'mm' },
      move_time: { value: 0.2, unit: 's' },
      dwell_time: { value: 2, unit: 's' },
    },
  ];

  it('adds gravity for vertical orientation', () => {
    const d = deriveMotion(
      reqs({
        orientation: 'vertical',
        payload_mass: { value: 5, unit: 'kg' },
        motion_profiles: profile,
      }),
    );
    // 5 x (22.5 + 9.81) = 161.55 N.
    expect(d.peak_force?.value).toBeCloseTo(5 * (22.5 + GRAVITY), 6);
    expect(d.peak_force?.unit).toBe('N');
  });

  it('excludes gravity for horizontal orientation', () => {
    const d = deriveMotion(
      reqs({
        orientation: 'horizontal',
        payload_mass: { value: 5, unit: 'kg' },
        motion_profiles: profile,
      }),
    );
    // 5 x 22.5 = 112.5 N.
    expect(d.peak_force?.value).toBeCloseTo(112.5, 6);
  });

  it('treats a 0 kg payload as a real 0 N force', () => {
    const d = deriveMotion(
      reqs({
        orientation: 'vertical',
        payload_mass: { value: 0, unit: 'kg' },
        motion_profiles: profile,
      }),
    );
    expect(d.peak_force).toEqual({ value: 0, unit: 'N' });
  });

  it('applies no force filter when payload is blank', () => {
    const d = deriveMotion(
      reqs({ orientation: 'vertical', motion_profiles: profile }),
    );
    expect(d.peak_force).toBeNull();
    expect(d.peak_velocity).not.toBeNull();
  });

  it('applies no force filter when orientation is unknown', () => {
    const d = deriveMotion(
      reqs({ payload_mass: { value: 5, unit: 'kg' }, motion_profiles: profile }),
    );
    expect(d.peak_force).toBeNull();
  });

  it('applies no force filter without move_time', () => {
    const d = deriveMotion(
      reqs({
        orientation: 'vertical',
        payload_mass: { value: 5, unit: 'kg' },
        motion_profiles: [
          { stroke: { value: 200, unit: 'mm' }, move_time: null, dwell_time: null },
        ],
      }),
    );
    expect(d.peak_force).toBeNull();
  });
});

describe('deriveMotion — duty cycle', () => {
  it('computes move_time / (move_time + dwell_time)', () => {
    const d = deriveMotion(
      reqs({
        motion_profiles: [
          {
            stroke: { value: 200, unit: 'mm' },
            move_time: { value: 0.2, unit: 's' },
            dwell_time: { value: 2, unit: 's' },
          },
        ],
      }),
    );
    expect(d.duty_cycle).toBeCloseTo(0.2 / 2.2, 6);
  });

  it('is 1 when dwell_time is zero (continuous motion)', () => {
    const d = deriveMotion(
      reqs({
        motion_profiles: [
          {
            stroke: { value: 200, unit: 'mm' },
            move_time: { value: 1, unit: 's' },
            dwell_time: { value: 0, unit: 's' },
          },
        ],
      }),
    );
    expect(d.duty_cycle).toBe(1);
  });

  it('is null when dwell_time is unset', () => {
    const d = deriveMotion(
      reqs({
        motion_profiles: [
          {
            stroke: { value: 200, unit: 'mm' },
            move_time: { value: 1, unit: 's' },
            dwell_time: null,
          },
        ],
      }),
    );
    expect(d.duty_cycle).toBeNull();
  });
});
