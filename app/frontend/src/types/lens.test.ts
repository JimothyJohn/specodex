import { describe, it, expect } from 'vitest';
import { computeLensColumns, getLensForKey, isLensArray, LENS_MIN_FILL_RATE } from './lens';
import { getAttributesForType, type AttributeMetadata } from './filters';
import { orderColumnAttributes } from './columnOrder';

const attr = (key: string): AttributeMetadata => ({
  key, displayName: key, type: 'object', applicableTypes: ['motor'], nested: true,
});

describe('getLensForKey', () => {
  it.each([
    ['rated_torque', 'function'],
    ['rated_speed', 'function'],
    ['rated_power', 'function'],
    ['backlash', 'function'],
    ['max_dimension', 'form'],
    ['weight', 'form'],
    ['dimensions', 'form'],
    ['shaft_diameter', 'fit'],
    ['frame_size', 'fit'],
    ['fieldbus', 'fit'],
    ['encoder_feedback_support', 'fit'],
    ['encoder_feedback_support.resolution_counts_per_rev', 'fit'],
    ['ip_rating', 'fit'],
    ['input_voltage', 'fit'],
    ['ethernet_ports', 'fit'],
  ])('%s → %s', (key, lens) => {
    expect(getLensForKey(key)).toBe(lens);
  });

  it('identification and unknown keys belong to no lens', () => {
    expect(getLensForKey('manufacturer')).toBeNull();
    expect(getLensForKey('series')).toBeNull();
    expect(getLensForKey('totally_unknown_key')).toBeNull();
  });

  it('every curated column of every type lands in a lens', () => {
    // No spec column of the three core types should be lens-less except
    // identification — otherwise it can never be shown in a lens view.
    for (const t of ['motor', 'drive', 'gearhead', 'robot_arm', 'contactor', 'electric_cylinder', 'inductive_sensor'] as const) {
      const orphan = getAttributesForType(t)
        .map(a => a.key)
        .filter(k => getLensForKey(k) === null && !['manufacturer', 'part_number', 'series', 'type', 'gear_type', 'product_family', 'motor_type'].includes(k));
      expect(orphan, `${t} orphans`).toEqual([]);
    }
  });
});

describe('computeLensColumns', () => {
  const cols = ['manufacturer', 'rated_torque', 'rated_speed', 'max_dimension', 'weight', 'shaft_diameter', 'fieldbus'].map(attr);

  it('no lens → null (normal view)', () => {
    expect(computeLensColumns(cols, [], [], 10)).toBeNull();
  });

  it('one lens snaps to its columns with manufacturer pinned', () => {
    expect(computeLensColumns(cols, ['function'], [], 10)!.map(a => a.key))
      .toEqual(['manufacturer', 'rated_torque', 'rated_speed']);
    expect(computeLensColumns(cols, ['form'], [], 10)!.map(a => a.key))
      .toEqual(['manufacturer', 'max_dimension', 'weight']);
    expect(computeLensColumns(cols, ['fit'], [], 10)!.map(a => a.key))
      .toEqual(['manufacturer', 'shaft_diameter', 'fieldbus']);
  });

  it('lenses combine as a union in input order', () => {
    expect(computeLensColumns(cols, ['form', 'function'], [], 10)!.map(a => a.key))
      .toEqual(['manufacturer', 'rated_torque', 'rated_speed', 'max_dimension', 'weight']);
  });

  it('drops effectively-empty columns but keeps sparse ones', () => {
    const fill = new Map([['max_dimension', LENS_MIN_FILL_RATE / 2], ['weight', 0.1]]);
    expect(computeLensColumns(cols, ['form'], [], 10, fill)!.map(a => a.key))
      .toEqual(['manufacturer', 'weight']);
  });

  it('respects user-hidden columns and the cap', () => {
    expect(computeLensColumns(cols, ['function', 'form'], ['rated_speed'], 3)!.map(a => a.key))
      .toEqual(['manufacturer', 'rated_torque', 'max_dimension']);
    expect(computeLensColumns(cols, ['function'], [], 0)).toEqual([]);
    expect(computeLensColumns(cols, ['function'], [], -5)).toEqual([]);
  });

  it('works on the real ordered motor attribute list', () => {
    const ordered = orderColumnAttributes(getAttributesForType('motor'), 'motor');
    const keys = computeLensColumns(ordered, ['function'], [], 50)!.map(a => a.key);
    expect(keys[0]).toBe('manufacturer');
    expect(keys).toContain('rated_torque');
    expect(keys).not.toContain('weight');
  });
});

describe('isLensArray', () => {
  it('accepts only valid persisted values', () => {
    expect(isLensArray(['form', 'fit'])).toBe(true);
    expect(isLensArray([])).toBe(true);
    expect(isLensArray(['form', 'shape'])).toBe(false);
    expect(isLensArray('form')).toBe(false);
    expect(isLensArray(null)).toBe(false);
  });
});
