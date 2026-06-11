/**
 * Real-DAL integration tests for GET /api/v1/search.
 *
 * Sibling to the mocked `tests/search.contract.test.ts`, which pins the
 * Zod boundary (limit/type enforcement, abuse inputs) with
 * `jest.mock('../src/db/dynamodb')`. That mock means a refactor that
 * breaks the real `db.list` → `searchProducts` data path passes green.
 * This file closes that gap: it seeds real rows into DynamoDB Local and
 * drives the live Express route end-to-end, exercising the
 * marshall/unmarshall round-trip, the PK/SK query, and the
 * filter/sort/limit/summary contract against the real DAL.
 *
 * This is HARDENING Phase 2.2.b — the `search.contract` half of the
 * "migrate the mocked backend tests to real-DAL" follow-up. Runs via
 * `npm run test:integration` (DynamoDB Local booted by
 * @shelf/jest-dynamodb's globalSetup); excluded from the default
 * `npm test` unit run.
 *
 * The route constructs its own `DynamoDBService` at import time with no
 * endpoint override, so we redirect it at DynamoDB Local the way the
 * AWS SDK natively supports — `AWS_ENDPOINT_URL_DYNAMODB` + the table
 * name in `DYNAMODB_TABLE_NAME` — set before the app module is
 * required. Zero production-code change: the env override is inert in
 * prod, where the Lambda resolves the real service endpoint.
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

// Point the route's internally-constructed DAL at DynamoDB Local. These
// must be set before `src/index` (and its `src/config`) is required, so
// the app is loaded lazily in beforeAll() rather than via a top import.
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

describe('GET /api/v1/search — real-DAL integration', () => {
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

  it('filters by product type via the real Query-by-PK path', async () => {
    await db.create({
      product_id: 'm-1',
      product_type: 'motor',
      manufacturer: 'ABB',
    } as Product);
    await db.create({
      product_id: 'm-2',
      product_type: 'motor',
      manufacturer: 'Siemens',
    } as Product);
    await db.create({
      product_id: 'd-1',
      product_type: 'drive',
      manufacturer: 'ABB',
    } as Product);

    const motors = await request(app).get('/api/v1/search?type=motor');
    expect(motors.status).toBe(200);
    expect(motors.body.success).toBe(true);
    expect(motors.body.count).toBe(2);
    expect(motors.body.data.map((p: { product_id: string }) => p.product_id).sort()).toEqual([
      'm-1',
      'm-2',
    ]);
    expect(motors.body.data.every((p: { product_type: string }) => p.product_type === 'motor')).toBe(true);

    const drives = await request(app).get('/api/v1/search?type=drive');
    expect(drives.status).toBe(200);
    expect(drives.body.count).toBe(1);
    expect(drives.body.data[0].product_id).toBe('d-1');
  });

  it('round-trips identity fields and type-specific summary specs', async () => {
    await db.create({
      product_id: 'motor-summary',
      product_type: 'motor',
      manufacturer: 'TestCorp',
      part_number: 'TC-900',
      product_name: 'TC-900 Servo',
      rated_power: { value: 750, unit: 'W' },
    } as unknown as Product);

    const res = await request(app).get('/api/v1/search?type=motor');
    expect(res.status).toBe(200);
    const row = res.body.data[0];
    expect(row.product_id).toBe('motor-summary');
    expect(row.product_type).toBe('motor');
    expect(row.manufacturer).toBe('TestCorp');
    expect(row.part_number).toBe('TC-900');
    expect(row.product_name).toBe('TC-900 Servo');
    // rated_power is in motor's SUMMARY_SPECS; the structured ValueUnit
    // must survive the marshall/unmarshall round-trip intact.
    expect(row.rated_power).toEqual({ value: 750, unit: 'W' });
  });

  it('applies the manufacturer filter (case-insensitive substring)', async () => {
    await db.create({
      product_id: 'abb-1',
      product_type: 'motor',
      manufacturer: 'ABB',
    } as Product);
    await db.create({
      product_id: 'sie-1',
      product_type: 'motor',
      manufacturer: 'Siemens',
    } as Product);

    const res = await request(app).get('/api/v1/search?type=motor&manufacturer=abb');
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(1);
    expect(res.body.data[0].product_id).toBe('abb-1');
  });

  it('applies a numeric where clause against a stored ValueUnit field', async () => {
    await db.create({
      product_id: 'lo',
      product_type: 'motor',
      manufacturer: 'M',
      rated_power: { value: 100, unit: 'W' },
    } as unknown as Product);
    await db.create({
      product_id: 'hi',
      product_type: 'motor',
      manufacturer: 'M',
      rated_power: { value: 1000, unit: 'W' },
    } as unknown as Product);

    const res = await request(app).get('/api/v1/search?type=motor&where=rated_power>=500');
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(1);
    expect(res.body.data[0].product_id).toBe('hi');
  });

  it('sorts by a string field with the requested direction', async () => {
    await db.create({
      product_id: 'a',
      product_type: 'motor',
      manufacturer: 'Alpha',
    } as Product);
    await db.create({
      product_id: 'c',
      product_type: 'motor',
      manufacturer: 'Charlie',
    } as Product);
    await db.create({
      product_id: 'b',
      product_type: 'motor',
      manufacturer: 'Bravo',
    } as Product);

    const res = await request(app).get('/api/v1/search?type=motor&sort=manufacturer:desc');
    expect(res.status).toBe(200);
    expect(res.body.data.map((p: { manufacturer: string }) => p.manufacturer)).toEqual([
      'Charlie',
      'Bravo',
      'Alpha',
    ]);
  });

  it('honours the limit against real-DAL results', async () => {
    for (let i = 0; i < 5; i++) {
      await db.create({
        product_id: `m-${i}`,
        product_type: 'motor',
        manufacturer: 'BulkCorp',
      } as Product);
    }

    const res = await request(app).get('/api/v1/search?type=motor&limit=2');
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(2);
    expect(res.body.data).toHaveLength(2);
  });

  it('returns an empty result set without error when nothing matches', async () => {
    await db.create({
      product_id: 'only',
      product_type: 'motor',
      manufacturer: 'ABB',
    } as Product);

    const res = await request(app).get('/api/v1/search?type=motor&manufacturer=nonexistent');
    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.count).toBe(0);
    expect(res.body.data).toEqual([]);
  });
});
