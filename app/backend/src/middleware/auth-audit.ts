/**
 * Structured logger for /api/auth/* events. CloudWatch picks up
 * stdout from Lambda; metric filters can extract by JSON field.
 *
 * Format: `AUTH_EVENT {json}` — the prefix lets plain-text filters
 * grep without false positives on other [auth] log lines, and the
 * JSON body lets `{ $.event = "login" && $.success = false }`-style
 * structured filters work in CloudWatch.
 *
 * What gets logged: event name, success flag, email (the username,
 * not a secret), the authed sub when present, request IP, user
 * agent, the Cognito error name on failures, and the wall-clock
 * duration. **Never** log the password, verification code, or any
 * JWT — even on failure paths.
 *
 * What gets alarmed (Phase 5b "Layer 4 — visibility"):
 *   - login failures > 50/min     → page (likely credential stuffing)
 *   - register events > 20/hour   → page (likely registration burst)
 *   - any single IP > 1000 read req/hour → notify (manual review)
 *
 * Alarms themselves live in CDK (a follow-up branch); this file is
 * only the emitter.
 */

import { Request } from 'express';

export type AuthEventName =
  | 'register'
  | 'confirm'
  | 'resend'
  | 'login'
  | 'refresh'
  | 'logout'
  | 'forgot'
  | 'reset';

export interface AuthAuditEvent {
  event: AuthEventName;
  success: boolean;
  /** Lowercased email; safe to log (this is the Cognito username,
   *  not a secret). Useful signal: alarm on >K registrations from
   *  the same address. */
  email?: string;
  /** Cognito sub when the request was authenticated. Present on
   *  /me, /logout, etc.; absent on register/login/forgot. */
  sub?: string;
  /** Best-effort client IP (X-Forwarded-For first hop, then
   *  req.ip). Behind CloudFront the X-F-F header is set. */
  ip?: string;
  /** Truncated to 256 chars to keep log lines bounded. */
  userAgent?: string;
  /** Cognito error name on failures (e.g. NotAuthorizedException,
   *  UsernameExistsException). Empty string when the failure was
   *  request validation, not an upstream error. */
  errorCode?: string;
  /** Wall-clock duration ms. Useful for spotting slow-roll
   *  credential stuffing. */
  durationMs?: number;
}

const PREFIX = 'AUTH_EVENT ';
const MAX_UA = 256;

function clientIp(req: Request): string | undefined {
  // CloudFront sets X-Forwarded-For with the first hop being the
  // real client IP. Behind ALB / API Gateway the same header is
  // populated with the same convention.
  const xff = req.headers['x-forwarded-for'];
  if (typeof xff === 'string') {
    const first = xff.split(',')[0]?.trim();
    if (first) return first;
  }
  if (Array.isArray(xff) && xff.length > 0) {
    return xff[0];
  }
  return req.ip;
}

function userAgent(req: Request): string | undefined {
  const ua = req.headers['user-agent'];
  if (typeof ua !== 'string') return undefined;
  return ua.length > MAX_UA ? ua.slice(0, MAX_UA) : ua;
}

/**
 * Emit a structured auth event. Never throws — logging is
 * fire-and-forget; a serialization failure must not break the
 * request.
 */
export function emitAuthEvent(e: AuthAuditEvent): void {
  try {
    // eslint-disable-next-line no-console
    console.log(PREFIX + JSON.stringify(e));
  } catch {
    // ignore — logging shouldn't be load-bearing on the response
  }
}

/**
 * Convenience helper: build the request-derived fields
 * (ip, userAgent) so handlers don't have to thread them through.
 * Pass the result alongside event-specific fields:
 *
 *   emitAuthEvent({
 *     ...auditMeta(req),
 *     event: 'login',
 *     success: true,
 *     email: parsed.data.email,
 *     durationMs: Date.now() - t0,
 *   });
 */
export function auditMeta(req: Request): Pick<AuthAuditEvent, 'ip' | 'userAgent'> {
  return {
    ip: clientIp(req),
    userAgent: userAgent(req),
  };
}
