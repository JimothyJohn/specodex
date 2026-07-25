/**
 * API routes for subscription management.
 * Proxies requests to the Stripe payments Lambda.
 *
 * All routes are auth-gated: identity comes from the verified JWT
 * (req.user.sub), never from a path param or request body. The Stripe
 * Lambda's wire format still uses `user_id`; that value is now sourced
 * from the token, not the client.
 */

import { Router, Request, Response } from 'express';
import { z } from 'zod';
import { stripeService } from '../services/stripe';
import { requireAuth } from '../middleware/auth';
import config from '../config';

const router = Router();

/**
 * GET /api/subscription/status
 * Check subscription status for the authed user.
 */
router.get('/status', requireAuth, async (req: Request, res: Response) => {
  try {
    if (!config.stripe.lambdaUrl) {
      res.json({
        success: true,
        data: { subscription_status: 'none', billing_enabled: false },
      });
      return;
    }

    const status = await stripeService.getSubscriptionStatus(req.user!.sub);
    res.json({ success: true, data: status });
  } catch (error) {
    // Upstream (Stripe) error messages can embed credentials, so neither
    // the client response nor the log carries the raw message — log only
    // the error class. Pinned by tests/log-leak.test.ts.
    console.error(
      'Error checking subscription status:',
      error instanceof Error ? error.name : typeof error,
    );
    res.status(500).json({ success: false, error: 'Failed to check subscription status' });
  }
});

/**
 * POST /api/subscription/checkout
 * Create a Stripe checkout session for the authed user.
 *
 * Body must be empty / {}; any user_id field is rejected. Identity
 * comes from the token. The strict schema makes the negative test
 * (old-style body with user_id → 400) explicit.
 */
const checkoutBodySchema = z.object({}).strict();

router.post('/checkout', requireAuth, async (req: Request, res: Response): Promise<void> => {
  try {
    const parsed = checkoutBodySchema.safeParse(req.body ?? {});
    if (!parsed.success) {
      res.status(400).json({
        success: false,
        error: 'Request body must be empty; identity is taken from the auth token',
      });
      return;
    }

    const result = await stripeService.createCheckoutSession(req.user!.sub);
    res.json({ success: true, data: result });
  } catch (error) {
    // Same leak shape as /status above — generic string out, class-only log.
    console.error(
      'Error creating checkout session:',
      error instanceof Error ? error.name : typeof error,
    );
    res.status(500).json({ success: false, error: 'Failed to create checkout session' });
  }
});

/**
 * GET /api/subscription/config
 * Returns whether billing is enabled (useful for frontend conditional rendering).
 * Public — used by the frontend before any user is authed.
 */
router.get('/config', (_req: Request, res: Response) => {
  res.json({
    success: true,
    data: {
      billing_enabled: !!config.stripe.lambdaUrl,
    },
  });
});

export default router;
