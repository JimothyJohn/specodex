/**
 * Per-query paygate for programmatic API consumers.
 *
 * Mount in front of the billable read routes (search, relations). The
 * contract is keyed entirely on the `X-API-Key` header:
 *
 *   - No header          → public/UI traffic. Passes through free,
 *                          unchanged. The browsable site never sends a
 *                          key, so it is never gated.
 *   - Header, unknown    → 401. A present-but-invalid key is a client
 *                          error worth surfacing, not silent free-tier.
 *   - Header, no active  → 402 Payment Required. Key is real but the
 *     subscription          owner has no active subscription.
 *   - Header, active     → served, then +1 query reported to Stripe
 *                          AFTER a successful (<400) response, so failed
 *                          requests are never billed.
 *
 * Availability bias: if the billing service is unreachable we fail OPEN
 * to the free path (log + passthrough) rather than 500 the read API.
 * The worst case is un-billed queries during an outage, never an
 * outage-induced denial of a read endpoint.
 */

import { Request, Response, NextFunction } from 'express';
import { stripeService } from '../services/stripe';

const API_KEY_HEADER = 'x-api-key';

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      // Set by apiKeyPaygate when a valid, subscribed key was presented.
      apiKeyUserId?: string;
    }
  }
}

export async function apiKeyPaygate(
  req: Request,
  res: Response,
  next: NextFunction,
): Promise<void> {
  const headerVal = req.header(API_KEY_HEADER);
  const apiKey = Array.isArray(headerVal) ? headerVal[0] : headerVal;

  // No key → free public path. Untouched behavior for the web UI.
  if (!apiKey) {
    next();
    return;
  }

  let verification;
  try {
    verification = await stripeService.verifyApiKey(apiKey);
  } catch (e) {
    // Billing service outage → fail open to free rather than 500 the
    // read API. Un-billed, but available.
    console.error('API key verification unavailable; serving free:', e);
    next();
    return;
  }

  if (!verification.valid || !verification.user_id) {
    res.status(401).json({ success: false, error: 'Invalid API key' });
    return;
  }

  if (verification.subscription_status !== 'active') {
    res.status(402).json({
      success: false,
      error: 'Active subscription required for API access. Subscribe at /api/subscription/checkout.',
    });
    return;
  }

  const userId = verification.user_id;
  req.apiKeyUserId = userId;

  // Meter AFTER the response is sent, and only on success — a 4xx/5xx
  // query isn't a billable query. Fire-and-forget: metering must never
  // delay or fail the response the user already received.
  res.on('finish', () => {
    if (res.statusCode < 400) {
      void stripeService.reportQueryUsage(userId, 1);
    }
  });

  next();
}
