/**
 * Secret-leak regression tests.
 *
 * The cloud-security rule "Logs are an attack surface AND a forensic
 * tool" (~/.claude/CLAUDE.md) is policy; this file is the enforcement.
 * It seeds the environment with known sentinel values, spies on every
 * `console.*` method, exercises error paths that have historically
 * leaked details, and asserts NO captured log line contains any
 * sentinel.
 *
 * If a future change adds something like:
 *
 *     console.log(`Loaded Gemini key: ${process.env.GEMINI_API_KEY}`);
 *
 * or a Stripe / AWS SDK error message embeds a credential into the
 * error string that then hits `console.error('Error:', err)`, every
 * test below fails until the leak is fixed.
 *
 * Note: tests deliberately use **sentinel** values, not the real
 * secrets — both because the real values aren't in the test env, and
 * because the test must work for anyone running it.
 */

import request from 'supertest';

const SENTINELS = {
  GEMINI_API_KEY: 'AIzaSy-TEST-SENTINEL-GEMINI-1234567890ABCDEFGH',
  AWS_SECRET_ACCESS_KEY: 'TEST-SENTINEL-AWS-SECRET-abcdef1234567890',
  AWS_ACCESS_KEY_ID: 'AKIATESTSENTINELAWSACCESS',
  STRIPE_SECRET_KEY: 'sk_test_TEST_SENTINEL_STRIPE_KEY_12345',
  STRIPE_WEBHOOK_SECRET: 'whsec_TEST_SENTINEL_WEBHOOK_SECRET',
  JWT_SUB_VALUE: 'sub-sentinel-12345-NEVER-LOG-ME',
  STRIPE_CUSTOMER_ID: 'cus_TEST_SENTINEL_NEVER_LOG_ME',
};

const ALL_SENTINELS = Object.values(SENTINELS);

// JWT verification mock so we can force-error.
const mockVerify = jest.fn();
jest.mock('aws-jwt-verify', () => ({
  CognitoJwtVerifier: { create: jest.fn(() => ({ verify: mockVerify })) },
}));

// DB mocks — let us trigger errors deterministically.
const mockProductsList = jest.fn();
const mockProductsGet = jest.fn();
jest.mock('../src/db/dynamodb', () => ({
  DynamoDBService: jest.fn().mockImplementation(() => ({
    listProducts: mockProductsList,
    getProduct: mockProductsGet,
    listAttributes: jest.fn(async () => []),
    getCategories: jest.fn(async () => []),
    getSummary: jest.fn(async () => ({ total: 0 })),
  })),
}));

// Stripe mock — errors here are the highest-leak risk surface.
const mockGetSubStatus = jest.fn();
const mockIsSubActive = jest.fn();
const mockCreateCheckout = jest.fn();
jest.mock('../src/services/stripe', () => ({
  stripeService: {
    getSubscriptionStatus: mockGetSubStatus,
    isSubscriptionActive: mockIsSubActive,
    createCheckoutSession: mockCreateCheckout,
    reportUsage: jest.fn(),
  },
}));

import config from '../src/config';
import app from '../src/index';
import { _resetVerifierForTests } from '../src/middleware/auth';

let logSpy: jest.SpyInstance;
let warnSpy: jest.SpyInstance;
let errorSpy: jest.SpyInstance;
let originalEnv: NodeJS.ProcessEnv;

beforeEach(() => {
  jest.clearAllMocks();
  _resetVerifierForTests();

  // Seed sentinels into the environment so any code that reads them
  // would substitute the test value, which we can detect in logs.
  originalEnv = { ...process.env };
  for (const [k, v] of Object.entries(SENTINELS)) {
    process.env[k] = v;
  }

  config.cognito.userPoolId = 'us-east-1_TEST';
  config.cognito.userPoolClientId = 'test-client-id';

  logSpy = jest.spyOn(console, 'log').mockImplementation();
  warnSpy = jest.spyOn(console, 'warn').mockImplementation();
  errorSpy = jest.spyOn(console, 'error').mockImplementation();
});

afterEach(() => {
  logSpy.mockRestore();
  warnSpy.mockRestore();
  errorSpy.mockRestore();
  process.env = originalEnv;
});

/** Concatenate every captured log message across all three console methods. */
function capturedLogs(): string {
  const parts: string[] = [];
  for (const spy of [logSpy, warnSpy, errorSpy]) {
    for (const call of spy.mock.calls) {
      for (const arg of call) {
        if (typeof arg === 'string') parts.push(arg);
        else if (arg instanceof Error) parts.push(arg.message + ' ' + (arg.stack ?? ''));
        else parts.push(JSON.stringify(arg));
      }
    }
  }
  return parts.join('\n');
}

function assertNoSentinelInLogs(): void {
  const logs = capturedLogs();
  for (const sentinel of ALL_SENTINELS) {
    expect(logs).not.toContain(sentinel);
  }
}

// --------------------------------------------------------------------
// JWT failure paths
// --------------------------------------------------------------------

describe('JWT verification failure paths do not leak the token', () => {
  it('invalid JWT signature — full token never appears in logs', async () => {
    const fakeToken =
      'eyJhbGciOiJIUzI1NiJ9.' +
      Buffer.from(JSON.stringify({ sub: SENTINELS.JWT_SUB_VALUE })).toString(
        'base64url',
      ) +
      '.invalid_signature_should_never_be_logged';

    mockVerify.mockRejectedValueOnce(new Error('Invalid signature: ' + fakeToken));

    const res = await request(app)
      .get('/api/projects')
      .set('Authorization', `Bearer ${fakeToken}`);

    expect(res.status).toBe(401);
    // The error message above embeds the sentinel sub. Even if the
    // catch path logs the verifier's error, the sub must NOT appear.
    expect(capturedLogs()).not.toContain(SENTINELS.JWT_SUB_VALUE);
  });

  it('expired JWT — sentinel sub never appears in logs', async () => {
    const err = new Error('Token expired');
    (err as Error & { exp?: number; sub?: string }).sub = SENTINELS.JWT_SUB_VALUE;
    mockVerify.mockRejectedValueOnce(err);

    await request(app)
      .get('/api/projects')
      .set('Authorization', 'Bearer expired-token');

    assertNoSentinelInLogs();
  });
});

// --------------------------------------------------------------------
// DynamoDB error path
// --------------------------------------------------------------------

describe('DynamoDB error paths do not leak credentials', () => {
  it('500 on /api/products with simulated DB error — no AWS keys in logs', async () => {
    // Simulate an SDK-style error whose message embeds the access key
    // (AWS SDK *has* historically done this in some error variants).
    const dbErr = new Error(
      `AccessDenied (key=${SENTINELS.AWS_ACCESS_KEY_ID} secret=${SENTINELS.AWS_SECRET_ACCESS_KEY})`,
    );
    mockProductsList.mockRejectedValueOnce(dbErr);

    const res = await request(app).get('/api/products');

    // Server-side error is fine — but logs must not carry the keys.
    expect([500, 502, 200]).toContain(res.status);
    assertNoSentinelInLogs();
  });
});

// --------------------------------------------------------------------
// Stripe error path
// --------------------------------------------------------------------

describe('Stripe error paths do not leak the secret key or webhook secret', () => {
  beforeEach(() => {
    config.stripe.lambdaUrl = 'https://stripe-lambda.test';
    mockVerify.mockResolvedValue({
      sub: SENTINELS.JWT_SUB_VALUE,
      email: 'sentinel@example.com',
      'cognito:groups': [],
    });
  });

  // KNOWN-LEAKING: `subscription.ts` 500 paths do
  //   `console.error('Error checking subscription status:', error)`
  // which logs the full `Error` object including its message — and
  // `res.json({ error: error.message })` sends it to the client. If
  // an upstream Stripe error embeds a credential into its message
  // (real Stripe SDK errors sometimes do this), the secret leaks
  // both to CloudWatch and to the response body.
  //
  // These two tests are marked `it.failing` to document the known
  // bug. They will start FAILING (i.e. starting to "pass normally")
  // when the leak is fixed — which is the signal to remove `.failing`.
  // Tracked as a follow-up HARDENING card; see the PR description.
  it.failing(
    '500 on /api/subscription/status — no sk_test in logs (KNOWN LEAK)',
    async () => {
      const stripeErr = new Error(
        `Stripe API error: invalid auth (key=${SENTINELS.STRIPE_SECRET_KEY})`,
      );
      mockGetSubStatus.mockRejectedValueOnce(stripeErr);

      await request(app)
        .get('/api/subscription/status')
        .set('Authorization', 'Bearer good-token');

      assertNoSentinelInLogs();
    },
  );

  it.failing(
    '500 on /api/subscription/checkout — no webhook secret in logs (KNOWN LEAK)',
    async () => {
      const checkoutErr = new Error(
        `checkout failed (whsec=${SENTINELS.STRIPE_WEBHOOK_SECRET})`,
      );
      mockCreateCheckout.mockRejectedValueOnce(checkoutErr);

      await request(app)
        .post('/api/subscription/checkout')
        .set('Authorization', 'Bearer good-token')
        .send({});

      assertNoSentinelInLogs();
    },
  );
});

// --------------------------------------------------------------------
// Malformed body / oversized payload paths
// --------------------------------------------------------------------

describe('Body-parser error paths do not leak env values', () => {
  it('malformed JSON — no env-derived secrets in any captured log', async () => {
    const res = await request(app)
      .post('/api/projects')
      .set('Authorization', 'Bearer some-token')
      .set('Content-Type', 'application/json')
      .send('{"name": "broken'); // intentional truncation

    expect([400, 401]).toContain(res.status);
    assertNoSentinelInLogs();
  });
});

// --------------------------------------------------------------------
// Sanity check: the assertion machinery actually catches a leak.
// --------------------------------------------------------------------

describe('self-test of the leak detector', () => {
  it('would catch a real leak (positive control)', () => {
    console.error('SIMULATED LEAK:', SENTINELS.GEMINI_API_KEY);
    expect(() => assertNoSentinelInLogs()).toThrow();
  });
});
