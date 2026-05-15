/**
 * Tests for the Build requirement-vocabulary schema helpers.
 */

import { describe, it, expect } from 'vitest';
import { emptyBuildRequirements } from './buildRequirements';

describe('emptyBuildRequirements', () => {
  it('starts every requirement field unset except units_preference', () => {
    const r = emptyBuildRequirements();
    expect(r.motion_class).toBeNull();
    expect(r.orientation).toBeNull();
    expect(r.payload_mass).toBeNull();
    expect(r.units_preference).toBe('metric');
  });

  it('ships exactly one blank motion profile', () => {
    const r = emptyBuildRequirements();
    expect(r.motion_profiles).toHaveLength(1);
    expect(r.motion_profiles[0]).toEqual({
      stroke: null,
      move_time: null,
      dwell_time: null,
    });
  });

  it('returns a fresh object each call (no shared mutable state)', () => {
    const a = emptyBuildRequirements();
    const b = emptyBuildRequirements();
    expect(a).not.toBe(b);
    expect(a.motion_profiles).not.toBe(b.motion_profiles);
    a.motion_profiles[0].stroke = { value: 100, unit: 'mm' };
    expect(b.motion_profiles[0].stroke).toBeNull();
  });
});
