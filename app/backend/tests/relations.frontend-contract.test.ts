/**
 * Frontend ↔ Express contract for /api/v1/relations/*.
 *
 * Imports the exact URL builder the /build page uses
 * (app/frontend/src/api/relationsUrl.ts) and fires its output at the
 * real Express app. If the frontend and the router drift apart again,
 * this fails with a 404 instead of production silently serving
 * index.html. The FastAPI mirror is
 * app/backend_py/tests/test_relations_frontend_contract.py.
 */

import request from 'supertest';
import app from '../src/index';
import { DynamoDBService } from '../src/db/dynamodb';
import { buildRelationsUrl, type RelationKind } from '../../frontend/src/api/relationsUrl';

jest.mock('../src/db/dynamodb');

const actuator = {
  product_id: '0a69acc3-1275-53ff-9ac4-52d1fb2fecbe',
  product_type: 'linear_actuator',
  compatible_motor_mounts: ['NEMA 23'],
};

const motor = {
  product_id: '5b9f4c2e-8d1a-4e6b-9c3f-2a7d1e0b4c88',
  product_type: 'motor',
  motor_mount_pattern: 'NEMA 23',
  rated_voltage: { min: 200, max: 240, unit: 'V' },
  rated_current: { value: 3.0, unit: 'A' },
  rated_torque: { value: 1.0, unit: 'Nm' },
  rated_speed: { value: 3000, unit: 'rpm' },
  shaft_diameter: { value: 14.0, unit: 'mm' },
  encoder_feedback_support: 'endat_2_2',
};

const drive = {
  product_id: 'd-1',
  product_type: 'drive',
  input_voltage: { min: 200, max: 240, unit: 'V' },
  rated_current: { value: 5.0, unit: 'A' },
  encoder_feedback_support: ['endat_2_2'],
};

const gearhead = {
  product_id: 'g-1',
  product_type: 'gearhead',
  input_motor_mount: ['NEMA 23'],
  input_shaft_diameter: { value: 14.0, unit: 'mm' },
  gear_ratio: 10.0,
  max_continuous_torque: { value: 100.0, unit: 'Nm' },
  max_input_speed: { value: 6000, unit: 'rpm' },
};

const byType: Record<string, unknown[]> = {
  linear_actuator: [actuator],
  motor: [motor],
  drive: [drive],
  gearhead: [gearhead],
};

beforeEach(() => {
  jest.clearAllMocks();
  (DynamoDBService.prototype.read as jest.Mock).mockImplementation(
    async (id: string, type: string) =>
      (byType[type] as Array<{ product_id: string }> | undefined)?.find(r => r.product_id === id) ??
      null,
  );
  (DynamoDBService.prototype.list as jest.Mock).mockImplementation(
    async (type: string) => byType[type] ?? [],
  );
});

const cases: Array<[RelationKind, { product_id: string; product_type: string }, string]> = [
  ['motors-for-actuator', actuator, motor.product_id],
  ['drives-for-motor', motor, drive.product_id],
  ['gearheads-for-motor', motor, gearhead.product_id],
];

describe('frontend-built relations URLs resolve on the Express router', () => {
  it.each(cases)('%s', async (relation, source, expectedId) => {
    const url = buildRelationsUrl('', relation, source);
    const res = await request(app).get(url);

    expect(res.status).toBe(200);
    expect(res.headers['content-type']).toMatch(/application\/json/);
    expect(res.body.success).toBe(true);
    expect(res.body.data.map((r: { product_id: string }) => r.product_id)).toEqual([expectedId]);
  });
});

describe('unknown /api paths', () => {
  // The URL shape the frontend used to build. It must be a JSON 404 at
  // the origin; CloudFront no longer rewrites API errors into
  // index.html (see app/infrastructure/lib/frontend-stack.ts).
  it.each([
    '/api/v1/relations/motors?output=x&output_type=linear_actuator',
    '/api/v1/does-not-exist',
  ])('%s returns a JSON 404', async url => {
    const res = await request(app).get(url);
    expect(res.status).toBe(404);
    expect(res.headers['content-type']).toMatch(/application\/json/);
    expect(res.body).toEqual({ success: false, error: 'Endpoint not found' });
  });
});
