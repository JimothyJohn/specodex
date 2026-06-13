/**
 * Real-DAL integration tests for /api/projects/*.
 *
 * Sibling to the mocked `tests/projects.test.ts`, which pins the route
 * handlers + Zod boundary by replacing the `ProjectsService` import
 * entirely (`jest.mock('../src/db/projects')`). That mock means a
 * refactor that breaks the real DynamoDB layout — `PK = USER#{sub}` /
 * `SK = PROJECT#{id}`, `ConditionExpression: attribute_not_exists(SK)`
 * on create, `attribute_exists(SK)` on rename/delete, the
 * `(owner_sub, id)` scoping on every Query/Get — passes green. This
 * file closes that gap by driving the live Express projects routes
 * end-to-end against the real `ProjectsService` pointed at DynamoDB
 * Local.
 *
 * This is HARDENING Phase 2.2.b — one of the remaining mocked-DAL
 * backend tests called out as the follow-up in PR #246. Runs via
 * `npm run test:integration` (DynamoDB Local booted by
 * @shelf/jest-dynamodb's globalSetup); excluded from the default
 * `npm test` unit run.
 *
 * Auth: `aws-jwt-verify` stays mocked. The point of this file is the
 * DAL layer; spinning a fake Cognito for the JWT verification path
 * would be a second test surface and the mocked sibling already pins
 * the auth-gating contract (401 on missing/invalid token).
 *
 * Same env-redirect trick as the sibling integration files: the
 * `ProjectsService` constructs its own `DynamoDBClient` at import
 * time with no endpoint override, so the AWS SDK's native
 * `AWS_ENDPOINT_URL_DYNAMODB` env is set before the app module is
 * lazily required. Zero production-code change — the override is
 * inert in prod, where the Lambda resolves the real service endpoint.
 */

import type { Application } from 'express';
import request from 'supertest';
import {
  DynamoDBClient,
  ScanCommand,
  DeleteItemCommand,
} from '@aws-sdk/client-dynamodb';

const TABLE_NAME = 'specodex-test';
const ENDPOINT = process.env.MOCK_DYNAMODB_ENDPOINT ?? 'http://localhost:8000';

// Point the real `ProjectsService` (constructed at route-module import
// time) at DynamoDB Local via the AWS SDK's native env override.
process.env.DYNAMODB_TABLE_NAME = TABLE_NAME;
process.env.AWS_ENDPOINT_URL_DYNAMODB = ENDPOINT;
process.env.AWS_REGION = 'us-east-1';
process.env.AWS_ACCESS_KEY_ID = 'local';
process.env.AWS_SECRET_ACCESS_KEY = 'local';

// Auth middleware needs Cognito IDs present to even attempt verification;
// without them every authed request 503s. Real values don't matter here
// because the verifier itself is mocked below.
process.env.COGNITO_USER_POOL_ID = 'us-east-1_TEST';
process.env.COGNITO_USER_POOL_CLIENT_ID = 'test-client-id';

// Mock the Cognito JWT verifier. The mock's `verify` returns the user
// payload we set per-test via `setAuthedUser()` — driving the route
// layer to read `req.user.sub` exactly as it would in prod, but without
// needing a live Cognito User Pool.
const mockVerify = jest.fn();
jest.mock('aws-jwt-verify', () => ({
  CognitoJwtVerifier: { create: jest.fn(() => ({ verify: mockVerify })) },
}));

const SUB_A = 'user-sub-A';
const SUB_B = 'user-sub-B';

function authedUser(sub: string) {
  return { sub, email: `${sub}@example.com`, 'cognito:groups': [] };
}

function setAuthedUser(sub: string) {
  mockVerify.mockResolvedValue(authedUser(sub));
}

async function truncateTable(): Promise<void> {
  const client = new DynamoDBClient({
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: { accessKeyId: 'local', secretAccessKey: 'local' },
  });
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

describe('/api/projects — real-DAL integration', () => {
  let app: Application;
  let resetVerifier: () => void;

  beforeAll(() => {
    // Lazy require so the env overrides above are in place before the
    // projects route module constructs its `ProjectsService` /
    // `DynamoDBClient` at import time.
    app = require('../../src/index').default as Application;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    resetVerifier = require('../../src/middleware/auth')
      ._resetVerifierForTests as () => void;
  });

  beforeEach(async () => {
    await truncateTable();
    mockVerify.mockReset();
    setAuthedUser(SUB_A);
    resetVerifier();
  });

  function authA() {
    return 'Bearer token-A';
  }
  function authB() {
    return 'Bearer token-B';
  }

  describe('POST /api/projects', () => {
    it('creates a project under the caller’s partition (USER#{sub}, PROJECT#{id})', async () => {
      const res = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'Cell A' });
      expect(res.status).toBe(201);
      expect(res.body.data.owner_sub).toBe(SUB_A);
      expect(res.body.data.name).toBe('Cell A');
      expect(res.body.data.product_refs).toEqual([]);
      expect(typeof res.body.data.id).toBe('string');
      // PK / SK are scrubbed from the public response.
      expect(res.body.data.PK).toBeUndefined();
      expect(res.body.data.SK).toBeUndefined();

      // The row is reachable via the real Query path the list endpoint
      // uses — confirms PK / SK layout is what the route's
      // KeyConditionExpression expects.
      const list = await request(app)
        .get('/api/projects')
        .set('Authorization', authA());
      expect(list.status).toBe(200);
      expect(list.body.count).toBe(1);
      expect(list.body.data[0].id).toBe(res.body.data.id);
    });

    it('trims whitespace in name through the real Put round-trip', async () => {
      const res = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: '  Cell B  ' });
      expect(res.status).toBe(201);
      expect(res.body.data.name).toBe('Cell B');

      // Round-trip via Get to confirm the trim landed in the stored row
      // (not just the response body).
      const fetched = await request(app)
        .get(`/api/projects/${res.body.data.id}`)
        .set('Authorization', authA());
      expect(fetched.status).toBe(200);
      expect(fetched.body.data.name).toBe('Cell B');
    });
  });

  describe('GET /api/projects', () => {
    it('returns only the caller’s rows via the real Query-by-PK', async () => {
      // Two projects for A, one for B — partitioned by `USER#{sub}`.
      const a1 = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'A-1' });
      const a2 = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'A-2' });
      setAuthedUser(SUB_B);
      await request(app)
        .post('/api/projects')
        .set('Authorization', authB())
        .send({ name: 'B-1' });

      // A's list sees A's two rows only.
      setAuthedUser(SUB_A);
      const listA = await request(app)
        .get('/api/projects')
        .set('Authorization', authA());
      expect(listA.status).toBe(200);
      expect(listA.body.count).toBe(2);
      const idsA = listA.body.data.map((p: { id: string }) => p.id).sort();
      expect(idsA).toEqual([a1.body.data.id, a2.body.data.id].sort());
      // No cross-tenant leak even though all rows share the table.
      expect(
        listA.body.data.every((p: { owner_sub: string }) => p.owner_sub === SUB_A),
      ).toBe(true);

      // B's list sees only B's row.
      setAuthedUser(SUB_B);
      const listB = await request(app)
        .get('/api/projects')
        .set('Authorization', authB());
      expect(listB.body.count).toBe(1);
      expect(listB.body.data[0].name).toBe('B-1');
    });

    it('returns an empty list, not 404, when the caller has no rows', async () => {
      const res = await request(app)
        .get('/api/projects')
        .set('Authorization', authA());
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);
      expect(res.body.count).toBe(0);
      expect(res.body.data).toEqual([]);
    });
  });

  describe('GET /api/projects/:id', () => {
    it('200s with the project via the real Get', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'Cell X' });

      const res = await request(app)
        .get(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA());
      expect(res.status).toBe(200);
      expect(res.body.data.id).toBe(created.body.data.id);
      expect(res.body.data.name).toBe('Cell X');
    });

    it('404s when not found', async () => {
      const res = await request(app)
        .get('/api/projects/nope')
        .set('Authorization', authA());
      expect(res.status).toBe(404);
    });

    it('404s on cross-tenant read (B cannot Get A’s project by ID)', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'A-private' });

      // B knows A's project ID but still gets 404 — the Get is scoped
      // to USER#{B}, where the row doesn't exist.
      setAuthedUser(SUB_B);
      const res = await request(app)
        .get(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authB());
      expect(res.status).toBe(404);

      // And A still sees it on their side.
      setAuthedUser(SUB_A);
      const stillThere = await request(app)
        .get(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA());
      expect(stillThere.status).toBe(200);
    });
  });

  describe('PATCH /api/projects/:id', () => {
    it('renames via the real conditional Update', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'Old' });

      const res = await request(app)
        .patch(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA())
        .send({ name: 'New' });
      expect(res.status).toBe(200);
      expect(res.body.data.name).toBe('New');
      // updated_at advances; created_at is preserved.
      expect(res.body.data.updated_at).not.toBe(created.body.data.updated_at);
      expect(res.body.data.created_at).toBe(created.body.data.created_at);

      // Confirm the new name landed in the stored row via Get.
      const fetched = await request(app)
        .get(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA());
      expect(fetched.body.data.name).toBe('New');
    });

    it('404s when the ConditionExpression rejects (attribute_exists(SK))', async () => {
      const res = await request(app)
        .patch('/api/projects/missing')
        .set('Authorization', authA())
        .send({ name: 'New' });
      expect(res.status).toBe(404);
    });

    it('404s on cross-tenant rename (B cannot PATCH A’s project)', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'A-rename' });

      setAuthedUser(SUB_B);
      const res = await request(app)
        .patch(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authB())
        .send({ name: 'Pwned' });
      expect(res.status).toBe(404);

      // A's row is unchanged.
      setAuthedUser(SUB_A);
      const fetched = await request(app)
        .get(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA());
      expect(fetched.body.data.name).toBe('A-rename');
    });
  });

  describe('DELETE /api/projects/:id', () => {
    it('deletes via the real conditional Delete', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'Doomed' });

      const res = await request(app)
        .delete(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA());
      expect(res.status).toBe(200);
      expect(res.body.data.deleted).toBe(true);

      // Subsequent Get returns 404 — the row is gone.
      const fetched = await request(app)
        .get(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA());
      expect(fetched.status).toBe(404);
    });

    it('404s on double-delete (ConditionalCheckFailed)', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'Once' });
      await request(app)
        .delete(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA());

      const second = await request(app)
        .delete(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA());
      expect(second.status).toBe(404);
    });

    it('404s on cross-tenant delete (B cannot DELETE A’s project)', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'A-secret' });

      setAuthedUser(SUB_B);
      const res = await request(app)
        .delete(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authB());
      expect(res.status).toBe(404);

      // A's row still exists.
      setAuthedUser(SUB_A);
      const fetched = await request(app)
        .get(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA());
      expect(fetched.status).toBe(200);
    });
  });

  describe('POST /api/projects/:id/products', () => {
    it('adds a product ref and survives the Get → Update round-trip', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'Cell' });

      const res = await request(app)
        .post(`/api/projects/${created.body.data.id}/products`)
        .set('Authorization', authA())
        .send({ product_type: 'motor', product_id: 'm-1' });
      expect(res.status).toBe(200);
      expect(res.body.data.product_refs).toEqual([
        { product_type: 'motor', product_id: 'm-1' },
      ]);

      // Confirm the merged list landed via a fresh Get.
      const fetched = await request(app)
        .get(`/api/projects/${created.body.data.id}`)
        .set('Authorization', authA());
      expect(fetched.body.data.product_refs).toEqual([
        { product_type: 'motor', product_id: 'm-1' },
      ]);
    });

    it('appends a second ref without dropping the first', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'Cell' });
      await request(app)
        .post(`/api/projects/${created.body.data.id}/products`)
        .set('Authorization', authA())
        .send({ product_type: 'motor', product_id: 'm-1' });

      const res = await request(app)
        .post(`/api/projects/${created.body.data.id}/products`)
        .set('Authorization', authA())
        .send({ product_type: 'drive', product_id: 'd-1' });
      expect(res.status).toBe(200);
      expect(res.body.data.product_refs).toEqual([
        { product_type: 'motor', product_id: 'm-1' },
        { product_type: 'drive', product_id: 'd-1' },
      ]);
    });

    it('is idempotent on a duplicate (type, id) tuple', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'Cell' });
      await request(app)
        .post(`/api/projects/${created.body.data.id}/products`)
        .set('Authorization', authA())
        .send({ product_type: 'motor', product_id: 'm-1' });

      const res = await request(app)
        .post(`/api/projects/${created.body.data.id}/products`)
        .set('Authorization', authA())
        .send({ product_type: 'motor', product_id: 'm-1' });
      expect(res.status).toBe(200);
      expect(res.body.data.product_refs).toHaveLength(1);
    });

    it('404s when the project does not exist (no Update issued)', async () => {
      const res = await request(app)
        .post('/api/projects/missing/products')
        .set('Authorization', authA())
        .send({ product_type: 'motor', product_id: 'm-1' });
      expect(res.status).toBe(404);
    });
  });

  describe('DELETE /api/projects/:id/products/:type/:pid', () => {
    it('removes a product ref via the real Get → Update round-trip', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'Cell' });
      await request(app)
        .post(`/api/projects/${created.body.data.id}/products`)
        .set('Authorization', authA())
        .send({ product_type: 'motor', product_id: 'm-1' });
      await request(app)
        .post(`/api/projects/${created.body.data.id}/products`)
        .set('Authorization', authA())
        .send({ product_type: 'drive', product_id: 'd-1' });

      const res = await request(app)
        .delete(`/api/projects/${created.body.data.id}/products/motor/m-1`)
        .set('Authorization', authA());
      expect(res.status).toBe(200);
      expect(res.body.data.product_refs).toEqual([
        { product_type: 'drive', product_id: 'd-1' },
      ]);
    });

    it('is a no-op (200, refs unchanged) when the ref is absent', async () => {
      const created = await request(app)
        .post('/api/projects')
        .set('Authorization', authA())
        .send({ name: 'Cell' });

      const res = await request(app)
        .delete(`/api/projects/${created.body.data.id}/products/motor/m-1`)
        .set('Authorization', authA());
      expect(res.status).toBe(200);
      expect(res.body.data.product_refs).toEqual([]);
    });

    it('404s when the project does not exist', async () => {
      const res = await request(app)
        .delete('/api/projects/nope/products/motor/m-1')
        .set('Authorization', authA());
      expect(res.status).toBe(404);
    });
  });
});
