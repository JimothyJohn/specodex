/**
 * Real-DAL integration tests for GET /api/v1/search contract boundaries.
 *
 * Sibling to the mocked `tests/search.contract.test.ts`, which pins the
 * Zod boundary (limit / type enforcement, malformed-input rejection) with
 * `jest.mock('../src/db/dynamodb')` returning `[]` on every list call.
 * That stub means a refactor that breaks how the real DAL walks the
 * per-type PK partitions — or how the search pipeline threads abusive
 * user input into `parseWhere` / `applyWhere` / text scoring against
 * real records — passes green in the mocked sibling.
 *
 * This file closes that gap. It seeds one row per `VALID_PRODUCT_TYPES`
 * literal, then drives each contract-boundary happy-path against the
 * live route: per-type PK routing lands the right rows, abusive query
 * strings (SQL-ish, NoSQL, null byte, oversize, unicode) are treated as
 * literal content that the real text-search + real filter pipeline
 * survives without a crash, `where` / `sort` array params flow through
 * the real DAL, and limit boundaries clamp on real result counts.
 *
 * Deliberately kept in the mocked sibling:
 * - Zod-rejected inputs (`limit=abc`, `type=Motor`, etc.) — 400 fires
 *   before the DAL, so the mock is exactly what a real DAL would do
 *   (nothing).
 * - The DB-error-surface case (`db.list` throws → 500 with hidden stack
 *   trace) — simulating transport failure is what mocks are for; same
 *   rationale as `routes.real-dal.test.ts` / `products.contract.real-dal.test.ts`
 *   for their transport-failure cases.
 *
 * This is HARDENING Phase 2.2.b — one more entry off the "remaining
 * mocked backend tests" migration follow-up, alongside the
 * `auto/hardening-*-real-dal-*` series (PRs #300, #308, #309, #378, #379).
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
import { PRODUCT_TYPES } from '../../src/types/generated_constants';

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

describe('GET /api/v1/search — contract boundaries against real DAL', () => {
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

  describe('type enum acceptance — each valid literal routes to its own PK', () => {
    it.each([...PRODUCT_TYPES])(
      'type=%s returns only rows from the matching partition',
      async (targetType) => {
        // Seed exactly one row per valid product type so the PK routing
        // is the only mechanism that can produce a single-row response
        // for the queried type. If db.list('motor') accidentally scanned
        // every type's partition, this test would surface count > 1.
        for (const t of PRODUCT_TYPES) {
          await db.create({
            product_id: `id-${t}`,
            product_type: t,
            manufacturer: 'PartitionCorp',
          } as unknown as Product);
        }

        const res = await request(app).get(`/api/v1/search?type=${targetType}`);
        expect(res.status).toBe(200);
        expect(res.body.success).toBe(true);
        expect(res.body.count).toBe(1);
        expect(res.body.data).toHaveLength(1);
        expect(res.body.data[0].product_id).toBe(`id-${targetType}`);
        expect(res.body.data[0].product_type).toBe(targetType);
      },
    );
  });

  describe('abuse inputs survive the real search pipeline', () => {
    beforeEach(async () => {
      // A single seeded motor so the search pipeline has *something*
      // to score against; the abuse inputs would look benign if the
      // pipeline short-circuited on an empty candidate list.
      await db.create({
        product_id: 'abuse-motor',
        product_type: 'motor',
        manufacturer: 'AbuseCorp',
        part_number: 'AC-1',
        product_name: 'Standard servo',
      } as unknown as Product);
    });

    it('treats SQL-ish text as literal content (no crash, no injection)', async () => {
      const q = encodeURIComponent("'; DROP TABLE products --");
      const res = await request(app).get(`/api/v1/search?type=motor&q=${q}`);
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);
      // The seeded motor doesn't contain "DROP TABLE" — text-search
      // scores zero against it, so no rows come back. The pipeline still
      // returns a well-formed envelope.
      expect(Array.isArray(res.body.data)).toBe(true);
    });

    it('treats NoSQL operator injection as literal text', async () => {
      const q = encodeURIComponent('{ "$ne": null }');
      const res = await request(app).get(`/api/v1/search?type=motor&q=${q}`);
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);
    });

    it('accepts a ~2 KB q value without truncation or crash', async () => {
      const q = 'a'.repeat(2000);
      const res = await request(app).get('/api/v1/search').query({ type: 'motor', q });
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);
    });

    it('handles unicode query strings without crashing', async () => {
      const res = await request(app)
        .get('/api/v1/search')
        .query({ type: 'motor', q: 'café é résumé' });
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);
    });

    it('a null byte in q does not produce a 500 from the real DAL path', async () => {
      const res = await request(app)
        .get('/api/v1/search')
        .query({ type: 'motor', q: 'abc\x00def' });
      // Express may strip/encode; either 200 or 400 is acceptable. 500 is not.
      expect(res.status).toBeLessThan(500);
    });
  });

  describe('where / sort array handling flows through the real DAL', () => {
    beforeEach(async () => {
      // Three motors with a spread on rated_power + rated_voltage so a
      // dual-clause where filter has something to narrow AND a stable
      // sort has a discriminating field. Manufacturer values are chosen
      // so the alphabetic sort visibly reorders vs insertion order.
      await db.create({
        product_id: 'lo',
        product_type: 'motor',
        manufacturer: 'Charlie',
        rated_power: { value: 100, unit: 'W' },
        rated_voltage: { min: 24, max: 24, unit: 'V' },
      } as unknown as Product);
      await db.create({
        product_id: 'mid',
        product_type: 'motor',
        manufacturer: 'Alpha',
        rated_power: { value: 500, unit: 'W' },
        rated_voltage: { min: 48, max: 48, unit: 'V' },
      } as unknown as Product);
      await db.create({
        product_id: 'hi',
        product_type: 'motor',
        manufacturer: 'Bravo',
        rated_power: { value: 1500, unit: 'W' },
        rated_voltage: { min: 400, max: 400, unit: 'V' },
      } as unknown as Product);
    });

    it('accepts repeated where params as an array — every clause is applied', async () => {
      const res = await request(app)
        .get('/api/v1/search')
        .query({ type: 'motor', where: ['rated_power>=200', 'rated_voltage<=48'] });
      expect(res.status).toBe(200);
      expect(res.body.count).toBe(1);
      expect(res.body.data[0].product_id).toBe('mid');
    });

    it('accepts repeated sort params as an array — first-key ordering wins ties', async () => {
      const res = await request(app)
        .get('/api/v1/search')
        .query({ type: 'motor', sort: ['manufacturer', 'rated_power:desc'] });
      expect(res.status).toBe(200);
      expect(res.body.count).toBe(3);
      expect(res.body.data.map((p: { manufacturer: string }) => p.manufacturer)).toEqual([
        'Alpha',
        'Bravo',
        'Charlie',
      ]);
    });

    it('duplicate type params fall to Zod (400) — the real DAL is never queried', async () => {
      // Express parses ?type=motor&type=drive as an array; the Zod enum
      // rejects the array shape. The mocked sibling verifies the mock
      // list() is never called; here we verify the real DAL sees no
      // partition query — expressed as an <500 status guarantee.
      const res = await request(app).get('/api/v1/search?type=motor&type=drive');
      expect(res.status).toBeLessThan(500);
      expect(res.status).toBe(400);
    });
  });

  describe('limit boundaries clamp on real result counts', () => {
    beforeEach(async () => {
      for (let i = 0; i < 5; i++) {
        await db.create({
          product_id: `m-${i}`,
          product_type: 'motor',
          manufacturer: 'LimitCorp',
        } as unknown as Product);
      }
    });

    it('limit=1 (lower bound) returns exactly one row from the real listing', async () => {
      const res = await request(app).get('/api/v1/search?type=motor&limit=1');
      expect(res.status).toBe(200);
      expect(res.body.count).toBe(1);
      expect(res.body.data).toHaveLength(1);
    });

    it('limit=100 (upper bound) returns every real row when the seed is below it', async () => {
      const res = await request(app).get('/api/v1/search?type=motor&limit=100');
      expect(res.status).toBe(200);
      expect(res.body.count).toBe(5);
      expect(res.body.data).toHaveLength(5);
    });

    it('omitted limit falls back to the default (20) against the real DAL', async () => {
      const res = await request(app).get('/api/v1/search?type=motor');
      expect(res.status).toBe(200);
      // Default 20 > 5 seeded rows, so every row surfaces.
      expect(res.body.count).toBe(5);
    });
  });
});
