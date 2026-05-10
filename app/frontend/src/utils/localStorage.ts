/**
 * Schema-guarded localStorage helpers.
 *
 * Goal: no component reads `JSON.parse(localStorage.getItem(...))` unguarded.
 * A malformed or stale value must fall back to a known default, not crash
 * render or leak through to downstream logic as the wrong shape.
 */

export type Validator<T> = (value: unknown) => value is T;

/**
 * Load + JSON-parse + type-guard in one step. Returns `fallback` on any
 * failure (missing key, malformed JSON, guard rejection, quota/access error).
 */
export function safeLoad<T>(
  key: string,
  isValid: Validator<T>,
  fallback: T,
): T {
  if (typeof window === 'undefined') return fallback;
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(key);
  } catch {
    return fallback;
  }
  if (raw === null) return fallback;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return fallback;
  }
  return isValid(parsed) ? parsed : fallback;
}

/**
 * Serialize + write. Swallows quota/access errors so UI doesn't crash when
 * storage is full, disabled, or we're in a sandboxed iframe.
 */
export function safeSave(key: string, value: unknown): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Best-effort only. Falls back to in-memory state.
  }
}

/**
 * Load a raw string with a validator (no JSON.parse). Useful for enum-like
 * keys stored as plain strings ('cozy' | 'compact', 'asc' | 'desc').
 */
export function safeLoadString<T extends string>(
  key: string,
  isValid: (value: string) => value is T,
  fallback: T,
): T {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw !== null && isValid(raw)) return raw;
  } catch {
    // noop
  }
  return fallback;
}

// --- common validators -------------------------------------------------------

export const isStringArray: Validator<string[]> = (v): v is string[] =>
  Array.isArray(v) && v.every((item) => typeof item === 'string');

export const isFiniteIntOrNull: Validator<number | null> = (v): v is number | null =>
  v === null || (typeof v === 'number' && Number.isFinite(v) && Number.isInteger(v));
