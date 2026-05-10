/**
 * Per-app-key persistence contract.
 *
 * `localStorage.test.ts` covers the helper API in the abstract. This file
 * pins down the contract for every persisted key the app *actually* uses,
 * so a refactor to a validator or default surfaces here instead of as a
 * silently-broken refresh in prod.
 *
 * For each key we cover four cases: missing, malformed JSON / off-schema
 * raw string, JSON-parses-but-wrong-shape, and a clean round-trip.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  safeLoad,
  safeLoadString,
  isStringArray,
} from './localStorage';
import {
  isBuild,
  isUnitSystem,
  isRowDensity,
  isBoolean,
} from '../context/AppContext';

function clearStorage() {
  window.localStorage.clear();
}

beforeEach(clearStorage);

describe('persisted key: unitSystem (string enum)', () => {
  const KEY = 'unitSystem';
  const DEFAULT = 'metric' as const;

  it('returns default when key is absent', () => {
    expect(safeLoadString(KEY, isUnitSystem, DEFAULT)).toBe('metric');
  });

  it('returns default when stored string is off-schema', () => {
    window.localStorage.setItem(KEY, 'kelvin');
    expect(safeLoadString(KEY, isUnitSystem, DEFAULT)).toBe('metric');
  });

  it('round-trips imperial', () => {
    window.localStorage.setItem(KEY, 'imperial');
    expect(safeLoadString(KEY, isUnitSystem, DEFAULT)).toBe('imperial');
  });

  it('returns default when stored as a JSON-encoded string', () => {
    // The app writes raw 'imperial', not '"imperial"'. A consumer who
    // accidentally JSON-stringified must not slip through.
    window.localStorage.setItem(KEY, JSON.stringify('imperial'));
    expect(safeLoadString(KEY, isUnitSystem, DEFAULT)).toBe('metric');
  });
});

describe('persisted key: productListRowDensity.v2 (string enum)', () => {
  // The .v2 suffix is the migration from the pre-May-2026 mode set
  // (compact|comfy). The new modes are cozy|compact, with the new
  // `compact` significantly denser than the old one. See AppContext
  // for the full rationale.
  const KEY = 'productListRowDensity.v2';
  const DEFAULT = 'cozy' as const;

  it('returns default when key is absent', () => {
    expect(safeLoadString(KEY, isRowDensity, DEFAULT)).toBe('cozy');
  });

  it('returns default when stored string is off-schema', () => {
    window.localStorage.setItem(KEY, 'medium');
    expect(safeLoadString(KEY, isRowDensity, DEFAULT)).toBe('cozy');
  });

  it('rejects pre-v2 "comfy" as off-schema', () => {
    // Catches anyone tempted to re-allow the old token via the validator.
    window.localStorage.setItem(KEY, 'comfy');
    expect(safeLoadString(KEY, isRowDensity, DEFAULT)).toBe('cozy');
  });

  it('round-trips compact', () => {
    window.localStorage.setItem(KEY, 'compact');
    expect(safeLoadString(KEY, isRowDensity, DEFAULT)).toBe('compact');
  });
});

describe('persisted key: specodex.compatibleOnly (boolean)', () => {
  const KEY = 'specodex.compatibleOnly';
  const DEFAULT = true;

  it('returns default when key is absent', () => {
    expect(safeLoad(KEY, isBoolean, DEFAULT)).toBe(true);
  });

  it('returns default when JSON is malformed', () => {
    window.localStorage.setItem(KEY, '{');
    expect(safeLoad(KEY, isBoolean, DEFAULT)).toBe(true);
  });

  it('returns default when shape is wrong (number, not boolean)', () => {
    window.localStorage.setItem(KEY, JSON.stringify(1));
    expect(safeLoad(KEY, isBoolean, DEFAULT)).toBe(true);
  });

  it('round-trips false', () => {
    window.localStorage.setItem(KEY, JSON.stringify(false));
    expect(safeLoad(KEY, isBoolean, DEFAULT)).toBe(false);
  });
});

describe('persisted key: specodex.build (build object)', () => {
  const KEY = 'specodex.build';

  it('returns default {} when key is absent', () => {
    expect(safeLoad(KEY, isBuild, {})).toEqual({});
  });

  it('returns default {} when JSON is malformed', () => {
    window.localStorage.setItem(KEY, '{');
    expect(safeLoad(KEY, isBuild, {})).toEqual({});
  });

  it('returns default {} when stored value is an array', () => {
    window.localStorage.setItem(KEY, JSON.stringify([]));
    expect(safeLoad(KEY, isBuild, {})).toEqual({});
  });

  it('returns default {} when a slot key is unknown (e.g. "blender")', () => {
    window.localStorage.setItem(KEY, JSON.stringify({ blender: { id: 'x' } }));
    expect(safeLoad(KEY, isBuild, {})).toEqual({});
  });

  it('returns default {} when a slot value is a string instead of a product', () => {
    window.localStorage.setItem(KEY, JSON.stringify({ motor: 'string-not-product' }));
    expect(safeLoad(KEY, isBuild, {})).toEqual({});
  });

  it('round-trips an empty object', () => {
    window.localStorage.setItem(KEY, JSON.stringify({}));
    expect(safeLoad(KEY, isBuild, {})).toEqual({});
  });

  it('round-trips a single filled motor slot', () => {
    const value = { motor: { id: 'm-1', product_type: 'motor' } };
    window.localStorage.setItem(KEY, JSON.stringify(value));
    expect(safeLoad(KEY, isBuild, {})).toEqual(value);
  });
});

describe('persisted key: productListHiddenColumns (string[])', () => {
  const KEY = 'productListHiddenColumns';
  const DEFAULT: string[] = [];

  it('returns default when key is absent', () => {
    expect(safeLoad(KEY, isStringArray, DEFAULT)).toEqual([]);
  });

  it('returns default when JSON is malformed', () => {
    window.localStorage.setItem(KEY, '{');
    expect(safeLoad(KEY, isStringArray, DEFAULT)).toEqual([]);
  });

  it('returns default when shape is an object', () => {
    window.localStorage.setItem(KEY, JSON.stringify({ a: 1 }));
    expect(safeLoad(KEY, isStringArray, DEFAULT)).toEqual([]);
  });

  it('returns default when array contains non-strings', () => {
    window.localStorage.setItem(KEY, JSON.stringify(['ok', 42]));
    expect(safeLoad(KEY, isStringArray, DEFAULT)).toEqual([]);
  });

  it('round-trips a list of hidden column keys', () => {
    window.localStorage.setItem(KEY, JSON.stringify(['rated_torque', 'voltage']));
    expect(safeLoad(KEY, isStringArray, DEFAULT)).toEqual(['rated_torque', 'voltage']);
  });
});

describe('persisted key: productListRestoredColumns (string[])', () => {
  // Same contract as hiddenColumns — covered separately so a future
  // divergence (different validator, different default) is impossible to
  // ship without updating one of these blocks.
  const KEY = 'productListRestoredColumns';
  const DEFAULT: string[] = [];

  it('returns default when key is absent', () => {
    expect(safeLoad(KEY, isStringArray, DEFAULT)).toEqual([]);
  });

  it('returns default when shape is wrong', () => {
    window.localStorage.setItem(KEY, JSON.stringify({ not: 'array' }));
    expect(safeLoad(KEY, isStringArray, DEFAULT)).toEqual([]);
  });

  it('round-trips a list of restored column keys', () => {
    window.localStorage.setItem(KEY, JSON.stringify(['frame_size']));
    expect(safeLoad(KEY, isStringArray, DEFAULT)).toEqual(['frame_size']);
  });
});

describe('persisted key: theme (raw string, not safeLoad-managed)', () => {
  // ThemeToggle reads theme via a raw `localStorage.getItem`, not safeLoad.
  // We don't lock that down here — Phase 4 of FRONTEND_TESTING.md owns the
  // ThemeToggle test. This block exists to flag that deviation: if you add
  // a new persisted key, prefer safeLoad/safeLoadString so it lands in the
  // contract above by default.
  it.skip('owned by ThemeToggle.test.tsx (Phase 4)', () => {});
});
