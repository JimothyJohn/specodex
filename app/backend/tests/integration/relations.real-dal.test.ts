/**
 * Real-DAL integration tests for /api/v1/relations/*.
 *
 * Sibling to the mocked `tests/relations.test.ts`, which pins the route
 * handlers + Zod boundary with `jest.mock('../src/db/dynamodb')`. Those
 * mocks mean a refactor that breaks the real `db.list('linear_actuator')`
 * → `compatibleActuators` data path passes green. This file closes that
 * gap by seeding real rows into DynamoDB Local and driving the live
 * Express relations routes end-to-end — the BUILD 1A endpoints shipped
 * in PR #262 are now the live source of truth for the requirements-
 * first selection flow, so the real-DAL coverage matters.
 *
 * This is HARDENING Phase 2.2.b — one of the remaining mocked-DAL
 * backend tests called out as the follow-up in PR #246. Runs via
 * `npm run test:integration` (DynamoDB Local booted by
 * @shelf/jest-dynamodb's globalSetup); excluded from the default
 * `npm test` unit run.
 *
 * Predicate-only coverage (the `_predicates.*` block in the mocked
 * sibling) stays where it is — those are pure functions with no DAL
 * dependency; spinning DynamoDB Local for them would just slow the
 * suite down.
 *
 * Same env-redirect trick as `search.real-dal.test.ts` /
 * `routes.real-dal.test.ts`: the route constructs its own
 * `DynamoDBService` at import time, so `AWS_ENDPOINT_URL_DYNAMODB` +
 * `DYNAMODB_TABLE_NAME` are set before the app module is lazily
 * required. Zero production-code change.
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

describe('/api/v1/relations — real-DAL integration', () => {
  let app: Application;
  let db: DynamoDBService;

  beforeAll(() => {
    // Lazy require so the env overrides above are in place before the
    // relations route module constructs its DynamoDBService at import
    // time.
    app = require('../../src/index').default as Application;
  });

  beforeEach(async () => {
    await truncateTable();
    db = seedDb();
  });

  describe('GET /api/v1/relations/actuators', () => {
    it('returns the full real catalogue when no floors are set', async () => {
      await db.create({
        product_id: 'la-300',
        product_type: 'linear_actuator',
        manufacturer: 'A',
        stroke: { value: 300, unit: 'mm' },
        max_push_force: { value: 200, unit: 'N' },
        max_linear_speed: { value: 500, unit: 'mm/s' },
      } as unknown as Product);
      await db.create({
        product_id: 'la-100',
        product_type: 'linear_actuator',
        manufacturer: 'A',
        stroke: { value: 100, unit: 'mm' },
        max_push_force: { value: 50, unit: 'N' },
        max_linear_speed: { value: 250, unit: 'mm/s' },
      } as unknown as Product);

      const res = await request(app).get('/api/v1/relations/actuators');
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);
      expect(res.body.count).toBe(2);
      expect(res.body.total).toBe(2);
    });

    it('narrows by stroke + force floors while reporting the full total', async () => {
      await db.create({
        product_id: 'long-strong',
        product_type: 'linear_actuator',
        manufacturer: 'A',
        stroke: { value: 300, unit: 'mm' },
        max_push_force: { value: 200, unit: 'N' },
        max_linear_speed: { value: 500, unit: 'mm/s' },
      } as unknown as Product);
      await db.create({
        product_id: 'short',
        product_type: 'linear_actuator',
        manufacturer: 'A',
        stroke: { value: 100, unit: 'mm' },
        max_push_force: { value: 200, unit: 'N' },
        max_linear_speed: { value: 500, unit: 'mm/s' },
      } as unknown as Product);
      await db.create({
        product_id: 'weak',
        product_type: 'linear_actuator',
        manufacturer: 'A',
        stroke: { value: 300, unit: 'mm' },
        max_push_force: { value: 50, unit: 'N' },
        max_linear_speed: { value: 500, unit: 'mm/s' },
      } as unknown as Product);

      const res = await request(app).get(
        '/api/v1/relations/actuators?min_stroke_mm=200&min_peak_force_n=175',
      );
      expect(res.status).toBe(200);
      expect(res.body.count).toBe(1);
      expect(res.body.total).toBe(3);
      expect(res.body.data[0].product_id).toBe('long-strong');
    });

    it('returns an empty list with total=0 when no actuators are seeded', async () => {
      const res = await request(app).get('/api/v1/relations/actuators?min_stroke_mm=1');
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);
      expect(res.body.count).toBe(0);
      expect(res.body.total).toBe(0);
      expect(res.body.data).toEqual([]);
    });
  });

  describe('GET /api/v1/relations/motors-for-actuator', () => {
    it('404s when the actuator id is absent from DynamoDB', async () => {
      const res = await request(app).get(
        '/api/v1/relations/motors-for-actuator?id=la-missing&type=linear_actuator',
      );
      expect(res.status).toBe(404);
      expect(res.body.success).toBe(false);
    });

    it('filters real motor rows by motor_mount_pattern intersection', async () => {
      await db.create({
        product_id: 'la-23',
        product_type: 'linear_actuator',
        manufacturer: 'A',
        compatible_motor_mounts: ['NEMA 23', 'NEMA 34'],
      } as unknown as Product);
      await db.create({
        product_id: 'm-23',
        product_type: 'motor',
        manufacturer: 'M',
        motor_mount_pattern: 'NEMA 23',
        shaft_diameter: { value: 14.0, unit: 'mm' },
      } as unknown as Product);
      await db.create({
        product_id: 'm-17',
        product_type: 'motor',
        manufacturer: 'M',
        motor_mount_pattern: 'NEMA 17',
        shaft_diameter: { value: 5.0, unit: 'mm' },
      } as unknown as Product);

      const res = await request(app).get(
        '/api/v1/relations/motors-for-actuator?id=la-23&type=linear_actuator',
      );
      expect(res.status).toBe(200);
      expect(res.body.success).toBe(true);
      expect(res.body.count).toBe(1);
      expect(res.body.data[0].product_id).toBe('m-23');
    });
  });

  describe('GET /api/v1/relations/drives-for-motor', () => {
    it('404s when the motor id is absent from DynamoDB', async () => {
      const res = await request(app).get(
        '/api/v1/relations/drives-for-motor?id=m-missing',
      );
      expect(res.status).toBe(404);
      expect(res.body.success).toBe(false);
    });

    it('returns only drives whose envelope covers the seeded motor', async () => {
      await db.create({
        product_id: 'm-23',
        product_type: 'motor',
        manufacturer: 'M',
        motor_mount_pattern: 'NEMA 23',
        rated_voltage: { min: 200, max: 240, unit: 'V' },
        rated_current: { value: 3.0, unit: 'A' },
        shaft_diameter: { value: 14.0, unit: 'mm' },
        encoder_feedback_support: 'endat_2_2',
      } as unknown as Product);
      await db.create({
        product_id: 'd-ok',
        product_type: 'drive',
        manufacturer: 'D',
        input_voltage: { min: 200, max: 240, unit: 'V' },
        rated_current: { value: 5.0, unit: 'A' },
        encoder_feedback_support: ['endat_2_2'],
      } as unknown as Product);
      await db.create({
        product_id: 'd-undersized',
        product_type: 'drive',
        manufacturer: 'D',
        input_voltage: { min: 200, max: 240, unit: 'V' },
        rated_current: { value: 1.0, unit: 'A' },
        encoder_feedback_support: ['endat_2_2'],
      } as unknown as Product);

      const res = await request(app).get('/api/v1/relations/drives-for-motor?id=m-23');
      expect(res.status).toBe(200);
      expect(res.body.count).toBe(1);
      expect(res.body.data[0].product_id).toBe('d-ok');
    });
  });

  describe('GET /api/v1/relations/gearheads-for-motor', () => {
    it('404s when the motor id is absent from DynamoDB', async () => {
      const res = await request(app).get(
        '/api/v1/relations/gearheads-for-motor?id=m-missing',
      );
      expect(res.status).toBe(404);
      expect(res.body.success).toBe(false);
    });

    it('filters real gearhead rows by mount + shaft compatibility', async () => {
      await db.create({
        product_id: 'm-23',
        product_type: 'motor',
        manufacturer: 'M',
        motor_mount_pattern: 'NEMA 23',
        shaft_diameter: { value: 14.0, unit: 'mm' },
      } as unknown as Product);
      await db.create({
        product_id: 'g-ok',
        product_type: 'gearhead',
        manufacturer: 'G',
        input_motor_mount: ['NEMA 23'],
        input_shaft_diameter: { value: 14.0, unit: 'mm' },
      } as unknown as Product);
      await db.create({
        product_id: 'g-wrong-mount',
        product_type: 'gearhead',
        manufacturer: 'G',
        input_motor_mount: ['NEMA 17'],
        input_shaft_diameter: { value: 14.0, unit: 'mm' },
      } as unknown as Product);
      await db.create({
        product_id: 'g-wrong-shaft',
        product_type: 'gearhead',
        manufacturer: 'G',
        input_motor_mount: ['NEMA 23'],
        input_shaft_diameter: { value: 12.0, unit: 'mm' },
      } as unknown as Product);

      const res = await request(app).get('/api/v1/relations/gearheads-for-motor?id=m-23');
      expect(res.status).toBe(200);
      expect(res.body.count).toBe(1);
      expect(res.body.data[0].product_id).toBe('g-ok');
    });
  });
});
