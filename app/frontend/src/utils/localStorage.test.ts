/**
 * Tests for the schema-guarded localStorage helper.
 *
 * Scenarios that the old unguarded `JSON.parse(getItem(...))` approach
 * handled poorly:
 *   - missing key → null → JSON.parse("null") → null → crashes `.filter`
 *   - malformed JSON → JSON.parse throws → caller must wrap in try
 *   - right-parsed-wrong-shape ({} instead of []) → passes through, crashes later
 *   - storage access throws (Safari private mode, quota, disabled cookies)
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  safeLoad,
  safeSave,
  safeLoadString,
  isStringArray,
  isFiniteIntOrNull,
} from './localStorage';

function clearStorage() {
  window.localStorage.clear();
}

describe('safeLoad', () => {
  beforeEach(clearStorage);

  it('returns fallback when key is missing', () => {
    expect(safeLoad('missing', isStringArray, [])).toEqual([]);
  });

  it('returns fallback when JSON is malformed', () => {
    window.localStorage.setItem('bad', '{');
    expect(safeLoad('bad', isStringArray, ['default'])).toEqual(['default']);
  });

  it('returns fallback when JSON parses but validator rejects', () => {
    window.localStorage.setItem('wrongShape', JSON.stringify({ not: 'an array' }));
    expect(safeLoad('wrongShape', isStringArray, [])).toEqual([]);
  });

  it('returns fallback when stored value is null literal', () => {
    window.localStorage.setItem('nullish', 'null');
    expect(safeLoad('nullish', isStringArray, ['default'])).toEqual(['default']);
  });

  it('returns fallback when stored value is "undefined" string', () => {
    window.localStorage.setItem('undef', 'undefined');
    expect(safeLoad('undef', isStringArray, [])).toEqual([]);
  });

  it('returns fallback for array with non-string members', () => {
    window.localStorage.setItem('mixed', JSON.stringify(['a', 42, null]));
    expect(safeLoad('mixed', isStringArray, [])).toEqual([]);
  });

  it('returns parsed value when validator accepts', () => {
    window.localStorage.setItem('good', JSON.stringify(['col1', 'col2']));
    expect(safeLoad('good', isStringArray, [])).toEqual(['col1', 'col2']);
  });

  it('returns fallback when localStorage.getItem throws', () => {
    const spy = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => {
        throw new Error('access denied');
      });
    expect(safeLoad('any', isStringArray, [])).toEqual([]);
    spy.mockRestore();
  });

  it('works with null-or-int validator for max-columns-style values', () => {
    window.localStorage.setItem('cap', JSON.stringify(null));
    expect(safeLoad('cap', isFiniteIntOrNull, 12)).toBeNull();
    window.localStorage.setItem('cap', JSON.stringify(8));
    expect(safeLoad('cap', isFiniteIntOrNull, 12)).toBe(8);
    window.localStorage.setItem('cap', JSON.stringify('eight'));
    expect(safeLoad('cap', isFiniteIntOrNull, 12)).toBe(12);
  });
});

describe('safeSave', () => {
  beforeEach(clearStorage);

  it('writes JSON to the given key', () => {
    safeSave('k', { a: 1 });
    expect(window.localStorage.getItem('k')).toBe('{"a":1}');
  });

  it('swallows quota errors without crashing', () => {
    const spy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new DOMException('quota exceeded', 'QuotaExceededError');
      });
    expect(() => safeSave('k', 'v')).not.toThrow();
    spy.mockRestore();
  });
});

describe('safeLoadString', () => {
  const isDensity = (v: string): v is 'cozy' | 'compact' =>
    v === 'cozy' || v === 'compact';

  beforeEach(clearStorage);

  it('returns stored string when validator accepts', () => {
    window.localStorage.setItem('density', 'compact');
    expect(safeLoadString('density', isDensity, 'cozy')).toBe('compact');
  });

  it('returns fallback when stored string is off-schema', () => {
    window.localStorage.setItem('density', 'medium');
    expect(safeLoadString('density', isDensity, 'cozy')).toBe('cozy');
  });

  it('returns fallback when key missing', () => {
    expect(safeLoadString('missing', isDensity, 'cozy')).toBe('cozy');
  });

  it('returns fallback when getItem throws', () => {
    const spy = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => {
        throw new Error('access denied');
      });
    expect(safeLoadString('density', isDensity, 'cozy')).toBe('cozy');
    spy.mockRestore();
  });
});

describe('validators', () => {
  it('isStringArray accepts empty array', () => {
    expect(isStringArray([])).toBe(true);
  });

  it('isStringArray rejects object', () => {
    expect(isStringArray({})).toBe(false);
  });

  it('isStringArray rejects null', () => {
    expect(isStringArray(null)).toBe(false);
  });

  it('isFiniteIntOrNull accepts 0', () => {
    expect(isFiniteIntOrNull(0)).toBe(true);
  });

  it('isFiniteIntOrNull rejects NaN', () => {
    expect(isFiniteIntOrNull(Number.NaN)).toBe(false);
  });

  it('isFiniteIntOrNull rejects Infinity', () => {
    expect(isFiniteIntOrNull(Number.POSITIVE_INFINITY)).toBe(false);
  });

  it('isFiniteIntOrNull rejects floats', () => {
    expect(isFiniteIntOrNull(1.5)).toBe(false);
  });

  it('isFiniteIntOrNull accepts null', () => {
    expect(isFiniteIntOrNull(null)).toBe(true);
  });
});
