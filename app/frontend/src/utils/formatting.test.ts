/**
 * Tests for formatting utility functions
 */

import { describe, it, expect } from 'vitest';
import { formatPropertyLabel, formatValue, formatNumber, formatRange } from './formatting';

describe('formatNumber', () => {
  it('passes non-numbers and non-finite values through', () => {
    expect(formatNumber('x')).toBe('x');
    expect(formatNumber(null)).toBe('null');
    expect(formatNumber(Infinity)).toBe('Infinity');
    expect(formatNumber(NaN)).toBe('NaN');
  });

  it('never rounds integers', () => {
    expect(formatNumber(4525)).toBe('4525');
    expect(formatNumber(13)).toBe('13');
    expect(formatNumber(0)).toBe('0');
  });

  it('groups thousands only from 10k up', () => {
    expect(formatNumber(90000)).toBe('90,000');
    expect(formatNumber(15700)).toBe('15,700');
    expect(formatNumber(4000)).toBe('4000');
    expect(formatNumber(2600)).toBe('2600');
  });

  it('trims extraction-noise float precision by magnitude band', () => {
    expect(formatNumber(1.64054)).toBe('1.64');
    expect(formatNumber(0.0127108)).toBe('0.0127');
    expect(formatNumber(0.0494)).toBe('0.0494');
    expect(formatNumber(115.51)).toBe('115.5');
    expect(formatNumber(10.2)).toBe('10.2');
    expect(formatNumber(-1.64054)).toBe('-1.64');
  });

  it('never shows trailing zeros', () => {
    expect(formatNumber(1.5)).toBe('1.5');
    expect(formatNumber(2.0)).toBe('2');
  });
});

describe('formatRange', () => {
  it('collapses degenerate ranges to the single value', () => {
    expect(formatRange(460, 460)).toBe('460');
    expect(formatRange(10, 10)).toBe('10');
  });

  it('renders distinct bounds as min-max', () => {
    expect(formatRange(200, 240)).toBe('200-240');
  });

  it('renders one-sided ranges as the present bound', () => {
    expect(formatRange(48, null)).toBe('48');
    expect(formatRange(undefined, 75)).toBe('75');
    expect(formatRange(null, null)).toBe('');
  });

  it('formats bounds through formatNumber', () => {
    expect(formatRange(0.0127108, 1.64054)).toBe('0.0127-1.64');
  });
});

describe('formatPropertyLabel', () => {
  it('capitalizes regular words', () => {
    expect(formatPropertyLabel('rated_voltage')).toBe('Rated Voltage');
  });

  it('uppercases known acronyms', () => {
    expect(formatPropertyLabel('ip_rating')).toBe('IP Rating');
    expect(formatPropertyLabel('ac_input')).toBe('AC Input');
    expect(formatPropertyLabel('dc_output')).toBe('DC Output');
    expect(formatPropertyLabel('pwm_frequency')).toBe('PWM Frequency');
  });

  it('handles single word', () => {
    expect(formatPropertyLabel('weight')).toBe('Weight');
  });

  it('handles single acronym', () => {
    expect(formatPropertyLabel('url')).toBe('URL');
  });

  it('handles multiple acronyms', () => {
    expect(formatPropertyLabel('io_api')).toBe('IO API');
  });

  it('handles mixed acronyms and words', () => {
    expect(formatPropertyLabel('max_rpm')).toBe('Max RPM');
    expect(formatPropertyLabel('usb_port_count')).toBe('USB Port Count');
  });

  it('handles empty string', () => {
    expect(formatPropertyLabel('')).toBe('');
  });
});

describe('formatValue', () => {
  it('returns empty string for null', () => {
    expect(formatValue(null)).toBe('');
  });

  it('returns empty string for undefined', () => {
    expect(formatValue(undefined)).toBe('');
  });

  it('converts string to string', () => {
    expect(formatValue('hello')).toBe('hello');
  });

  it('converts number to string', () => {
    expect(formatValue(42)).toBe('42');
  });

  it('converts boolean to string', () => {
    expect(formatValue(true)).toBe('true');
  });

  it('formats value+unit object', () => {
    expect(formatValue({ value: 3000, unit: 'rpm' })).toBe('3000 rpm');
  });

  it('formats min+max+unit object', () => {
    expect(formatValue({ min: 100, max: 240, unit: 'V' })).toBe('100-240 V');
  });

  it('formats nominal+unit object', () => {
    expect(formatValue({ nominal: 24, unit: 'V' })).toBe('24 V');
  });

  it('formats rated+unit object', () => {
    expect(formatValue({ rated: 5, unit: 'A' })).toBe('5 A');
  });

  it('formats min+max without unit', () => {
    expect(formatValue({ min: 0, max: 100 })).toBe('0-100');
  });

  it('returns empty string for empty array', () => {
    expect(formatValue([])).toBe('');
  });

  it('formats string array', () => {
    expect(formatValue(['EtherCAT', 'CANopen'])).toBe('EtherCAT, CANopen');
  });

  it('formats array of value+unit objects', () => {
    const arr = [
      { value: 4000, unit: 'Hz' },
      { value: 8000, unit: 'Hz' },
    ];
    expect(formatValue(arr)).toBe('4000, 8000 Hz');
  });

  it('formats array of min+max+unit objects', () => {
    const arr = [
      { min: 50, max: 60, unit: 'Hz' },
      { min: 47, max: 63, unit: 'Hz' },
    ];
    expect(formatValue(arr)).toBe('50-60, 47-63 Hz');
  });

  it('formats nested object as key-value pairs', () => {
    const result = formatValue({ width: 100, height: 50, unit: 'mm' });
    expect(result).toContain('Width: 100');
    expect(result).toContain('Height: 50');
    expect(result).toContain('mm');
  });

  it('filters out missing entries from nested objects', () => {
    const result = formatValue({ width: 100, height: null, unit: 'mm' });
    expect(result).not.toContain('Height');
  });

  it('respects max depth', () => {
    expect(formatValue({ a: 1 }, 6, 5)).toBe('[Max depth exceeded]');
  });

  it('returns empty string for object with all missing values', () => {
    expect(formatValue({ a: null, b: undefined })).toBe('');
  });

  it('default (no system arg) keeps metric output stable', () => {
    expect(formatValue({ value: 100, unit: 'N' })).toBe('100 N');
  });

  it('imperial flips ValueUnit (Nm → in·lb)', () => {
    const out = formatValue({ value: 1, unit: 'Nm' }, 0, 5, 'imperial');
    expect(out).toContain('in·lb');
    expect(out).toMatch(/^8\.85/);
  });

  it('imperial flips MinMaxUnit temperature with offset', () => {
    expect(
      formatValue({ min: -20, max: 60, unit: '°C' }, 0, 5, 'imperial'),
    ).toBe('-4-140 °F');
  });

  it('imperial leaves voltage (no idiomatic imperial) unchanged', () => {
    expect(formatValue({ value: 24, unit: 'V' }, 0, 5, 'imperial')).toBe('24 V');
  });

  it('imperial flips dimensions object with shared unit', () => {
    const out = formatValue(
      { width: 100, height: 50, unit: 'mm' },
      0,
      5,
      'imperial',
    );
    expect(out).toContain('in');
    expect(out).not.toContain(' mm');
  });

  it('collapses degenerate MinMaxUnit ranges (extraction min === max)', () => {
    expect(formatValue({ min: 460, max: 460, unit: 'V' })).toBe('460 V');
    expect(formatValue({ min: 10, max: 10 })).toBe('10');
  });

  it('trims float precision in ValueUnit cells', () => {
    expect(formatValue({ value: 1.64054, unit: 'Nm' })).toBe('1.64 Nm');
    expect(formatValue({ value: 0.0127108, unit: 'Nm' })).toBe('0.0127 Nm');
  });

  it('groups large magnitudes in ValueUnit cells', () => {
    expect(formatValue({ value: 90000, unit: 'W' })).toBe('90,000 W');
  });
});
