/**
 * Regression tests for the log-injection defense.
 *
 * Three layers of coverage:
 *
 *  1. `safeLog` formatter contract — type coercion, truncation, quoting.
 *     Documents the **intentional** decision that safeLog does NOT
 *     strip CR/LF (see util/log.ts for why).
 *
 *  2. `requestLogger` middleware barrier — the inline
 *     `req.path.replace(/\r|\n/g, '')` that CodeQL `js/log-injection`
 *     requires. Removing the `.replace` should make these tests fail.
 *
 *  3. `readonlyGuard` middleware barrier — same defense at a different
 *     log call site, exercised when a write hits public mode.
 *
 * Bug history: PRs #82 (inline safeLog), #83 (codeql-loginjection-inline),
 * #84 (codeql-loginjection-audit) shipped fixes without regression tests.
 * This file is the regression net so future refactors don't undo the
 * defense silently.
 */

import { Request, Response, NextFunction } from 'express';

import { safeLog } from '../src/util/log';
import { requestLogger } from '../src/middleware/requestLogger';
import { readonlyGuard } from '../src/middleware/readonly';

// --------------------------------------------------------------------
// safeLog formatter contract
// --------------------------------------------------------------------

describe('safeLog (formatter contract — util/log.ts)', () => {
  describe('type coercion', () => {
    it('returns "null" for null', () => {
      expect(safeLog(null)).toBe('null');
    });

    it('returns "undefined" for undefined', () => {
      expect(safeLog(undefined)).toBe('undefined');
    });

    it('coerces numbers without quoting', () => {
      expect(safeLog(42)).toBe('42');
      expect(safeLog(0)).toBe('0');
      expect(safeLog(-1.5)).toBe('-1.5');
    });

    it('coerces booleans without quoting', () => {
      expect(safeLog(true)).toBe('true');
      expect(safeLog(false)).toBe('false');
    });

    it('wraps strings in single quotes', () => {
      expect(safeLog('hello')).toBe("'hello'");
    });

    it('JSON-stringifies plain objects, then quotes', () => {
      expect(safeLog({ a: 1 })).toBe(`'{"a":1}'`);
    });

    it('returns "[unserializable]" (quoted) for circular references', () => {
      const circular: Record<string, unknown> = {};
      circular.self = circular;
      expect(safeLog(circular)).toBe(`'[unserializable]'`);
    });
  });

  describe('truncation', () => {
    it('does not truncate strings up to 200 chars', () => {
      const s = 'a'.repeat(200);
      expect(safeLog(s)).toBe(`'${s}'`);
    });

    it('truncates strings longer than 200 chars and appends ellipsis', () => {
      const s = 'a'.repeat(250);
      expect(safeLog(s)).toBe(`'${'a'.repeat(200)}…'`);
    });

    it('truncates the inner content, not the outer quote', () => {
      const s = 'b'.repeat(201);
      const result = safeLog(s);
      expect(result.startsWith("'")).toBe(true);
      expect(result.endsWith("'")).toBe(true);
      expect(result.slice(1, -1)).toBe('b'.repeat(200) + '…');
    });

    it('truncation also applies to JSON-stringified objects', () => {
      const long = { huge: 'x'.repeat(300) };
      const result = safeLog(long);
      // Stringified is `{"huge":"xxx..."}` — over 200 chars total.
      expect(result.length).toBeLessThanOrEqual(200 + 3); // 200 + ellipsis + 2 quotes
      expect(result.endsWith("…'")).toBe(true);
    });
  });

  // --- Documented intentional behavior ---
  //
  // safeLog deliberately does NOT strip CR/LF. The CR/LF strip — the
  // actual log-injection barrier — must happen at the call site so
  // CodeQL's `js/log-injection` sanitizer can recognize it on the
  // data-flow path to the sink. See util/log.ts for the full reasoning.
  //
  // These tests pin that contract: if someone "helpfully" adds a
  // `.replace(/\r|\n/g, '')` inside safeLog, CodeQL will silently
  // re-flag the call sites because it doesn't see the helper-internal
  // sanitizer. Keep this contract.
  describe('preserves CR/LF (intentional — call sites strip)', () => {
    it('keeps \\n in the output', () => {
      expect(safeLog('a\nb')).toContain('\n');
    });

    it('keeps \\r in the output', () => {
      expect(safeLog('a\rb')).toContain('\r');
    });

    it('keeps \\r\\n sequences', () => {
      expect(safeLog('a\r\nb')).toContain('\r\n');
    });
  });
});

// --------------------------------------------------------------------
// requestLogger middleware (CodeQL js/log-injection barrier)
// --------------------------------------------------------------------

describe('requestLogger middleware (CR/LF barrier — middleware/requestLogger.ts)', () => {
  let consoleLogSpy: jest.SpyInstance;

  beforeEach(() => {
    consoleLogSpy = jest.spyOn(console, 'log').mockImplementation();
  });

  afterEach(() => {
    consoleLogSpy.mockRestore();
  });

  function fakeReq(path: string, method = 'GET'): Request {
    return { path, method } as Request;
  }

  function fakeRes(): Response {
    return {} as Response;
  }

  function loggedLine(): string {
    expect(consoleLogSpy).toHaveBeenCalledTimes(1);
    return String(consoleLogSpy.mock.calls[0][0]);
  }

  it('strips \\r\\n from req.path before logging (canonical injection)', () => {
    const next = jest.fn();
    requestLogger(fakeReq('/foo\r\nINJECTED-LINE'), fakeRes(), next as NextFunction);
    const line = loggedLine();
    expect(line).not.toMatch(/[\r\n]/);
    // Path content survives, with separators removed:
    expect(line).toContain('fooINJECTED-LINE');
    expect(next).toHaveBeenCalledTimes(1);
  });

  it('strips bare \\r', () => {
    requestLogger(fakeReq('/foo\rBAR'), fakeRes(), jest.fn() as NextFunction);
    expect(loggedLine()).not.toContain('\r');
  });

  it('strips bare \\n', () => {
    requestLogger(fakeReq('/foo\nBAR'), fakeRes(), jest.fn() as NextFunction);
    expect(loggedLine()).not.toContain('\n');
  });

  it('strips multiple separated CR/LF sequences', () => {
    requestLogger(
      fakeReq('/foo\r\nFAKE-LOG-1\r\nFAKE-LOG-2'),
      fakeRes(),
      jest.fn() as NextFunction,
    );
    const line = loggedLine();
    expect(line).not.toMatch(/[\r\n]/);
    // Confirms the strip is global, not just the first match.
    expect(line).toContain('FAKE-LOG-1');
    expect(line).toContain('FAKE-LOG-2');
  });

  it('preserves the HTTP method (not user-controlled)', () => {
    requestLogger(fakeReq('/x', 'POST'), fakeRes(), jest.fn() as NextFunction);
    expect(loggedLine()).toContain('POST');
  });

  it('always calls next()', () => {
    const next = jest.fn();
    requestLogger(fakeReq('/x'), fakeRes(), next as NextFunction);
    expect(next).toHaveBeenCalledTimes(1);
  });
});

// --------------------------------------------------------------------
// readonlyGuard middleware (same barrier, different call site)
// --------------------------------------------------------------------

describe('readonlyGuard middleware (CR/LF barrier — middleware/readonly.ts)', () => {
  let consoleWarnSpy: jest.SpyInstance;

  beforeEach(() => {
    consoleWarnSpy = jest.spyOn(console, 'warn').mockImplementation();
  });

  afterEach(() => {
    consoleWarnSpy.mockRestore();
  });

  function fakeReq(path: string, method: string): Request {
    return { path, method } as Request;
  }

  function fakeRes(): Response {
    return {
      status: jest.fn().mockReturnThis(),
      json: jest.fn().mockReturnThis(),
    } as unknown as Response;
  }

  it('strips \\r\\n from req.path in the blocked-write log line', () => {
    const next = jest.fn();
    // Use a path NOT in WRITE_ALLOWED_PATHS or WRITE_ALLOWED_PREFIXES,
    // and a non-GET method, so the guard logs and 403s.
    readonlyGuard(
      fakeReq('/products\r\nINJECTED-WRITE', 'POST'),
      fakeRes(),
      next as NextFunction,
    );
    expect(consoleWarnSpy).toHaveBeenCalledTimes(1);
    const line = String(consoleWarnSpy.mock.calls[0][0]);
    expect(line).not.toMatch(/[\r\n]/);
    expect(line).toContain('productsINJECTED-WRITE');
    expect(next).not.toHaveBeenCalled(); // 403, no pass-through
  });

  it('does not log (and does not strip) on allowed GET — barrier is on the warn path only', () => {
    const next = jest.fn();
    readonlyGuard(fakeReq('/whatever', 'GET'), fakeRes(), next as NextFunction);
    expect(consoleWarnSpy).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });
});
