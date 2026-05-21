/**
 * Tests for the linear-mode display transforms.
 *
 * Each helper had two real call shapes in `ProductList.tsx`'s
 * `linearizedSource` memo: a ValueUnit (`{value, unit}`) and a
 * MinMaxUnit (`{min, max, unit}`). The pre-extraction transforms also
 * passed through anything else unchanged (null/undefined/zero
 * linearTravel guard). These tests pin all three.
 */

import { describe, it, expect } from 'vitest';
import { rpmToLinearSpeed, torqueToThrust } from './linearMode';

describe('rpmToLinearSpeed', () => {
  // 3000 rpm * 10 mm/rev / 60 = 500 mm/s
  it('converts a ValueUnit RPM reading at the documented coefficient', () => {
    const result = rpmToLinearSpeed({ value: 3000, unit: 'rpm' }, 10);
    expect(result).toEqual({ value: 500, unit: 'mm/s' });
  });

  it('converts a MinMaxUnit RPM range, preserving null endpoints', () => {
    const result = rpmToLinearSpeed({ min: 600, max: null, unit: 'rpm' }, 10);
    expect(result).toEqual({ min: 100, max: null, unit: 'mm/s' });
  });

  it('returns the value unchanged when linearTravel is 0', () => {
    const input = { value: 3000, unit: 'rpm' };
    expect(rpmToLinearSpeed(input, 0)).toBe(input);
  });

  it.each([null, undefined, 42, 'string', { foo: 'bar' }])(
    'returns the value unchanged for unrecognisable input %p',
    (input) => {
      expect(rpmToLinearSpeed(input, 10)).toBe(input);
    },
  );
});

describe('torqueToThrust', () => {
  // F = T * 2π / lead — lead = 10 mm = 0.01 m → factor = 2π / 0.01 ≈ 628.3
  // 1 Nm @ 10 mm lead → ~628.3 N
  it('converts a ValueUnit torque reading at the documented coefficient', () => {
    const result = torqueToThrust({ value: 1, unit: 'Nm' }, 10);
    expect(result.unit).toBe('N');
    expect(result.value).toBeCloseTo(628.3, 0);
  });

  it('converts a MinMaxUnit torque range, preserving null endpoints', () => {
    const result = torqueToThrust({ min: null, max: 1, unit: 'Nm' }, 10);
    expect(result.min).toBeNull();
    expect(result.max).toBeCloseTo(628.3, 0);
    expect(result.unit).toBe('N');
  });

  it('returns the value unchanged when linearTravel is 0', () => {
    const input = { value: 1, unit: 'Nm' };
    expect(torqueToThrust(input, 0)).toBe(input);
  });

  it.each([null, undefined, 42, 'string', { foo: 'bar' }])(
    'returns the value unchanged for unrecognisable input %p',
    (input) => {
      expect(torqueToThrust(input, 10)).toBe(input);
    },
  );
});
