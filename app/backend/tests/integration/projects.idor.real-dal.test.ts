/**
 * Real-DAL IDOR tests for /api/projects/*.
 *
 * Sibling to the mocked `tests/projects.idor.test.ts`, which replaces
 * `ProjectsService` with an in-memory `Map<sub, Map<id, Project>>`. That
 * mock *assumes* the storage layer scopes by owner sub — it can only
 * check that the route passed the JWT's sub down. If the real DynamoDB
 * layout ever stopped enforcing the same isolation (a dropped
 * `PK = USER#{sub}` prefix, an `attribute_exists(SK)` guard removed from
 * the conditional Update so a cross-tenant PATCH *upserts* instead of
 * failing), the mocked file stays green.
 *
 * This file closes that gap: the same attacker scenarios, driven through
 * the real `ProjectsService` against DynamoDB Local, with assertions on
 * the raw stored items that the mocked sibling structurally cannot make.
 *
 * Distinct from `projects.real-dal.test.ts`, which covers the happy-path
 * CRUD round-trips (and cross-tenant get/rename/delete). What's here and
 * only here:
 *   - cross-tenant product-ref mutation (add / remove)
 *   - the 404-vs-404 enumeration indistinguishability check
 *   - forged-identity attempts (`X-User-Id` header, body `owner_sub`)
 *   - **no-phantom-row**: a rejected cross-tenant write must not leave an
 *     item behind in the attacker's partition (only a real UpdateItem
 *     can upsert; the in-memory mock never could)
 *   - **byte-identical victim row**: the target row is unchanged after
 *     the full attack sweep
 *
 * This is HARDENING Phase 2.2.b — one of the remaining mocked-DAL backend
 * tests called out as the follow-up in PR #246. Runs via
 * `npm run test:integration` (DynamoDB Local booted by
 * @shelf/jest-dynamodb's globalSetup); excluded from the default
 * `npm test` unit run.
 *
 * Auth: `aws-jwt-verify` stays mocked, same as the other real-DAL files.
 * The surface under test is the DAL's tenant scoping, not JWT
 * verification — the mocked sibling pins the 401-on-bad-token contract.
 */

import type { Application } from 'express';
import request from 'supertest';
import {
  DynamoDBClient,
  ScanCommand,
  QueryCommand,
  GetItemCommand,
  DeleteItemCommand,
} from '@aws-sdk/client-dynamodb';
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb';

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

const mockVerify = jest.fn();
jest.mock('aws-jwt-verify', () => ({
  CognitoJwtVerifier: { create: jest.fn(() => ({ verify: mockVerify })) },
}));

const ALICE = 'user-sub-alice';
const BOB = 'user-sub-bob';
const ALICE_TOKEN = 'Bearer token-alice';
const BOB_TOKEN = 'Bearer token-bob';

function setAuthedUser(sub: string) {
  mockVerify.mockResolvedValue({
    sub,
    email: `${sub}@example.com`,
    'cognito:groups': [],
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

/** Read the stored item straight out of DynamoDB, bypassing the routes. */
async function rawItem(
  sub: string,
  id: string,
): Promise<Record<string, unknown> | null> {
  const client = rawClient();
  const res = await client.send(
    new GetItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({ PK: `USER#${sub}`, SK: `PROJECT#${id}` }),
    }),
  );
  return res.Item ? (unmarshall(res.Item) as Record<string, unknown>) : null;
}

/** Every project row sitting in one user's partition. */
async function rawPartition(sub: string): Promise<Record<string, unknown>[]> {
  const client = rawClient();
  const res = await client.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: 'PK = :pk AND begins_with(SK, :sk)',
      ExpressionAttributeValues: marshall({
        ':pk': `USER#${sub}`,
        ':sk': 'PROJECT#',
      }),
    }),
  );
  return (res.Items ?? []).map(i => unmarshall(i) as Record<string, unknown>);
}

describe('/api/projects — real-DAL IDOR / cross-tenant isolation', () => {
  let app: Application;
  let resetVerifier: () => void;

  beforeAll(() => {
    // Lazy require so the env overrides above are in place before the
    // projects route module constructs its `ProjectsService` /
    // `DynamoDBClient` at import time.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    app = require('../../src/index').default as Application;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    resetVerifier = require('../../src/middleware/auth')
      ._resetVerifierForTests as () => void;
  });

  beforeEach(async () => {
    await truncateTable();
    mockVerify.mockReset();
    setAuthedUser(ALICE);
    resetVerifier();
  });

  /** Create one project owned by Alice, with an optional product ref. */
  async function seedAliceProject(
    name = 'Alice Private',
    ref?: { product_type: string; product_id: string },
  ): Promise<string> {
    setAuthedUser(ALICE);
    const created = await request(app)
      .post('/api/projects')
      .set('Authorization', ALICE_TOKEN)
      .send({ name });
    expect(created.status).toBe(201);
    const id = created.body.data.id as string;
    if (ref) {
      const added = await request(app)
        .post(`/api/projects/${id}/products`)
        .set('Authorization', ALICE_TOKEN)
        .send(ref);
      expect(added.status).toBe(200);
    }
    return id;
  }

  // ------------------------------------------------------------------
  // Product-ref mutation — the two endpoints projects.real-dal.test.ts
  // only exercises same-tenant.
  // ------------------------------------------------------------------

  describe('POST /api/projects/:id/products (cross-tenant add)', () => {
    it('404s and leaves no phantom row in the attacker’s partition', async () => {
      const aliceId = await seedAliceProject('Alice Cell');

      setAuthedUser(BOB);
      const res = await request(app)
        .post(`/api/projects/${aliceId}/products`)
        .set('Authorization', BOB_TOKEN)
        .send({ product_type: 'motor', product_id: 'bob-injected' });
      expect(res.status).toBe(404);

      // `addProduct` reads first (scoped Get → null → 404), so no
      // UpdateItem is issued. If the guard regressed to a bare Update,
      // DynamoDB would UPSERT a row into USER#bob — assert it didn't.
      expect(await rawPartition(BOB)).toEqual([]);

      // Alice's refs are untouched.
      const victim = await rawItem(ALICE, aliceId);
      expect(victim?.product_refs).toEqual([]);
    });

    it('does not let Bob append to Alice’s existing refs', async () => {
      const aliceId = await seedAliceProject('Alice Cell', {
        product_type: 'motor',
        product_id: 'alice-m1',
      });

      setAuthedUser(BOB);
      const res = await request(app)
        .post(`/api/projects/${aliceId}/products`)
        .set('Authorization', BOB_TOKEN)
        .send({ product_type: 'drive', product_id: 'bob-d1' });
      expect(res.status).toBe(404);

      const victim = await rawItem(ALICE, aliceId);
      expect(victim?.product_refs).toEqual([
        { product_type: 'motor', product_id: 'alice-m1' },
      ]);
    });
  });

  describe('DELETE /api/projects/:id/products/:type/:pid (cross-tenant remove)', () => {
    it('404s and leaves Alice’s ref in place', async () => {
      const aliceId = await seedAliceProject('Alice Cell', {
        product_type: 'motor',
        product_id: 'alice-m1',
      });

      setAuthedUser(BOB);
      const res = await request(app)
        .delete(`/api/projects/${aliceId}/products/motor/alice-m1`)
        .set('Authorization', BOB_TOKEN);
      expect(res.status).toBe(404);

      const victim = await rawItem(ALICE, aliceId);
      expect(victim?.product_refs).toEqual([
        { product_type: 'motor', product_id: 'alice-m1' },
      ]);
      expect(await rawPartition(BOB)).toEqual([]);
    });
  });

  // ------------------------------------------------------------------
  // Enumeration
  // ------------------------------------------------------------------

  describe('ID enumeration (sequential guessing)', () => {
    it('does not leak whether a foreign project ID exists vs not', async () => {
      const aliceId = await seedAliceProject('Alice Real');

      setAuthedUser(BOB);
      const known = await request(app)
        .get(`/api/projects/${aliceId}`)
        .set('Authorization', BOB_TOKEN);
      const random = await request(app)
        .get('/api/projects/never-existed')
        .set('Authorization', BOB_TOKEN);

      expect(known.status).toBe(404);
      expect(random.status).toBe(404);
      // Byte-identical bodies — an existing-but-foreign ID must look
      // exactly like a nonexistent one.
      expect(known.body).toEqual(random.body);
      expect(JSON.stringify(known.body)).not.toContain('Alice Real');
    });

    it('scoped Query never returns another partition’s rows', async () => {
      await seedAliceProject('Alice One');
      await seedAliceProject('Alice Two');

      setAuthedUser(BOB);
      const res = await request(app)
        .get('/api/projects')
        .set('Authorization', BOB_TOKEN);
      expect(res.status).toBe(200);
      expect(res.body.count).toBe(0);
      expect(res.body.data).toEqual([]);

      // Both rows really are in the table — the empty list is isolation,
      // not an empty table.
      expect(await rawPartition(ALICE)).toHaveLength(2);
    });
  });

  // ------------------------------------------------------------------
  // Forged identity
  // ------------------------------------------------------------------

  describe('forged-identity attempts', () => {
    it('ignores an X-User-Id header naming the victim', async () => {
      const aliceId = await seedAliceProject('Alice Header');

      setAuthedUser(BOB);
      const res = await request(app)
        .get(`/api/projects/${aliceId}`)
        .set('Authorization', BOB_TOKEN)
        .set('X-User-Id', ALICE);
      expect(res.status).toBe(404);
    });

    it('ignores a body-supplied owner_sub on create (row lands in the caller’s partition)', async () => {
      setAuthedUser(BOB);
      const res = await request(app)
        .post('/api/projects')
        .set('Authorization', BOB_TOKEN)
        .send({ name: 'forged', owner_sub: ALICE });
      expect(res.status).toBe(201);
      expect(res.body.data.owner_sub).toBe(BOB);

      const id = res.body.data.id as string;
      // The stored item's partition key and owner_sub are both Bob's;
      // nothing was written under Alice.
      const stored = await rawItem(BOB, id);
      expect(stored).not.toBeNull();
      expect(stored!.PK).toBe(`USER#${BOB}`);
      expect(stored!.SK).toBe(`PROJECT#${id}`);
      expect(stored!.owner_sub).toBe(BOB);
      expect(await rawItem(ALICE, id)).toBeNull();
      expect(await rawPartition(ALICE)).toEqual([]);
    });
  });

  // ------------------------------------------------------------------
  // Whole-sweep invariant
  // ------------------------------------------------------------------

  describe('victim row after a full attack sweep', () => {
    it('is byte-identical, and the attacker’s partition stays empty', async () => {
      const aliceId = await seedAliceProject('Alice Untouched', {
        product_type: 'motor',
        product_id: 'alice-m1',
      });
      const before = await rawItem(ALICE, aliceId);
      expect(before).not.toBeNull();

      setAuthedUser(BOB);
      const attacks = [
        request(app)
          .get(`/api/projects/${aliceId}`)
          .set('Authorization', BOB_TOKEN),
        request(app)
          .patch(`/api/projects/${aliceId}`)
          .set('Authorization', BOB_TOKEN)
          .send({ name: 'pwned' }),
        request(app)
          .delete(`/api/projects/${aliceId}`)
          .set('Authorization', BOB_TOKEN),
        request(app)
          .post(`/api/projects/${aliceId}/products`)
          .set('Authorization', BOB_TOKEN)
          .send({ product_type: 'drive', product_id: 'bob-d1' }),
        request(app)
          .delete(`/api/projects/${aliceId}/products/motor/alice-m1`)
          .set('Authorization', BOB_TOKEN),
      ];
      for (const attack of attacks) {
        const res = await attack;
        expect(res.status).toBe(404);
      }

      // Not just "name unchanged" — the entire stored item, updated_at
      // included, is what it was before Bob touched anything.
      expect(await rawItem(ALICE, aliceId)).toEqual(before);
      // And no conditional write leaked a partial row into Bob's space.
      expect(await rawPartition(BOB)).toEqual([]);
    });
  });
});
