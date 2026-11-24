/**
 * Real-DAL integration tests for the subscription / paygate boundary.
 *
 * Sibling to the mocked `tests/subscription.test.ts` and
 * `tests/apiKeyPaygate.test.ts`. Both do `jest.mock('../src/db/dynamodb')`,
 * so every request they drive runs against a stubbed DAL whose `list()`
 * resolves `[]` by default. That makes three contracts invisible to them:
 *
 *   1. **The subscription routes are DAL-inert.** `/config`, `/status` and
 *      `/checkout` are pure proxies to the Stripe Lambda and must never
 *      touch the products table. Against a stub, a route that started
 *      scanning the catalog would look identical to one that didn't.
 *
 *   2. **`requireSubscription`'s 401/403 is a *pre-handler* stop.** The
 *      mocked sibling drives the middleware with a hand-rolled fake `res`
 *      and a `next` that flips a boolean, so "next was not called" is the
 *      whole assertion — it cannot see whether a downstream DAL-backed
 *      handler would have leaked rows. Here the middleware is stacked in
 *      front of a handler that really reads the seeded table.
 *
 *   3. **The paygate's *pass* paths serve real data.** The mocked paygate
 *      suite asserts `status === 200` while `list()` returns `[]`, so a
 *      regression that admitted the request but truncated the result set
 *      stays green. Every pass path here (no key / active key / billing
 *      outage fail-open) asserts the actual seeded rows come back, and
 *      every gated path asserts zero catalog rows reach the client.
 *
 * This is HARDENING Phase 2.2.b — the `subscription` entry in the
 * "migrate the mocked backend tests to real-DAL" follow-up. Runs via
 * `npm run test:integration` (DynamoDB Local booted by
 * @shelf/jest-dynamodb's globalSetup); excluded from the default
 * `npm test` unit run.
 *
 * Stripe stays mocked — it is an external HTTP dependency, not a DAL.
 * `aws-jwt-verify` stays mocked for the same reason the other real-DAL
 * files mock it: the surface under test is what crosses the DB boundary,
 * not JWT verification (the mocked sibling pins the 401-on-bad-token
 * contract).
 */

import express, { Application, Request, Response } from 'express';
import request from 'supertest';
import {
  DynamoDBClient,
  ScanCommand,
  DeleteItemCommand,
} from '@aws-sdk/client-dynamodb';
import { unmarshall } from '@aws-sdk/util-dynamodb';
import { DynamoDBService } from '../../src/db/dynamodb';
import { Product } from '../../src/types/models';

const TABLE_NAME = 'specodex-test';
const ENDPOINT = process.env.MOCK_DYNAMODB_ENDPOINT ?? 'http://localhost:8000';

// Point the routes' internally-constructed DAL at DynamoDB Local via the
// AWS SDK's native env override. Must be set before `src/index` (and its
// `src/config`) is required, so the app is loaded lazily in beforeAll().
process.env.DYNAMODB_TABLE_NAME = TABLE_NAME;
process.env.AWS_ENDPOINT_URL_DYNAMODB = ENDPOINT;
process.env.AWS_REGION = 'us-east-1';
process.env.AWS_ACCESS_KEY_ID = 'local';
process.env.AWS_SECRET_ACCESS_KEY = 'local';

// The auth middleware 503s outright when the Cognito IDs are unset. Real
// values don't matter — the verifier itself is mocked below.
process.env.COGNITO_USER_POOL_ID = 'us-east-1_TEST';
process.env.COGNITO_USER_POOL_CLIENT_ID = 'test-client-id';

const mockVerify = jest.fn();
jest.mock('aws-jwt-verify', () => ({
  CognitoJwtVerifier: { create: jest.fn(() => ({ verify: mockVerify })) },
}));

jest.mock('../../src/services/stripe', () => ({
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

import config from '../../src/config';
import { stripeService } from '../../src/services/stripe';
import { requireAuth, _resetVerifierForTests } from '../../src/middleware/auth';
import { requireSubscription } from '../../src/middleware/subscription';

const verifyApiKey = stripeService.verifyApiKey as jest.Mock;
const reportQueryUsage = stripeService.reportQueryUsage as jest.Mock;
const isSubscriptionActive = stripeService.isSubscriptionActive as jest.Mock;
const getSubscriptionStatus = stripeService.getSubscriptionStatus as jest.Mock;
const createCheckoutSession = stripeService.createCheckoutSession as jest.Mock;

/** Let the `res.on('finish')` metering microtask run. */
const tick = () => new Promise((r) => setTimeout(r, 10));

function seedDb(): DynamoDBService {
  return new DynamoDBService({
    tableName: TABLE_NAME,
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: { accessKeyId: 'local', secretAccessKey: 'local' },
  });
}

function rawClient(): DynamoDBClient {
  return new DynamoDBClient({
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: { accessKeyId: 'local', secretAccessKey: 'local' },
  });
}

async function truncateTable(): Promise<void> {
  const client = rawClient();
  const scan = await client.send(
    new ScanCommand({ TableName: TABLE_NAME, ProjectionExpression: 'PK, SK' }),
  );
  for (const item of scan.Items ?? []) {
    await client.send(
      new DeleteItemCommand({
        TableName: TABLE_NAME,
        Key: { PK: item.PK!, SK: item.SK! },
      }),
    );
  }
}

/**
 * Every stored item, key-sorted and JSON-stringified — a byte-level
 * snapshot for "the table is unchanged" assertions.
 */
async function tableSnapshot(): Promise<string> {
  const client = rawClient();
  const scan = await client.send(new ScanCommand({ TableName: TABLE_NAME }));
  const rows = (scan.Items ?? [])
    .map((item) => unmarshall(item))
    .sort((a, b) => `${a.PK}|${a.SK}`.localeCompare(`${b.PK}|${b.SK}`));
  return JSON.stringify(rows);
}

/** The two motors every suite seeds, so "real rows came back" is checkable. */
async function seedMotors(db: DynamoDBService): Promise<void> {
  await db.create({
    product_id: 'paygate-m-1',
    product_type: 'motor',
    manufacturer: 'ABB',
    part_number: 'PG-001',
  } as unknown as Product);
  await db.create({
    product_id: 'paygate-m-2',
    product_type: 'motor',
    manufacturer: 'Siemens',
    part_number: 'PG-002',
  } as unknown as Product);
}

/** Product ids present anywhere in a response body, however nested. */
function leakedProductIds(body: unknown): string[] {
  const found: string[] = [];
  const walk = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (node && typeof node === 'object') {
      for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
        if (key === 'product_id' && typeof value === 'string') found.push(value);
        else walk(value);
      }
    }
  };
  walk(body);
  return found.sort();
}

function setAuthedUser(sub: string): void {
  mockVerify.mockResolvedValue({
    sub,
    email: `${sub}@example.com`,
    'cognito:groups': [],
  });
}

describe('subscription / paygate — real-DAL integration', () => {
  let app: Application;
  let db: DynamoDBService;

  const origLambdaUrl = config.stripe.lambdaUrl;

  beforeAll(() => {
    // Lazy require so the env overrides above are in place before the
    // route modules construct their DynamoDBService at import time.
    app = require('../../src/index').default as Application;
  });

  beforeEach(async () => {
    jest.clearAllMocks();
    _resetVerifierForTests();
    mockVerify.mockReset();
    reportQueryUsage.mockResolvedValue(true);
    config.stripe.lambdaUrl = origLambdaUrl;
    await truncateTable();
    db = seedDb();
    await seedMotors(db);
  });

  afterAll(() => {
    config.stripe.lambdaUrl = origLambdaUrl;
  });

  // ============ 1. The subscription routes never touch the table ============

  describe('subscription routes are DAL-inert', () => {
    it('GET /config neither reads nor writes the catalog', async () => {
      const before = await tableSnapshot();

      const res = await request(app).get('/api/subscription/config');

      expect(res.status).toBe(200);
      expect(res.body.data).toHaveProperty('billing_enabled');
      expect(leakedProductIds(res.body)).toEqual([]);
      expect(await tableSnapshot()).toBe(before);
    });

    it('GET /status (billing unconfigured) leaves the table byte-identical', async () => {
      setAuthedUser('user-123');
      config.stripe.lambdaUrl = '';
      const before = await tableSnapshot();

      const res = await request(app)
        .get('/api/subscription/status')
        .set('Authorization', 'Bearer good-token');

      expect(res.status).toBe(200);
      expect(res.body.data.subscription_status).toBe('none');
      expect(leakedProductIds(res.body)).toEqual([]);
      expect(await tableSnapshot()).toBe(before);
    });

    it('GET /status (billing configured) leaves the table byte-identical', async () => {
      setAuthedUser('user-123');
      config.stripe.lambdaUrl = 'https://stripe-lambda.test';
      getSubscriptionStatus.mockResolvedValue({
        user_id: 'user-123',
        subscription_status: 'active',
      });
      const before = await tableSnapshot();

      const res = await request(app)
        .get('/api/subscription/status')
        .set('Authorization', 'Bearer good-token');

      expect(res.status).toBe(200);
      expect(getSubscriptionStatus).toHaveBeenCalledWith('user-123');
      expect(leakedProductIds(res.body)).toEqual([]);
      expect(await tableSnapshot()).toBe(before);
    });

    it('POST /checkout leaves the table byte-identical on success and on 400', async () => {
      setAuthedUser('user-123');
      createCheckoutSession.mockResolvedValue({
        checkout_url: 'https://checkout.stripe.com/session123',
      });
      const before = await tableSnapshot();

      const ok = await request(app)
        .post('/api/subscription/checkout')
        .set('Authorization', 'Bearer good-token')
        .send({});
      expect(ok.status).toBe(200);
      expect(await tableSnapshot()).toBe(before);

      // Legacy body carrying a user_id → 400, still no write.
      const rejected = await request(app)
        .post('/api/subscription/checkout')
        .set('Authorization', 'Bearer good-token')
        .send({ user_id: 'someone-else' });
      expect(rejected.status).toBe(400);
      expect(await tableSnapshot()).toBe(before);
    });

    it('an unauthenticated sweep of every subscription route writes nothing', async () => {
      const before = await tableSnapshot();

      const status = await request(app).get('/api/subscription/status');
      const checkout = await request(app).post('/api/subscription/checkout').send({});

      expect(status.status).toBe(401);
      expect(checkout.status).toBe(401);
      expect(await tableSnapshot()).toBe(before);
    });
  });

  // ====== 2. requireSubscription stops the request BEFORE the DAL runs ======

  describe('requireSubscription in front of a DAL-backed handler', () => {
    let gatedApp: Application;
    let handlerRuns: number;

    beforeEach(() => {
      handlerRuns = 0;
      gatedApp = express();
      gatedApp.use(express.json());
      gatedApp.get(
        '/gated',
        requireAuth,
        requireSubscription,
        async (_req: Request, res: Response) => {
          handlerRuns += 1;
          const products = await db.list('motor');
          res.json({ success: true, data: products });
        },
      );
    });

    it('no token → 401, the handler never runs and no rows are read', async () => {
      const res = await request(gatedApp).get('/gated');

      expect(res.status).toBe(401);
      expect(handlerRuns).toBe(0);
      expect(leakedProductIds(res.body)).toEqual([]);
      expect(isSubscriptionActive).not.toHaveBeenCalled();
    });

    it('authed but no active subscription → 403 with zero catalog rows', async () => {
      setAuthedUser('user-123');
      isSubscriptionActive.mockResolvedValue(false);

      const res = await request(gatedApp)
        .get('/gated')
        .set('Authorization', 'Bearer good-token');

      expect(res.status).toBe(403);
      expect(isSubscriptionActive).toHaveBeenCalledWith('user-123');
      // The point of the real table: there ARE rows to leak, and none did.
      expect(handlerRuns).toBe(0);
      expect(leakedProductIds(res.body)).toEqual([]);
    });

    it('authed with an active subscription → the real seeded rows come back', async () => {
      setAuthedUser('user-123');
      isSubscriptionActive.mockResolvedValue(true);

      const res = await request(gatedApp)
        .get('/gated')
        .set('Authorization', 'Bearer good-token');

      expect(res.status).toBe(200);
      expect(handlerRuns).toBe(1);
      expect(leakedProductIds(res.body)).toEqual(['paygate-m-1', 'paygate-m-2']);
    });
  });

  // ========= 3. apiKeyPaygate on /api/v1/search, against real rows =========

  describe('apiKeyPaygate on /api/v1/search', () => {
    it('no X-API-Key → free path serves the real seeded rows, nothing metered', async () => {
      const res = await request(app).get('/api/v1/search?type=motor');

      expect(res.status).toBe(200);
      // The mocked sibling stubs list() → [], so its 200 cannot tell
      // "served free" from "served nothing". This can.
      expect(leakedProductIds(res.body)).toEqual(['paygate-m-1', 'paygate-m-2']);
      expect(verifyApiKey).not.toHaveBeenCalled();
      await tick();
      expect(reportQueryUsage).not.toHaveBeenCalled();
    });

    it('unknown key → 401 and not one catalog row crosses the boundary', async () => {
      verifyApiKey.mockResolvedValue({ valid: false });

      const res = await request(app)
        .get('/api/v1/search?type=motor')
        .set('X-API-Key', 'sk_query_bogus');

      expect(res.status).toBe(401);
      expect(leakedProductIds(res.body)).toEqual([]);
      await tick();
      expect(reportQueryUsage).not.toHaveBeenCalled();
    });

    it('valid key without an active subscription → 402 and no catalog rows', async () => {
      verifyApiKey.mockResolvedValue({
        valid: true,
        user_id: 'u-1',
        subscription_status: 'past_due',
      });

      const res = await request(app)
        .get('/api/v1/search?type=motor')
        .set('X-API-Key', 'sk_query_real');

      expect(res.status).toBe(402);
      expect(leakedProductIds(res.body)).toEqual([]);
      await tick();
      expect(reportQueryUsage).not.toHaveBeenCalled();
    });

    it('active key → the real rows, and exactly one query metered to the owner', async () => {
      verifyApiKey.mockResolvedValue({
        valid: true,
        user_id: 'u-42',
        subscription_status: 'active',
      });

      const res = await request(app)
        .get('/api/v1/search?type=motor')
        .set('X-API-Key', 'sk_query_real');

      expect(res.status).toBe(200);
      expect(leakedProductIds(res.body)).toEqual(['paygate-m-1', 'paygate-m-2']);
      await tick();
      expect(reportQueryUsage).toHaveBeenCalledTimes(1);
      expect(reportQueryUsage).toHaveBeenCalledWith('u-42', 1);
    });

    it('billing outage → fails OPEN to the real rows, and nothing is metered', async () => {
      verifyApiKey.mockRejectedValue(new Error('billing unreachable'));

      const res = await request(app)
        .get('/api/v1/search?type=motor')
        .set('X-API-Key', 'sk_query_real');

      expect(res.status).toBe(200);
      // Availability bias is only worth anything if the free fallback
      // still returns data — a stubbed DAL can't show that.
      expect(leakedProductIds(res.body)).toEqual(['paygate-m-1', 'paygate-m-2']);
      await tick();
      expect(reportQueryUsage).not.toHaveBeenCalled();
    });

    it('a real 400 from query validation is not a billable query', async () => {
      verifyApiKey.mockResolvedValue({
        valid: true,
        user_id: 'u-42',
        subscription_status: 'active',
      });

      const res = await request(app)
        .get('/api/v1/search?limit=9999') // limit max is 100
        .set('X-API-Key', 'sk_query_real');

      expect(res.status).toBe(400);
      expect(leakedProductIds(res.body)).toEqual([]);
      await tick();
      expect(reportQueryUsage).not.toHaveBeenCalled();
    });

    it('a gated request leaves the catalog unchanged', async () => {
      const before = await tableSnapshot();
      verifyApiKey.mockResolvedValue({ valid: false });

      await request(app)
        .get('/api/v1/search?type=motor')
        .set('X-API-Key', 'sk_query_bogus');

      expect(await tableSnapshot()).toBe(before);
    });
  });
});
