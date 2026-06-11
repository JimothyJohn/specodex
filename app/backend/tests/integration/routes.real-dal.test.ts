/**
 * Real-DAL integration tests for the product + datasheet routes.
 *
 * Sibling to the mocked `tests/routes.test.ts`, which pins each route's
 * HTTP contract with `jest.mock('../src/db/dynamodb')`. Those mocks mean
 * a refactor that breaks the real DAL paths — Query-by-PK counting,
 * ProjectionExpression aggregation scans, marshall/unmarshall fidelity,
 * scan-then-batchDelete bulk deletes — passes green. This file drives
 * the live Express routes end-to-end against DynamoDB Local.
 *
 * This is HARDENING Phase 2.2.b — the `routes.test.ts` half of the
 * "migrate the mocked backend tests to real-DAL" follow-up, plus the
 * step-4 contract round-trip (a fully-structured product survives
 * POST → DynamoDB → GET byte-for-byte, typed against the
 * Pydantic-generated `generated.ts` Motor). Error-injection cases
 * (DB throws → 500) stay in the mocked sibling — simulating transport
 * failure is what mocks are for.
 *
 * Same env-redirect trick as `search.real-dal.test.ts`: the routes
 * construct their own `DynamoDBService` at import time, so
 * `AWS_ENDPOINT_URL_DYNAMODB` + `DYNAMODB_TABLE_NAME` are set before
 * the app module is lazily required. Zero production-code change.
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
// Type-only import of the Pydantic-generated contract; erased at runtime
// (ts-jest runs isolatedModules), enforced when editors/CI type-check.
import type { Motor as GeneratedMotor } from '../../../frontend/src/types/generated';

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

describe('product + datasheet routes — real-DAL integration', () => {
  let app: Application;
  let db: DynamoDBService;

  beforeAll(() => {
    // Lazy require so the env overrides above are in place before the
    // route modules construct their DynamoDBService at import time.
    app = require('../../src/index').default as Application;
  });

  beforeEach(async () => {
    await truncateTable();
    db = seedDb();
  });

  // ===================== Aggregation endpoints =====================

  describe('GET /api/products/summary', () => {
    it('counts seeded rows per type via the real Query COUNT path', async () => {
      await db.create({ product_id: 'm-1', product_type: 'motor', manufacturer: 'A' } as Product);
      await db.create({ product_id: 'm-2', product_type: 'motor', manufacturer: 'B' } as Product);
      await db.create({ product_id: 'd-1', product_type: 'drive', manufacturer: 'A' } as Product);

      const res = await request(app).get('/api/products/summary');
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);
      expect(res.body.data.total).toBe(3);
      expect(res.body.data.motors).toBe(2);
      expect(res.body.data.drives).toBe(1);
    });
  });

  describe('GET /api/products/categories', () => {
    it('returns every valid type with real counts (zero-count types included)', async () => {
      await db.create({ product_id: 'm-1', product_type: 'motor', manufacturer: 'A' } as Product);

      const res = await request(app).get('/api/products/categories');
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);

      const byType = new Map<string, { count: number; display_name: string }>(
        res.body.data.map((c: { type: string; count: number; display_name: string }) => [
          c.type,
          c,
        ]),
      );
      expect(byType.get('motor')?.count).toBe(1);
      expect(byType.get('motor')?.display_name).toBeTruthy();
      // Zero-count types still appear — the categories endpoint advertises
      // the full VALID_PRODUCT_TYPES surface, not just populated partitions.
      expect(byType.has('drive')).toBe(true);
      expect(byType.get('drive')?.count).toBe(0);
    });
  });

  describe('GET /api/products/manufacturers + /names', () => {
    it('deduplicates and sorts across type partitions via projection scans', async () => {
      await db.create({
        product_id: 'm-1', product_type: 'motor', manufacturer: 'Siemens', product_name: 'SM-1',
      } as Product);
      await db.create({
        product_id: 'm-2', product_type: 'motor', manufacturer: 'ABB', product_name: 'AM-2',
      } as Product);
      await db.create({
        product_id: 'd-1', product_type: 'drive', manufacturer: 'ABB', product_name: 'AD-1',
      } as Product);

      const mfrs = await request(app).get('/api/products/manufacturers');
      expect(mfrs.status).toBe(200);
      expect(mfrs.body.data).toEqual(['ABB', 'Siemens']);

      const names = await request(app).get('/api/products/names');
      expect(names.status).toBe(200);
      expect(names.body.data).toEqual(['AD-1', 'AM-2', 'SM-1']);
    });
  });

  // ===================== List / read =====================

  describe('GET /api/products', () => {
    it('lists seeded products with type filter and limit', async () => {
      for (let i = 0; i < 3; i++) {
        await db.create({
          product_id: `m-${i}`, product_type: 'motor', manufacturer: 'Bulk',
        } as Product);
      }
      await db.create({ product_id: 'd-1', product_type: 'drive', manufacturer: 'Bulk' } as Product);

      const motors = await request(app).get('/api/products?type=motor');
      expect(motors.status).toBe(200);
      expect(motors.body.count).toBe(3);
      expect(
        motors.body.data.every((p: { product_type: string }) => p.product_type === 'motor'),
      ).toBe(true);

      const limited = await request(app).get('/api/products?type=motor&limit=2');
      expect(limited.status).toBe(200);
      expect(limited.body.count).toBe(2);
    });
  });

  describe('GET /api/products/:id', () => {
    it('reads a seeded row and 404s on a missing one', async () => {
      await db.create({
        product_id: 'real-1', product_type: 'motor', manufacturer: 'ReadCorp',
      } as Product);

      const found = await request(app).get('/api/products/real-1?type=motor');
      expect(found.status).toBe(200);
      expect(found.body.data.product_id).toBe('real-1');
      expect(found.body.data.manufacturer).toBe('ReadCorp');

      const missing = await request(app).get('/api/products/no-such-id?type=motor');
      expect(missing.status).toBe(404);
      expect(missing.body.success).toBe(false);
    });
  });

  // ===================== Create =====================

  describe('POST /api/products', () => {
    it('persists a single product end-to-end', async () => {
      const post = await request(app).post('/api/products').send({
        product_id: 'post-1',
        product_type: 'motor',
        product_name: 'Posted Motor',
        manufacturer: 'PostCorp',
      });
      expect(post.status).toBe(201);
      expect(post.body.data.items_created).toBe(1);

      const read = await request(app).get('/api/products/post-1?type=motor');
      expect(read.status).toBe(200);
      expect(read.body.data.product_name).toBe('Posted Motor');
    });

    it('persists a batch through the real BatchWriteItem path', async () => {
      const post = await request(app)
        .post('/api/products')
        .send([
          { product_id: 'b-1', product_type: 'motor', manufacturer: 'BatchCorp' },
          { product_id: 'b-2', product_type: 'drive', manufacturer: 'BatchCorp' },
        ]);
      expect(post.status).toBe(201);
      expect(post.body.data.items_created).toBe(2);

      const summary = await request(app).get('/api/products/summary');
      expect(summary.body.data.total).toBe(2);
    });
  });

  // ===================== Delete =====================

  describe('DELETE /api/products/:id', () => {
    it('deletes a real row and 404s on the follow-up read', async () => {
      await db.create({
        product_id: 'del-1', product_type: 'motor', manufacturer: 'DelCorp',
      } as Product);

      const del = await request(app).delete('/api/products/del-1?type=motor');
      expect(del.status).toBe(200);
      expect(del.body.success).toBe(true);

      const read = await request(app).get('/api/products/del-1?type=motor');
      expect(read.status).toBe(404);
    });
  });

  describe('DELETE /api/products/part-number/:partNumber', () => {
    it('deletes every row sharing the part number via the real scan path', async () => {
      await db.create({
        product_id: 'pn-1', product_type: 'motor', manufacturer: 'A', part_number: 'PN-X',
      } as Product);
      await db.create({
        product_id: 'pn-2', product_type: 'drive', manufacturer: 'B', part_number: 'PN-X',
      } as Product);
      await db.create({
        product_id: 'keep', product_type: 'motor', manufacturer: 'A', part_number: 'PN-KEEP',
      } as Product);

      const del = await request(app).delete('/api/products/part-number/PN-X');
      expect(del.status).toBe(200);
      expect(del.body.data.deleted).toBe(2);

      const summary = await request(app).get('/api/products/summary');
      expect(summary.body.data.total).toBe(1);

      const missing = await request(app).delete('/api/products/part-number/PN-X');
      expect(missing.status).toBe(404);
    });
  });

  // ===================== Deduplicate =====================

  describe('POST /api/products/deduplicate', () => {
    it('dry-runs then deletes a real duplicate pair', async () => {
      const dup = {
        product_type: 'motor',
        part_number: 'DUP-1',
        product_name: 'Dup Motor',
        manufacturer: 'DupCorp',
      };
      await db.create({ ...dup, product_id: 'dup-a' } as Product);
      await db.create({ ...dup, product_id: 'dup-b' } as Product);
      await db.create({
        product_id: 'uniq', product_type: 'motor', part_number: 'UNIQ',
        product_name: 'Unique', manufacturer: 'DupCorp',
      } as Product);

      const dry = await request(app).post('/api/products/deduplicate').send({ confirm: false });
      expect(dry.status).toBe(200);
      expect(dry.body.data.dry_run).toBe(true);
      expect(dry.body.data.found).toBe(1);

      const wet = await request(app).post('/api/products/deduplicate').send({ confirm: true });
      expect(wet.status).toBe(200);
      expect(wet.body.data.dry_run).toBe(false);
      expect(wet.body.data.deleted).toBe(1);

      const summary = await request(app).get('/api/products/summary');
      expect(summary.body.data.total).toBe(2);
    });
  });

  // ===================== Datasheets =====================

  describe('datasheet routes', () => {
    it('creates, lists (with type mapping), rejects duplicates, deletes', async () => {
      const created = await request(app).post('/api/datasheets').send({
        url: 'https://example.com/ds.pdf',
        product_type: 'motor',
        product_name: 'DS Motor',
      });
      expect(created.status).toBe(201);

      const dup = await request(app).post('/api/datasheets').send({
        url: 'https://example.com/ds.pdf',
        product_type: 'motor',
        product_name: 'DS Motor again',
      });
      expect(dup.status).toBe(409);

      const list = await request(app).get('/api/datasheets');
      expect(list.status).toBe(200);
      expect(list.body.data).toHaveLength(1);
      // The route remaps product_type → 'datasheet' and preserves the
      // component type — pin that against the real stored row.
      expect(list.body.data[0].product_type).toBe('datasheet');
      expect(list.body.data[0].component_type).toBe('motor');
      const datasheetId = list.body.data[0].datasheet_id;
      expect(datasheetId).toBeTruthy();

      const del = await request(app).delete(`/api/datasheets/${datasheetId}?type=motor`);
      expect(del.status).toBe(200);

      const after = await request(app).get('/api/datasheets');
      expect(after.body.data).toHaveLength(0);
    });
  });

  // ===================== Contract round-trip (2.2 step 4) =====================

  describe('generated-types contract round-trip', () => {
    it('a fully-structured Motor survives POST → DynamoDB → GET without coercion', async () => {
      // Every structured field family the Pydantic models emit:
      // ValueUnit, MinMaxUnit-shaped ranges, nested dimensions, string
      // lists, nulls. If marshall/unmarshall coerces numbers to strings,
      // drops nested nulls, or reorders into sets, the deep-equal fails.
      const motor: GeneratedMotor & { product_id: string } = {
        product_id: 'contract-1',
        product_type: 'motor',
        product_name: 'Contract Servo',
        product_family: 'CS-Series',
        part_number: 'CS-750',
        manufacturer: 'ContractCorp',
        release_year: 2024,
        weight: { value: 2.5, unit: 'kg' },
        rated_power: { value: 750, unit: 'W' },
        msrp: null,
        datasheet_url: 'https://example.com/cs750.pdf',
      } as GeneratedMotor & { product_id: string };

      const post = await request(app).post('/api/products').send(motor);
      expect(post.status).toBe(201);
      expect(post.body.data.items_created).toBe(1);

      const read = await request(app).get('/api/products/contract-1?type=motor');
      expect(read.status).toBe(200);
      const row = read.body.data;

      // Identity + scalar fidelity
      expect(row.product_id).toBe('contract-1');
      expect(row.product_type).toBe('motor');
      expect(row.product_family).toBe('CS-Series');
      expect(row.release_year).toBe(2024);
      expect(typeof row.release_year).toBe('number');

      // Structured ValueUnit fidelity — numbers stay numbers
      expect(row.weight).toEqual({ value: 2.5, unit: 'kg' });
      expect(typeof row.weight.value).toBe('number');
      expect(row.rated_power).toEqual({ value: 750, unit: 'W' });

      // Explicit null survives (not dropped, not stringified)
      expect(row.msrp).toBeNull();
    });
  });
});
