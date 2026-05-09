/**
 * Readonly middleware — enforces read-only access in public mode.
 *
 * When APP_MODE=public, all non-GET/HEAD/OPTIONS requests are rejected
 * with 403, except for explicitly allowed paths (e.g., /api/upload).
 * This is the hard boundary between the public cloud deployment
 * and the local admin toolset.
 */

import { Request, Response, NextFunction } from 'express';
import { safeLog } from '../util/log';

const ALLOWED_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

// Paths exempt from readonly — these only queue work, they don't mutate existing data
const WRITE_ALLOWED_PATHS = new Set(['/upload', '/upload/']);

// Path prefixes exempt from readonly. Auth endpoints (register, login,
// password reset, etc.) need POST in public mode but don't mutate
// product data — the user table is Cognito's, not ours. Projects are
// per-user data; the route enforces ownership via requireAuth.
const WRITE_ALLOWED_PREFIXES = ['/auth/', '/projects'];

export function readonlyGuard(req: Request, res: Response, next: NextFunction): void {
  if (ALLOWED_METHODS.has(req.method)) {
    next();
    return;
  }

  // Allow specific write paths in public mode (e.g., PDF upload queue)
  if (WRITE_ALLOWED_PATHS.has(req.path)) {
    next();
    return;
  }

  if (WRITE_ALLOWED_PREFIXES.some(prefix => req.path.startsWith(prefix))) {
    next();
    return;
  }

  console.warn(`[readonly] Blocked ${req.method} ${safeLog(req.path)} — public mode is read-only`);
  res.status(403).json({
    success: false,
    error: 'This endpoint is read-only in public mode',
    hint: 'Use the local admin toolset for write operations',
  });
}
