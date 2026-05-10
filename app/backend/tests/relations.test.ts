/**
 * Contract + predicate tests for /api/v1/relations/*.
 *
 * Exercises the three route handlers over a mocked DynamoDB plus the
 * underlying TS predicates (ported from specodex/relations.py — the
 * Python tests in tests/unit/test_relations.py cover the canonical
 * behaviour; these spot-check the TS port stays in sync).
 */

import request from 'supertest';
import app from '../src/index';
import { DynamoDBService } from '../src/db/dynamodb';
import { _predicates } from '../src/routes/relations';

jest.mock('../src/db/dynamodb');

function mockRead(value: unknown | null) {
  (DynamoDBService.prototype.read as jest.Mock).mockResolvedValue(value);
}
function mockList(value: unknown[]) {
  (DynamoDBService.prototype.list as jest.Mock).mockResolvedValue(value);
}

const motorNema23 = {
  product_id: 'm-23',
  product_type: 'motor',
  motor_mount_pattern: 'NEMA 23',
  rated_voltage: { min: 200, max: 240, unit: 'V' },
  rated_current: { value: 3.0, unit: 'A' },
  rated_torque: { value: 1.0, unit: 'Nm' },
  rated_speed: { value: 3000, unit: 'rpm' },
  shaft_diameter: { value: 14.0, unit: 'mm' },
  encoder_feedback_support: 'endat_2_2',
};

const drive240 = {
  product_id: 'd-240',
  product_type: 'drive',
  input_voltage: { min: 200, max: 240, unit: 'V' },
  rated_current: { value: 5.0, unit: 'A' },
  encoder_feedback_support: ['endat_2_2'],
};

const gearheadNema23 = {
  product_id: 'g-23',
  product_type: 'gearhead',
  input_motor_mount: ['NEMA 23'],
  input_shaft_diameter: { value: 14.0, unit: 'mm' },
};

const linearActuatorNema23 = {
  product_id: 'la-23',
  product_type: 'linear_actuator',
  compatible_motor_mounts: ['NEMA 23', 'NEMA 34'],
};

beforeEach(() => {
  jest.clearAllMocks();
});

describe('GET /api/v1/relations/motors-for-actuator', () => {
  it('returns 400 when id is missing', async () => {
    const res = await request(app).get('/api/v1/relations/motors-for-actuator?type=linear_actuator');
    expect(res.status).toBe(400);
    expect(res.body.success).toBe(false);
  });

  it('returns 400 on invalid type', async () => {
    const res = await request(app).get('/api/v1/relations/motors-for-actuator?id=la-23&type=motor');
    expect(res.status).toBe(400);
  });

  it('returns 404 when the actuator is not found', async () => {
    mockRead(null);
    const res = await request(app).get(
      '/api/v1/relations/motors-for-actuator?id=missing&type=linear_actuator',
    );
    expect(res.status).toBe(404);
  });

  it('returns matching motors on the happy path', async () => {
    mockRead(linearActuatorNema23);
    mockList([motorNema23, { ...motorNema23, product_id: 'm-17', motor_mount_pattern: 'NEMA 17' }]);
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
  it('returns 400 when id is missing', async () => {
    const res = await request(app).get('/api/v1/relations/drives-for-motor');
    expect(res.status).toBe(400);
  });

  it('returns 404 when motor not found', async () => {
    mockRead(null);
    const res = await request(app).get('/api/v1/relations/drives-for-motor?id=missing');
    expect(res.status).toBe(404);
  });

  it('returns drives whose envelope covers the motor', async () => {
    mockRead(motorNema23);
    mockList([
      drive240,
      { ...drive240, product_id: 'd-undersized', rated_current: { value: 1.0, unit: 'A' } },
    ]);
    const res = await request(app).get('/api/v1/relations/drives-for-motor?id=m-23');
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(1);
    expect(res.body.data[0].product_id).toBe('d-240');
  });
});

describe('GET /api/v1/relations/gearheads-for-motor', () => {
  it('returns 404 when motor not found', async () => {
    mockRead(null);
    const res = await request(app).get('/api/v1/relations/gearheads-for-motor?id=missing');
    expect(res.status).toBe(404);
  });

  it('filters gearheads by mount + shaft', async () => {
    mockRead(motorNema23);
    mockList([
      gearheadNema23,
      { ...gearheadNema23, product_id: 'g-wrong-mount', input_motor_mount: ['NEMA 17'] },
      {
        ...gearheadNema23,
        product_id: 'g-wrong-shaft',
        input_shaft_diameter: { value: 12.0, unit: 'mm' },
      },
    ]);
    const res = await request(app).get('/api/v1/relations/gearheads-for-motor?id=m-23');
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(1);
    expect(res.body.data[0].product_id).toBe('g-23');
  });
});

describe('Predicates (port of specodex/relations.py)', () => {
  it('valueGte handles equal and unit-mismatched values', () => {
    expect(_predicates.valueGte({ value: 3, unit: 'A' }, { value: 3, unit: 'A' })).toBe(true);
    expect(_predicates.valueGte({ value: 5000, unit: 'mA' }, { value: 3, unit: 'A' })).toBe(false);
    expect(_predicates.valueGte(null, { value: 3, unit: 'A' })).toBe(false);
  });

  it('rangeWithin requires fully-contained inner range', () => {
    expect(
      _predicates.rangeWithin(
        { min: 210, max: 230, unit: 'V' },
        { min: 200, max: 240, unit: 'V' },
      ),
    ).toBe(true);
    expect(
      _predicates.rangeWithin(
        { min: 180, max: 230, unit: 'V' },
        { min: 200, max: 240, unit: 'V' },
      ),
    ).toBe(false);
    expect(
      _predicates.rangeWithin(
        { min: 220, max: 220, unit: 'V' },
        { min: 200, max: 240, unit: 'V' },
      ),
    ).toBe(true);
  });

  it('shaftCompatible accepts within 0.1mm tolerance', () => {
    expect(
      _predicates.shaftCompatible({ value: 14.0, unit: 'mm' }, { value: 14.05, unit: 'mm' }),
    ).toBe(true);
    expect(
      _predicates.shaftCompatible({ value: 14.0, unit: 'mm' }, { value: 15.0, unit: 'mm' }),
    ).toBe(false);
  });

  it('encoderIntersect requires motor protocol in drive list', () => {
    expect(
      _predicates.encoderIntersect(
        { product_id: 'm', product_type: 'motor', encoder_feedback_support: 'endat_2_2' },
        { product_id: 'd', product_type: 'drive', encoder_feedback_support: ['endat_2_2'] },
      ),
    ).toBe(true);
    expect(
      _predicates.encoderIntersect(
        { product_id: 'm', product_type: 'motor', encoder_feedback_support: 'endat_2_2' },
        { product_id: 'd', product_type: 'drive', encoder_feedback_support: ['biss_c'] },
      ),
    ).toBe(false);
  });
});
