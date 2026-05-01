/**
 * Auth route handlers — proxies to Cognito.
 *
 * Why proxy instead of letting the SPA call Cognito directly: keeps
 * the SDK out of the bundle, gives one place to add rate-limiting and
 * audit logging, and means the frontend only ever talks to one
 * origin. The tradeoff (extra hop on login) is acceptable since
 * login is rare.
 *
 * Endpoints:
 *   POST /api/auth/register   — SignUp
 *   POST /api/auth/confirm    — ConfirmSignUp (email verification code)
 *   POST /api/auth/login      — InitiateAuth (USER_PASSWORD_AUTH)
 *   POST /api/auth/refresh    — InitiateAuth (REFRESH_TOKEN_AUTH)
 *   POST /api/auth/logout     — RevokeToken (refresh-token revocation)
 *   POST /api/auth/forgot     — ForgotPassword
 *   POST /api/auth/reset      — ConfirmForgotPassword
 *   GET  /api/auth/me         — returns the authed user (requireAuth-gated)
 *
 * Cognito error codes are mapped to HTTP statuses sparingly — we
 * surface enough for the UI to render a useful message but don't
 * leak whether an email exists (preventUserExistenceErrors is
 * enabled on the client; Cognito handles most of this for us).
 */

import { Router, Request, Response } from 'express';
import { z } from 'zod';
import {
  CognitoIdentityProviderClient,
  SignUpCommand,
  ConfirmSignUpCommand,
  InitiateAuthCommand,
  ForgotPasswordCommand,
  ConfirmForgotPasswordCommand,
  ResendConfirmationCodeCommand,
  RevokeTokenCommand,
} from '@aws-sdk/client-cognito-identity-provider';
import config from '../config';
import { requireAuth } from '../middleware/auth';

const router = Router();

let cachedClient: CognitoIdentityProviderClient | null = null;
function getClient(): CognitoIdentityProviderClient {
  if (cachedClient) return cachedClient;
  cachedClient = new CognitoIdentityProviderClient({ region: config.aws.region });
  return cachedClient;
}

function ensureConfigured(res: Response): boolean {
  if (!config.cognito.userPoolClientId) {
    res.status(503).json({
      success: false,
      error: 'Auth not configured on this deployment',
    });
    return false;
  }
  return true;
}

const emailSchema = z.string().email().max(254);
// Mirrors the user-pool policy: 12 chars, mixed case, digit. Cognito
// will reject loose passwords on its own; keeping the schema here
// produces a faster, friendlier 400 before we round-trip.
const passwordSchema = z.string()
  .min(12, 'Password must be at least 12 characters')
  .max(256)
  .regex(/[a-z]/, 'Password must contain a lowercase letter')
  .regex(/[A-Z]/, 'Password must contain an uppercase letter')
  .regex(/[0-9]/, 'Password must contain a number');

const registerSchema = z.object({ email: emailSchema, password: passwordSchema });
const confirmSchema = z.object({ email: emailSchema, code: z.string().min(1).max(32) });
const loginSchema = z.object({ email: emailSchema, password: z.string().min(1).max(256) });
const refreshSchema = z.object({ refresh_token: z.string().min(1) });
const logoutSchema = z.object({ refresh_token: z.string().min(1) });
const forgotSchema = z.object({ email: emailSchema });
const resetSchema = z.object({
  email: emailSchema,
  code: z.string().min(1).max(32),
  password: passwordSchema,
});
const resendSchema = z.object({ email: emailSchema });

function badRequest(res: Response, err: z.ZodError): void {
  res.status(400).json({
    success: false,
    error: 'Invalid request body',
    details: err.issues.map(i => ({ path: i.path.join('.'), message: i.message })),
  });
}

function cognitoError(res: Response, err: unknown): void {
  const e = err as { name?: string; message?: string };
  const name = e.name || 'UnknownError';
  // Map a small handful — everything else falls through to 400.
  // Don't echo Cognito messages verbatim; they sometimes leak
  // internal codes.
  const map: Record<string, { status: number; error: string }> = {
    UsernameExistsException: { status: 409, error: 'Account already exists' },
    NotAuthorizedException: { status: 401, error: 'Invalid credentials' },
    UserNotConfirmedException: { status: 403, error: 'Email not verified' },
    CodeMismatchException: { status: 400, error: 'Invalid verification code' },
    ExpiredCodeException: { status: 400, error: 'Verification code expired' },
    InvalidPasswordException: { status: 400, error: 'Password does not meet policy' },
    LimitExceededException: { status: 429, error: 'Too many attempts; try again later' },
    TooManyRequestsException: { status: 429, error: 'Too many requests' },
    UserNotFoundException: { status: 404, error: 'No account for that email' },
  };
  const mapped = map[name];
  if (mapped) {
    res.status(mapped.status).json({ success: false, error: mapped.error });
    return;
  }
  console.error('[auth] unmapped Cognito error:', name, e.message);
  res.status(400).json({ success: false, error: 'Authentication request failed' });
}

router.post('/register', async (req: Request, res: Response) => {
  if (!ensureConfigured(res)) return;
  const parsed = registerSchema.safeParse(req.body);
  if (!parsed.success) return badRequest(res, parsed.error);

  try {
    await getClient().send(new SignUpCommand({
      ClientId: config.cognito.userPoolClientId,
      Username: parsed.data.email,
      Password: parsed.data.password,
      UserAttributes: [{ Name: 'email', Value: parsed.data.email }],
    }));
    res.json({
      success: true,
      data: { message: 'Verification code sent to email', next: 'confirm' },
    });
  } catch (err) {
    cognitoError(res, err);
  }
});

router.post('/confirm', async (req: Request, res: Response) => {
  if (!ensureConfigured(res)) return;
  const parsed = confirmSchema.safeParse(req.body);
  if (!parsed.success) return badRequest(res, parsed.error);

  try {
    await getClient().send(new ConfirmSignUpCommand({
      ClientId: config.cognito.userPoolClientId,
      Username: parsed.data.email,
      ConfirmationCode: parsed.data.code,
    }));
    res.json({ success: true, data: { message: 'Email verified', next: 'login' } });
  } catch (err) {
    cognitoError(res, err);
  }
});

router.post('/resend', async (req: Request, res: Response) => {
  if (!ensureConfigured(res)) return;
  const parsed = resendSchema.safeParse(req.body);
  if (!parsed.success) return badRequest(res, parsed.error);

  try {
    await getClient().send(new ResendConfirmationCodeCommand({
      ClientId: config.cognito.userPoolClientId,
      Username: parsed.data.email,
    }));
    res.json({ success: true, data: { message: 'Verification code resent' } });
  } catch (err) {
    cognitoError(res, err);
  }
});

router.post('/login', async (req: Request, res: Response) => {
  if (!ensureConfigured(res)) return;
  const parsed = loginSchema.safeParse(req.body);
  if (!parsed.success) return badRequest(res, parsed.error);

  try {
    const result = await getClient().send(new InitiateAuthCommand({
      ClientId: config.cognito.userPoolClientId,
      AuthFlow: 'USER_PASSWORD_AUTH',
      AuthParameters: {
        USERNAME: parsed.data.email,
        PASSWORD: parsed.data.password,
      },
    }));
    const auth = result.AuthenticationResult;
    if (!auth) {
      res.status(400).json({
        success: false,
        error: 'Login required additional challenge (MFA not yet supported)',
      });
      return;
    }
    res.json({
      success: true,
      data: {
        id_token: auth.IdToken,
        access_token: auth.AccessToken,
        refresh_token: auth.RefreshToken,
        expires_in: auth.ExpiresIn,
      },
    });
  } catch (err) {
    cognitoError(res, err);
  }
});

router.post('/refresh', async (req: Request, res: Response) => {
  if (!ensureConfigured(res)) return;
  const parsed = refreshSchema.safeParse(req.body);
  if (!parsed.success) return badRequest(res, parsed.error);

  try {
    const result = await getClient().send(new InitiateAuthCommand({
      ClientId: config.cognito.userPoolClientId,
      AuthFlow: 'REFRESH_TOKEN_AUTH',
      AuthParameters: { REFRESH_TOKEN: parsed.data.refresh_token },
    }));
    const auth = result.AuthenticationResult;
    if (!auth) {
      res.status(401).json({ success: false, error: 'Refresh failed' });
      return;
    }
    res.json({
      success: true,
      data: {
        id_token: auth.IdToken,
        access_token: auth.AccessToken,
        // Cognito doesn't rotate refresh tokens here; client keeps
        // the original until it expires (default 30d).
        expires_in: auth.ExpiresIn,
      },
    });
  } catch (err) {
    cognitoError(res, err);
  }
});

/**
 * Revoke the supplied refresh token at Cognito. After this call, no
 * new id/access tokens can be minted from it. Idempotent — a
 * already-revoked or expired token returns 200 too, since the
 * client-side logout proceeds regardless and a 200 from this
 * endpoint just means "Cognito will no longer honor this token,"
 * which is true even if it never honored it in the first place.
 *
 * Not requireAuth-gated: a stolen id token alone shouldn't be
 * what's needed to revoke its own refresh token, and conversely a
 * client that's lost its id token mid-session still wants to be
 * able to invalidate the leaked refresh token. Knowledge of the
 * refresh token itself is the auth.
 */
router.post('/logout', async (req: Request, res: Response) => {
  if (!ensureConfigured(res)) return;
  const parsed = logoutSchema.safeParse(req.body);
  if (!parsed.success) return badRequest(res, parsed.error);

  try {
    await getClient().send(new RevokeTokenCommand({
      ClientId: config.cognito.userPoolClientId,
      Token: parsed.data.refresh_token,
    }));
    res.json({ success: true, data: { message: 'Refresh token revoked' } });
  } catch (err) {
    // Best-effort: a token that was already revoked, expired, or
    // never valid still gets a 200 — the client's local logout
    // proceeds either way, and we don't want a transient SDK error
    // to leave the UI stuck logged-in. The exception is the
    // configuration-class errors (missing pool client ID); those
    // surface as 503 via cognitoError -> ensureConfigured.
    const code = (err as { name?: string })?.name;
    if (code === 'NotAuthorizedException' || code === 'UnsupportedTokenTypeException') {
      res.json({ success: true, data: { message: 'Refresh token revoked (or already invalid)' } });
      return;
    }
    cognitoError(res, err);
  }
});

router.post('/forgot', async (req: Request, res: Response) => {
  if (!ensureConfigured(res)) return;
  const parsed = forgotSchema.safeParse(req.body);
  if (!parsed.success) return badRequest(res, parsed.error);

  try {
    await getClient().send(new ForgotPasswordCommand({
      ClientId: config.cognito.userPoolClientId,
      Username: parsed.data.email,
    }));
    res.json({ success: true, data: { message: 'Reset code sent if account exists' } });
  } catch (err) {
    cognitoError(res, err);
  }
});

router.post('/reset', async (req: Request, res: Response) => {
  if (!ensureConfigured(res)) return;
  const parsed = resetSchema.safeParse(req.body);
  if (!parsed.success) return badRequest(res, parsed.error);

  try {
    await getClient().send(new ConfirmForgotPasswordCommand({
      ClientId: config.cognito.userPoolClientId,
      Username: parsed.data.email,
      ConfirmationCode: parsed.data.code,
      Password: parsed.data.password,
    }));
    res.json({ success: true, data: { message: 'Password reset; you can now log in' } });
  } catch (err) {
    cognitoError(res, err);
  }
});

router.get('/me', requireAuth, (req: Request, res: Response) => {
  res.json({ success: true, data: req.user });
});

export default router;
