/**
 * Real-DAL integration tests for POST /api/v1/compat/check.
 *
 * Sibling to the mocked `tests/compat.test.ts`, which pins the compat
 * service math + the HTTP contract via `jest.mock('../src/db/dynamodb')`.
 * The mocks mean a refactor that breaks the real per-type PK/SK
 * composition, the `db.read(id, type)` marshall/unmarshall, or the
 * parallel two-read fan-out inside the route passes green. This file
 * closes that gap: the route runs against DynamoDB Local end-to-end so
 * a bug where the drive/motor rows land in the wrong partition (or
 * where read() silently returns a torn row) fails here loudly.
 *
 * This is HARDENING Phase 2.2.b — one more slice of the "migrate the
 * mocked backend tests to real-DAL" follow-up. Service-level math
 * (`check()`, `softenReport()`, `isPairSupported()`, `ADJACENT_TYPES`)
 * stays in the mocked sibling — pure functions with no DAL dependency;
 * spinning DynamoDB Local for them would just slow the suite down.
 * Route-level 400 boundaries (invalid body shape, unsupported pair,
 * `GET /adjacent`) also stay mocked — they never reach the DAL.
 *
 * Same env-redirect trick as `routes.real-dal.test.ts` /
 * `search.real-dal.test.ts`: the compat route constructs its own
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
import { Drive, Gearhead, Motor, Product } from '../../src/types/models';

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

function motorSeed(over: Partial<Motor> = {}): Motor {
  return {
    product_id: 'm1',
    product_type: 'motor',
    manufacturer: 'TestCo',
    rated_voltage: { min: 200, max: 240, unit: 'V' },
    rated_current: { value: 5, unit: 'A' },
    rated_power: { value: 1000, unit: 'W' },
    rated_speed: { value: 3000, unit: 'rpm' },
    rated_torque: { value: 3, unit: 'Nm' },
    shaft_diameter: { value: 14, unit: 'mm' },
    frame_size: '60',
    encoder_feedback_support: 'EnDat 2.2',
    type: 'ac servo',
    ...over,
  } as Motor;
}

function driveSeed(over: Partial<Drive> = {}): Drive {
  return {
    product_id: 'd1',
    product_type: 'drive',
    manufacturer: 'TestCo',
    input_voltage: { min: 200, max: 240, unit: 'V' },
    rated_current: { value: 10, unit: 'A' },
    rated_power: { value: 2000, unit: 'W' },
    encoder_feedback_support: ['EnDat 2.2', 'Resolver'],
    fieldbus: ['EtherCAT'],
    ...over,
  } as Drive;
}

function gearheadSeed(over: Partial<Gearhead> = {}): Gearhead {
  return {
    product_id: 'g1',
    product_type: 'gearhead',
    manufacturer: 'TestCo',
    gear_ratio: 10,
    frame_size: '60',
    input_shaft_diameter: { value: 14, unit: 'mm' },
    max_input_speed: { value: 4000, unit: 'rpm' },
    rated_torque: { value: 30, unit: 'Nm' },
    ...over,
  } as Gearhead;
}

describe('POST /api/v1/compat/check — real-DAL integration', () => {
  let app: Application;
  let db: DynamoDBService;

  beforeAll(() => {
    // Lazy require so the env overrides above are in place before the
    // compat route module constructs its DynamoDBService at import
    // time.
    app = require('../../src/index').default as Application;
  });

  beforeEach(async () => {
    await truncateTable();
    db = seedDb();
  });

  it('drive↔motor: seeded pair round-trips through the DAL and returns a softened report', async () => {
    await db.create(driveSeed() as unknown as Product);
    await db.create(motorSeed() as unknown as Product);

    const res = await request(app)
      .post('/api/v1/compat/check')
      .send({ a: { id: 'd1', type: 'drive' }, b: { id: 'm1', type: 'motor' } });

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.data.from_type).toBe('drive');
    expect(res.body.data.to_type).toBe('motor');
    // Softened at the API boundary — never 'fail'.
    expect(['ok', 'partial']).toContain(res.body.data.status);
    // The two rotary junctions the service produces for drive+motor.
    const ports = res.body.data.results.map(
      (r: { from_port: string }) => r.from_port,
    );
    expect(ports).toContain('Drive.motor_output');
    expect(ports).toContain('Drive.feedback');
  });

  it('drive↔motor: undersized drive current softens to partial with detail preserved', async () => {
    await db.create(driveSeed({ rated_current: { value: 2, unit: 'A' } }) as unknown as Product);
    await db.create(motorSeed() as unknown as Product);

    const res = await request(app)
      .post('/api/v1/compat/check')
      .send({ a: { id: 'd1', type: 'drive' }, b: { id: 'm1', type: 'motor' } });

    expect(res.status).toBe(200);
    expect(res.body.data.status).toBe('partial');
    const power = res.body.data.results.find(
      (r: { from_port: string }) => r.from_port === 'Drive.motor_output',
    );
    const current = power.checks.find(
      (c: { field: string }) => c.field === 'current',
    );
    expect(current.status).toBe('partial');
    expect(current.detail).toContain('supply 2 < demand 5');
  });

  it('motor↔gearhead: seeded pair round-trips through the DAL and returns the shaft junction', async () => {
    await db.create(motorSeed() as unknown as Product);
    await db.create(gearheadSeed() as unknown as Product);

    const res = await request(app)
      .post('/api/v1/compat/check')
      .send({ a: { id: 'm1', type: 'motor' }, b: { id: 'g1', type: 'gearhead' } });

    expect(res.status).toBe(200);
    expect(res.body.data.from_type).toBe('motor');
    expect(res.body.data.to_type).toBe('gearhead');
    const shaft = res.body.data.results.find(
      (r: { from_port: string }) => r.from_port === 'Motor.shaft_output',
    );
    expect(shaft).toBeDefined();
    // Seed values line up (14 mm shaft/bore, 3000 rpm ≤ 4000 rpm cap, frame 60).
    expect(shaft.status).toBe('ok');
  });

  it('404s when one product is absent from DynamoDB (motor seeded, drive missing)', async () => {
    await db.create(motorSeed() as unknown as Product);

    const res = await request(app)
      .post('/api/v1/compat/check')
      .send({ a: { id: 'missing', type: 'drive' }, b: { id: 'm1', type: 'motor' } });

    expect(res.status).toBe(404);
    expect(res.body.success).toBe(false);
    expect(res.body.error).toContain('Product a');
  });

  it('404s when both products are absent (verifies the parallel-read fan-out surfaces both misses)', async () => {
    const res = await request(app)
      .post('/api/v1/compat/check')
      .send({ a: { id: 'nope-1', type: 'drive' }, b: { id: 'nope-2', type: 'motor' } });

    expect(res.status).toBe(404);
    expect(res.body.error).toBe('Both products not found');
  });

  it('does not leak a same-id row across types: reading a drive-id as motor 404s even when the drive exists', async () => {
    // If PK composition drops the type and only keys on product_id, this
    // test would surface it — the shared id `x1` on both a drive and a
    // motor would return the wrong row when queried as the sibling type.
    await db.create(driveSeed({ product_id: 'x1' }) as unknown as Product);
    // No motor row with id 'x1' is seeded.

    const res = await request(app)
      .post('/api/v1/compat/check')
      .send({ a: { id: 'x1', type: 'drive' }, b: { id: 'x1', type: 'motor' } });

    expect(res.status).toBe(404);
    // The drive was found but the motor wasn't — error surfaces the
    // motor-side miss specifically.
    expect(res.body.error).toContain('Product b');
  });
});
