/**
 * Real-DAL integration tests for GET /api/products cursor pagination.
 *
 * Sibling to the mocked `tests/products.contract.test.ts`, which pins
 * every route boundary (limit clamping, cursor hardening, 400/500 error
 * shapes) by stubbing `DynamoDBService.prototype.listPage`. That mock
 * means a refactor that breaks the real Query loop — the
 * `LastEvaluatedKey` → `ExclusiveStartKey` round-trip, the per-type
 * partition walk in `listPage`, the base64url-encoded cursor shape the
 * route mints from a real `next` — can pass green while pagination is
 * silently wrong in prod. This file closes that gap by seeding real
 * rows and driving the live pagination loop end-to-end against
 * DynamoDB Local.
 *
 * Cursor-hardening (adversarial base64url shapes) stays in the mocked
 * sibling: those assertions specifically verify that `listPage` is
 * NEVER called, which is exactly what a mock is for. Error-injection
 * (DB throws → 500, DAL returns false → 500) also stays mocked, per
 * the same rationale that `routes.real-dal.test.ts` uses for its
 * transport-failure cases.
 *
 * This is HARDENING Phase 2.2.b — one more entry off the "remaining
 * 13 mocked backend tests" migration follow-up landed by PR #246 and
 * the `auto/hardening-*-real-dal-*` series (PRs #300, #308, #309, #378).
 *
 * Same env-redirect trick as the sibling real-DAL suites: env vars are
 * set at module-load time before `../../src/index` is lazily required,
 * so the route module constructs its own `DynamoDBService` against
 * DynamoDB Local without any production-code change.
 */

import type { Application } from 'express';
import request from 'supertest';
import { DynamoDBService } from '../../src/db/dynamodb';
import {
  DynamoDBClient,
  ScanCommand,
  DeleteItemCommand,
} from '@aws-sdk/client-dynamodb';
import { Product } from '../../src/types/models';

const TABLE_NAME = 'specodex-test';
const ENDPOINT = process.env.MOCK_DYNAMODB_ENDPOINT ?? 'http://localhost:8000';

process.env.DYNAMODB_TABLE_NAME = TABLE_NAME;
process.env.AWS_ENDPOINT_URL_DYNAMODB = ENDPOINT;
process.env.AWS_REGION = 'us-east-1';
process.env.AWS_ACCESS_KEY_ID = 'local';
process.env.AWS_SECRET_ACCESS_KEY = 'local';

function seedDb(): DynamoDBService {
  return new DynamoDBService({
    tableName: TABLE_NAME,
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: { accessKeyId: 'local', secretAccessKey: 'local' },
  });
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

describe('GET /api/products cursor pagination — real-DAL integration', () => {
  let app: Application;
  let db: DynamoDBService;

  beforeAll(() => {
    // Lazy require so the env overrides above are in place before the
    // route module constructs its DynamoDBService at import time.
    app = require('../../src/index').default as Application;
  });

  beforeEach(async () => {
    await truncateTable();
    db = seedDb();
  });

  it('returns truncated=true + a real cursor when the limit is under the count', async () => {
    for (let i = 0; i < 5; i++) {
      await db.create({
        product_id: `m-${i}`,
        product_type: 'motor',
        manufacturer: 'PageCorp',
      } as Product);
    }

    const res = await request(app).get('/api/products?type=motor&limit=2');
    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.count).toBe(2);
    expect(res.body.data).toHaveLength(2);
    expect(res.body.truncated).toBe(true);
    expect(typeof res.body.cursor).toBe('string');
    expect(res.body.cursor.length).toBeGreaterThan(0);

    // Cursor decodes to a well-formed continuation for the same type.
    const decoded = JSON.parse(
      Buffer.from(res.body.cursor, 'base64url').toString('utf8'),
    );
    expect(decoded.type).toBe('motor');
    expect(decoded.key).toBeDefined();
    // DynamoDB's LastEvaluatedKey carries the PK/SK of the last emitted
    // row as marshalled attributes ({S: '...'}). The cursor round-trips
    // that shape verbatim.
    expect(decoded.key.PK).toEqual({ S: 'PRODUCT#MOTOR' });
    expect(decoded.key.SK.S).toMatch(/^PRODUCT#m-\d$/);
  });

  it('resumes at the returned cursor and reaches the end without overlap', async () => {
    for (let i = 0; i < 5; i++) {
      await db.create({
        product_id: `m-${i}`,
        product_type: 'motor',
        manufacturer: 'PageCorp',
      } as Product);
    }

    const first = await request(app).get('/api/products?type=motor&limit=2');
    expect(first.status).toBe(200);
    expect(first.body.truncated).toBe(true);
    const firstIds = first.body.data.map((p: { product_id: string }) => p.product_id);
    expect(firstIds).toHaveLength(2);

    const second = await request(app).get(
      `/api/products?type=motor&limit=2&cursor=${encodeURIComponent(first.body.cursor)}`,
    );
    expect(second.status).toBe(200);
    expect(second.body.count).toBe(2);
    expect(second.body.truncated).toBe(true);
    const secondIds = second.body.data.map((p: { product_id: string }) => p.product_id);
    // No page-boundary duplicates: the DAL's ExclusiveStartKey semantic
    // means the first row of page 2 is strictly after the last row of
    // page 1. If cursor encoding drifts vs LastEvaluatedKey, this
    // assertion catches it.
    expect(secondIds.filter((id: string) => firstIds.includes(id))).toEqual([]);

    const third = await request(app).get(
      `/api/products?type=motor&limit=2&cursor=${encodeURIComponent(second.body.cursor)}`,
    );
    expect(third.status).toBe(200);
    expect(third.body.count).toBe(1);
    expect(third.body.truncated).toBe(false);
    expect(third.body.cursor).toBeNull();

    // Union of the three pages equals every seeded row, exactly once.
    const all = [...firstIds, ...secondIds, ...third.body.data.map((p: { product_id: string }) => p.product_id)];
    expect(all.sort()).toEqual(['m-0', 'm-1', 'm-2', 'm-3', 'm-4']);
  });

  it('returns truncated=false + cursor=null when a single page covers the listing', async () => {
    await db.create({
      product_id: 'only-1',
      product_type: 'motor',
      manufacturer: 'ExhaustedCorp',
    } as Product);

    const res = await request(app).get('/api/products?type=motor');
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(1);
    expect(res.body.truncated).toBe(false);
    expect(res.body.cursor).toBeNull();
  });

  it('rejects a cursor minted for a different type with 400 (no DAL scan)', async () => {
    // Seed a real motor row so the listing has *something* the DAL
    // could return if the guard were broken; the 400 must fire before
    // any query hits the table.
    await db.create({
      product_id: 'motor-only',
      product_type: 'motor',
      manufacturer: 'A',
    } as Product);

    // Mint a cursor by asking for a paginated drive listing first (there
    // are no drives, so we have to hand-craft; the mocked sibling does
    // the same via `cursorOf({ type: 'drive' })`).
    const driveCursor = Buffer.from(JSON.stringify({ type: 'drive' })).toString('base64url');

    const res = await request(app).get(
      `/api/products?type=motor&cursor=${encodeURIComponent(driveCursor)}`,
    );
    expect(res.status).toBe(400);
    expect(res.body.success).toBe(false);
  });

  it('accepts a type-scoped cursor on a type=all listing and paginates through it', async () => {
    // Seed enough motors that page 1 with limit=2 leaves a real
    // continuation cursor. Then feed that motor cursor into a type=all
    // request — the route should accept it and continue the walk.
    for (let i = 0; i < 3; i++) {
      await db.create({
        product_id: `m-${i}`,
        product_type: 'motor',
        manufacturer: 'AllCorp',
      } as Product);
    }

    const motorPage = await request(app).get('/api/products?type=motor&limit=2');
    expect(motorPage.status).toBe(200);
    expect(motorPage.body.truncated).toBe(true);

    const allPage = await request(app).get(
      `/api/products?cursor=${encodeURIComponent(motorPage.body.cursor)}&limit=2000`,
    );
    expect(allPage.status).toBe(200);
    // The all-listing resumes inside the motor partition and returns
    // the tail motor row. The DAL then walks the remaining type
    // partitions (all empty in this seed) and completes cleanly.
    expect(allPage.body.success).toBe(true);
    expect(allPage.body.count).toBe(1);
    expect(allPage.body.data[0].product_id).toBe('m-2');
    expect(allPage.body.truncated).toBe(false);
    expect(allPage.body.cursor).toBeNull();
  });

  it('honours an explicit limit under the ceiling against real query pages', async () => {
    for (let i = 0; i < 4; i++) {
      await db.create({
        product_id: `m-${i}`,
        product_type: 'motor',
        manufacturer: 'LimitCorp',
      } as Product);
    }

    const res = await request(app).get('/api/products?type=motor&limit=3');
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(3);
    expect(res.body.data).toHaveLength(3);
    expect(res.body.truncated).toBe(true);
    expect(res.body.cursor).not.toBeNull();
  });
});
