/**
 * Request-logging middleware.
 *
 * The `.replace(/\r|\n/g, '')` is the CodeQL `js/log-injection` barrier —
 * see `util/log.ts` for why it must appear inline at the call site rather
 * than inside `safeLog`. CodeQL's sanitizer recognizes the replace only
 * when it sits on the data-flow path to the `console.*` sink.
 *
 * Extracted from inline in `index.ts` so the CR/LF barrier is unit-testable
 * without driving through Express's URL parser (which silently preserves
 * percent-encoded forms like `%0D%0A` in `req.path`, masking the barrier
 * during HTTP-level tests).
 */

import { Request, Response, NextFunction } from 'express';
import config from '../config';
import { safeLog } from '../util/log';

export function requestLogger(req: Request, _res: Response, next: NextFunction): void {
  console.log(`[${config.appMode}] ${req.method} ${safeLog(req.path.replace(/\r|\n/g, ''))}`);
  next();
}
