/**
 * Tests for Build's URL state codec (todo/BUILD.md Part 2).
 */

import { describe, it, expect } from 'vitest';
import {
  serialiseBuildRequirementsToURL,
  parseURLToBuildRequirements,
} from './buildURLState';
import {
  emptyBuildRequirements,
  type BuildRequirements,
} from '../types/buildRequirements';

function fullRequirements(): BuildRequirements {
  return {
    motion_class: 'linear',
    orientation: 'vertical',
    payload_mass: { value: 5, unit: 'kg' },
    motion_profiles: [
      {
        stroke: { value: 200, unit: 'mm' },
        move_time: { value: 0.2, unit: 's' },
        dwell_time: { value: 2, unit: 's' },
      },
    ],
    units_preference: 'metric',
  };
}

describe('serialiseBuildRequirementsToURL', () => {
  it('encodes every field with the documented param keys', () => {
    const params = serialiseBuildRequirementsToURL(fullRequirements());
    expect(params.get('ml')).toBe('linear');
    expect(params.get('or')).toBe('vertical');
    expect(params.get('pm')).toBe('5kg');
    expect(params.get('st')).toBe('200mm');
    expect(params.get('mt')).toBe('0.2s');
    expect(params.get('dw')).toBe('2s');
    expect(params.get('up')).toBe('metric');
  });

  it('omits null fields entirely (blank = no constraint)', () => {
    const params = serialiseBuildRequirementsToURL(emptyBuildRequirements());
    expect(params.has('ml')).toBe(false);
    expect(params.has('or')).toBe(false);
    expect(params.has('pm')).toBe(false);
    expect(params.has('st')).toBe(false);
    expect(params.has('mt')).toBe(false);
    expect(params.has('dw')).toBe(false);
  });

  it('always emits units_preference even on an empty form', () => {
    const params = serialiseBuildRequirementsToURL(emptyBuildRequirements());
    expect(params.get('up')).toBe('metric');
  });

  it('preserves the imperial display preference', () => {
    const req = { ...emptyBuildRequirements(), units_preference: 'imperial' as const };
    expect(serialiseBuildRequirementsToURL(req).get('up')).toBe('imperial');
  });

  it('encodes ValueUnit with no separator between number and unit', () => {
    const req = fullRequirements();
    req.payload_mass = { value: 12.5, unit: 'lb' };
    expect(serialiseBuildRequirementsToURL(req).get('pm')).toBe('12.5lb');
  });
});

describe('parseURLToBuildRequirements', () => {
  it('round-trips a fully populated requirements state', () => {
    const original = fullRequirements();
    const params = serialiseBuildRequirementsToURL(original);
    expect(parseURLToBuildRequirements(params)).toEqual(original);
  });

  it('round-trips the empty form', () => {
    const params = serialiseBuildRequirementsToURL(emptyBuildRequirements());
    expect(parseURLToBuildRequirements(params)).toEqual(
      emptyBuildRequirements(),
    );
  });

  it('returns the empty form for a param-less URL', () => {
    expect(parseURLToBuildRequirements(new URLSearchParams())).toEqual(
      emptyBuildRequirements(),
    );
  });

  it('decodes a hand-written URL', () => {
    const params = new URLSearchParams(
      'ml=linear&or=horizontal&pm=5kg&st=200mm&mt=0.2s&dw=2s&up=imperial',
    );
    const req = parseURLToBuildRequirements(params);
    expect(req.motion_class).toBe('linear');
    expect(req.orientation).toBe('horizontal');
    expect(req.payload_mass).toEqual({ value: 5, unit: 'kg' });
    expect(req.motion_profiles[0]).toEqual({
      stroke: { value: 200, unit: 'mm' },
      move_time: { value: 0.2, unit: 's' },
      dwell_time: { value: 2, unit: 's' },
    });
    expect(req.units_preference).toBe('imperial');
  });

  it('always yields exactly one motion profile', () => {
    expect(parseURLToBuildRequirements(new URLSearchParams()).motion_profiles)
      .toHaveLength(1);
  });

  it('drops out-of-vocabulary enum values to null', () => {
    const params = new URLSearchParams('ml=diagonal&or=sideways');
    const req = parseURLToBuildRequirements(params);
    expect(req.motion_class).toBeNull();
    expect(req.orientation).toBeNull();
  });

  it('falls back to metric for a junk units_preference', () => {
    const req = parseURLToBuildRequirements(new URLSearchParams('up=furlongs'));
    expect(req.units_preference).toBe('metric');
  });

  it('drops a ValueUnit param with no leading number to null', () => {
    const req = parseURLToBuildRequirements(new URLSearchParams('st=mm&pm=kg'));
    expect(req.motion_profiles[0].stroke).toBeNull();
    expect(req.payload_mass).toBeNull();
  });

  it('decodes a unit-less number and a leading-decimal number', () => {
    const req = parseURLToBuildRequirements(
      new URLSearchParams('st=200&mt=.25s'),
    );
    expect(req.motion_profiles[0].stroke).toEqual({ value: 200, unit: '' });
    expect(req.motion_profiles[0].move_time).toEqual({
      value: 0.25,
      unit: 's',
    });
  });

  it('treats a present-but-empty ValueUnit param as null', () => {
    const req = parseURLToBuildRequirements(new URLSearchParams('st='));
    expect(req.motion_profiles[0].stroke).toBeNull();
  });

  it('does not throw on adversarial input', () => {
    const hostile = new URLSearchParams(
      'ml=<script>&pm=NaNkg&st=Infinitymm&mt=1e9999s&dw=---',
    );
    expect(() => parseURLToBuildRequirements(hostile)).not.toThrow();
  });

  it('ignores unrelated query params', () => {
    const params = new URLSearchParams('utm_source=x&ml=linear&foo=bar');
    expect(parseURLToBuildRequirements(params).motion_class).toBe('linear');
  });
});
