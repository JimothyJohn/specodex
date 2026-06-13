/**
 * Per-query paygate on /api/v1/search (and, by the same middleware,
 * /api/v1/relations). Verifies the X-API-Key contract end to end:
 * free without a key, 401/402 gating, metering only on success, and
 * fail-open on a billing outage.
 */

import request from 'supertest';

const mockVerify = jest.fn();
jest.mock('aws-jwt-verify', () => ({
  CognitoJwtVerifier: { create: jest.fn(() => ({ verify: mockVerify })) },
}));

jest.mock('../src/services/stripe', () => ({
  stripeService: {
    verifyApiKey: jest.fn(),
    reportQueryUsage: jest.fn().mockResolvedValue(true),
    createApiKey: jest.fn(),
    getSubscriptionStatus: jest.fn(),
    isSubscriptionActive: jest.fn(),
    createCheckoutSession: jest.fn(),
    reportUsage: jest.fn(),
  },
}));

jest.mock('../src/db/dynamodb');

import app from '../src/index';
import config from '../src/config';
import { stripeService } from '../src/services/stripe';
import { DynamoDBService } from '../src/db/dynamodb';
import { _resetVerifierForTests } from '../src/middleware/auth';

const verifyApiKey = stripeService.verifyApiKey as jest.Mock;
const reportQueryUsage = stripeService.reportQueryUsage as jest.Mock;
const createApiKey = stripeService.createApiKey as jest.Mock;

/** Let the res.on('finish') metering microtask run. */
const tick = () => new Promise((r) => setTimeout(r, 10));

beforeEach(() => {
  jest.clearAllMocks();
  // Search returns 200 with an empty result set by default.
  (DynamoDBService.prototype.list as jest.Mock).mockResolvedValue([]);
});

describe('apiKeyPaygate on /api/v1/search', () => {
  it('no X-API-Key → served free, key never verified, nothing metered', async () => {
    const res = await request(app).get('/api/v1/search?type=motor');
    expect(res.status).toBe(200);
    expect(verifyApiKey).not.toHaveBeenCalled();
    await tick();
    expect(reportQueryUsage).not.toHaveBeenCalled();
  });

  it('unknown key → 401, not metered, query never runs', async () => {
    verifyApiKey.mockResolvedValue({ valid: false });
    const res = await request(app)
      .get('/api/v1/search?type=motor')
      .set('X-API-Key', 'sk_query_bogus');
    expect(res.status).toBe(401);
    expect(DynamoDBService.prototype.list).not.toHaveBeenCalled();
    await tick();
    expect(reportQueryUsage).not.toHaveBeenCalled();
  });

  it('valid key but no active subscription → 402', async () => {
    verifyApiKey.mockResolvedValue({
      valid: true,
      user_id: 'u-1',
      subscription_status: 'past_due',
    });
    const res = await request(app)
      .get('/api/v1/search?type=motor')
      .set('X-API-Key', 'sk_query_real');
    expect(res.status).toBe(402);
    await tick();
    expect(reportQueryUsage).not.toHaveBeenCalled();
  });

  it('valid active key → 200 and exactly one query metered to the owner', async () => {
    verifyApiKey.mockResolvedValue({
      valid: true,
      user_id: 'u-42',
      subscription_status: 'active',
    });
    const res = await request(app)
      .get('/api/v1/search?type=motor')
      .set('X-API-Key', 'sk_query_real');
    expect(res.status).toBe(200);
    await tick();
    expect(reportQueryUsage).toHaveBeenCalledTimes(1);
    expect(reportQueryUsage).toHaveBeenCalledWith('u-42', 1);
  });

  it('failed query (DB error → 500) is NOT metered', async () => {
    verifyApiKey.mockResolvedValue({
      valid: true,
      user_id: 'u-42',
      subscription_status: 'active',
    });
    (DynamoDBService.prototype.list as jest.Mock).mockRejectedValue(new Error('boom'));
    const res = await request(app)
      .get('/api/v1/search?type=motor')
      .set('X-API-Key', 'sk_query_real');
    expect(res.status).toBeGreaterThanOrEqual(500);
    await tick();
    expect(reportQueryUsage).not.toHaveBeenCalled();
  });

  it('billing service outage → fail open to free, not metered', async () => {
    verifyApiKey.mockRejectedValue(new Error('billing unreachable'));
    const res = await request(app)
      .get('/api/v1/search?type=motor')
      .set('X-API-Key', 'sk_query_real');
    expect(res.status).toBe(200);
    await tick();
    expect(reportQueryUsage).not.toHaveBeenCalled();
  });

  it('invalid query params still 400 (paygate runs before validation but key is valid)', async () => {
    verifyApiKey.mockResolvedValue({
      valid: true,
      user_id: 'u-42',
      subscription_status: 'active',
    });
    const res = await request(app)
      .get('/api/v1/search?limit=9999') // limit max is 100
      .set('X-API-Key', 'sk_query_real');
    expect(res.status).toBe(400);
    await tick();
    // 4xx is not a billable query.
    expect(reportQueryUsage).not.toHaveBeenCalled();
  });
});

describe('POST /api/apikeys (mint)', () => {
  const origPoolId = config.cognito.userPoolId;
  const origClientId = config.cognito.userPoolClientId;
  const origUrl = config.stripe.lambdaUrl;

  beforeEach(() => {
    _resetVerifierForTests();
    mockVerify.mockReset();
    config.cognito.userPoolId = 'us-east-1_TEST';
    config.cognito.userPoolClientId = 'test-client-id';
    config.stripe.lambdaUrl = 'https://stripe-lambda.test';
  });

  afterAll(() => {
    config.cognito.userPoolId = origPoolId;
    config.cognito.userPoolClientId = origClientId;
    config.stripe.lambdaUrl = origUrl;
  });

  function authedUser(sub = 'user-123') {
    mockVerify.mockResolvedValue({ sub, email: `${sub}@example.com`, 'cognito:groups': [] });
  }

  it('401 without a token', async () => {
    const res = await request(app).post('/api/apikeys').send({});
    expect(res.status).toBe(401);
    expect(createApiKey).not.toHaveBeenCalled();
  });

  it('mints a key for the authed user, identity from the token', async () => {
    authedUser('user-123');
    createApiKey.mockResolvedValue('sk_query_minted');
    const res = await request(app)
      .post('/api/apikeys')
      .set('Authorization', 'Bearer t')
      .send({});
    expect(res.status).toBe(200);
    expect(res.body.data.api_key).toBe('sk_query_minted');
    expect(createApiKey).toHaveBeenCalledWith('user-123');
  });

  it('rejects a body carrying a user_id (identity is the token, not the body)', async () => {
    authedUser('user-123');
    const res = await request(app)
      .post('/api/apikeys')
      .set('Authorization', 'Bearer t')
      .send({ user_id: 'someone-else' });
    expect(res.status).toBe(400);
    expect(createApiKey).not.toHaveBeenCalled();
  });

  it('503 when billing is not configured', async () => {
    authedUser('user-123');
    config.stripe.lambdaUrl = '';
    const res = await request(app)
      .post('/api/apikeys')
      .set('Authorization', 'Bearer t')
      .send({});
    expect(res.status).toBe(503);
  });
});
