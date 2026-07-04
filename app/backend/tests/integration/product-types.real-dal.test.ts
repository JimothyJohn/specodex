/**
 * Real-DAL integration test for the per-product-type route loops.
 *
 * Sibling to the mocked `tests/product-types.test.ts`, which pins the
 * config-consistency invariants (`VALID_PRODUCT_TYPES` membership +
 * `formatDisplayName` shape) and runs `.each(HARDWARE_TYPES)` HTTP
 * sweeps with `jest.mock('../src/db/dynamodb')`. The mocks mean a
 * refactor that breaks per-type PK/SK composition, per-type
 * marshall/unmarshall, or a per-type `Query` filter would slip through
 * the mocked suite — every `.mockResolvedValue` returns the test's own
 * fixture regardless of which type the route asked for.
 *
 * This file closes that gap: for every type in `VALID_PRODUCT_TYPES`
 * it seeds a row into DynamoDB Local, drives the Express routes
 * end-to-end, and asserts the round-trip preserves the type. A bug
 * where `motor` rows land in the `drive` partition (or vice versa)
 * fails here loudly; the mocked sibling cannot see it.
 *
 * Same env-redirect trick as `routes.real-dal.test.ts` /
 * `search.real-dal.test.ts`: the routes construct their own
 * `DynamoDBService` at import time, so `AWS_ENDPOINT_URL_DYNAMODB` +
 * `DYNAMODB_TABLE_NAME` are set before the app module is lazily
 * required. Zero production-code change.
 *
 * This is HARDENING Phase 2.2.b — one more slice of the "migrate the
 * mocked backend tests to real-DAL" follow-up. Config-consistency
 * tests stay in the mocked sibling (no DB involvement).
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
import { VALID_PRODUCT_TYPES } from '../../src/config/productTypes';

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

describe('per-product-type route loops — real-DAL integration', () => {
  let app: Application;
  let db: DynamoDBService;

  beforeAll(() => {
    app = require('../../src/index').default as Application;
  });

  beforeEach(async () => {
    await truncateTable();
    db = seedDb();
  });

  describe('GET /api/products?type=<type>', () => {
    it.each(VALID_PRODUCT_TYPES)(
      'lists only the seeded %s row (no cross-type bleed)',
      async (type) => {
        // Seed one row in `type` and one in a sibling type. If
        // PK composition or the Query filter is wrong for `type`,
        // the sibling leaks through or the target row vanishes.
        const sibling = VALID_PRODUCT_TYPES.find((t) => t !== type)!;
        await db.create({
          product_id: `${type}-001`,
          product_type: type,
          manufacturer: 'TestCorp',
        } as Product);
        await db.create({
          product_id: `${sibling}-001`,
          product_type: sibling,
          manufacturer: 'OtherCorp',
        } as Product);

        const res = await request(app).get(`/api/products?type=${type}`);
        expect(res.status).toBe(200);
        expect(res.body.success).toBe(true);
        expect(res.body.data).toHaveLength(1);
        expect(res.body.data[0].product_type).toBe(type);
        expect(res.body.data[0].product_id).toBe(`${type}-001`);
      },
    );
  });

  describe('GET /api/products/:id?type=<type>', () => {
    it.each(VALID_PRODUCT_TYPES)(
      'reads a seeded %s row by id+type',
      async (type) => {
        await db.create({
          product_id: `${type}-001`,
          product_type: type,
          manufacturer: 'TestCorp',
        } as Product);

        const res = await request(app).get(`/api/products/${type}-001?type=${type}`);
        expect(res.status).toBe(200);
        expect(res.body.success).toBe(true);
        expect(res.body.data.product_id).toBe(`${type}-001`);
        expect(res.body.data.product_type).toBe(type);
        expect(res.body.data.manufacturer).toBe('TestCorp');
      },
    );

    it.each(VALID_PRODUCT_TYPES)(
      '404s on a missing %s row',
      async (type) => {
        const res = await request(app).get(`/api/products/missing?type=${type}`);
        expect(res.status).toBe(404);
        expect(res.body.success).toBe(false);
      },
    );
  });

  describe('POST /api/products', () => {
    it.each(VALID_PRODUCT_TYPES)(
      'creates a %s product and round-trips via GET',
      async (type) => {
        const payload = {
          product_type: type,
          product_name: `Test ${type}`,
          manufacturer: 'TestCorp',
          part_number: `TC-${type}-001`,
        };
        const post = await request(app).post('/api/products').send(payload);
        expect(post.status).toBe(201);
        expect(post.body.success).toBe(true);
        expect(post.body.data.items_created).toBe(1);

        // The route assigns a server-side id; rather than re-derive it,
        // confirm the type-filtered list now contains exactly one row of
        // this type with the expected part_number.
        const list = await request(app).get(`/api/products?type=${type}`);
        expect(list.status).toBe(200);
        expect(list.body.data).toHaveLength(1);
        expect(list.body.data[0].product_type).toBe(type);
        expect(list.body.data[0].part_number).toBe(`TC-${type}-001`);
      },
    );
  });

  describe('DELETE /api/products/:id?type=<type>', () => {
    it.each(VALID_PRODUCT_TYPES)(
      'deletes a seeded %s row and the follow-up GET is 404',
      async (type) => {
        await db.create({
          product_id: `${type}-del`,
          product_type: type,
          manufacturer: 'DelCorp',
        } as Product);

        const del = await request(app).delete(`/api/products/${type}-del?type=${type}`);
        expect(del.status).toBe(200);
        expect(del.body.success).toBe(true);

        const after = await request(app).get(`/api/products/${type}-del?type=${type}`);
        expect(after.status).toBe(404);
      },
    );
  });

  describe('GET /api/v1/search?type=<type>', () => {
    it.each(VALID_PRODUCT_TYPES)(
      'returns the seeded %s row through the real Query path',
      async (type) => {
        await db.create({
          product_id: `${type}-search-1`,
          product_type: type,
          manufacturer: 'SearchCorp',
        } as Product);

        const res = await request(app).get(`/api/v1/search?type=${type}`);
        expect(res.status).toBe(200);
        expect(res.body.success).toBe(true);
        // The search endpoint returns an array of products under .data;
        // every row must carry the requested type.
        const items = res.body.data ?? [];
        expect(Array.isArray(items)).toBe(true);
        for (const item of items) {
          expect(item.product_type).toBe(type);
        }
        // At least the seeded row must be present.
        expect(items.some((p: Product) => p.product_id === `${type}-search-1`)).toBe(true);
      },
    );

    it('rejects an invalid product type at the Zod boundary', async () => {
      const res = await request(app).get('/api/v1/search?type=nonexistent');
      expect(res.status).toBe(400);
      expect(res.body.success).toBe(false);
    });
  });

  describe('GET /api/products/summary', () => {
    it('counts every configured type via the real COUNT-projection Query', async () => {
      // Seed exactly one row in every type — the summary should
      // report `total = N` and a per-type partition equal to 1.
      for (const type of VALID_PRODUCT_TYPES) {
        await db.create({
          product_id: `${type}-sum`,
          product_type: type,
          manufacturer: 'SumCorp',
        } as Product);
      }

      const res = await request(app).get('/api/products/summary');
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);
      expect(res.body.data.total).toBe(VALID_PRODUCT_TYPES.length);
    });
  });
});
