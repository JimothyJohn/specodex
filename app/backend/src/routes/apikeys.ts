/**
 * API-key management for per-query billing.
 *
 * POST /api/apikeys — mint a key for the authed user. Identity comes
 * from the verified JWT (req.user.sub), never the body, mirroring the
 * subscription routes. The plaintext key is returned exactly once.
 */

import { Router, Request, Response } from 'express';
import { z } from 'zod';
import { stripeService } from '../services/stripe';
import { requireAuth } from '../middleware/auth';
import config from '../config';

const router = Router();

// Body must be empty; identity is the token. Makes the negative test
// (a body carrying user_id → 400) explicit, same pattern as checkout.
const emptyBodySchema = z.object({}).strict();

router.post('/', requireAuth, async (req: Request, res: Response): Promise<void> => {
  try {
    if (!config.stripe.lambdaUrl) {
      res.status(503).json({ success: false, error: 'Billing is not configured' });
      return;
    }
    const parsed = emptyBodySchema.safeParse(req.body ?? {});
    if (!parsed.success) {
      res.status(400).json({
        success: false,
        error: 'Request body must be empty; identity is taken from the auth token',
      });
      return;
    }

    const apiKey = await stripeService.createApiKey(req.user!.sub);
    // Returned once; the caller must store it now. Only its hash is
    // persisted upstream, so it cannot be re-shown.
    res.json({ success: true, data: { api_key: apiKey } });
  } catch (error: any) {
    console.error('Error creating API key:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

export default router;
