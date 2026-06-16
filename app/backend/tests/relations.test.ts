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

describe('GET /api/v1/relations/actuators', () => {
  const la = (id: string, over: Record<string, unknown>) => ({
    product_id: id,
    product_type: 'linear_actuator',
    stroke: { value: 300, unit: 'mm' },
    max_push_force: { value: 200, unit: 'N' },
    max_linear_speed: { value: 500, unit: 'mm/s' },
    ...over,
  });

  it('returns the full catalogue when no floors are set (blank = no constraint)', async () => {
    mockList([la('a-1', {}), la('a-2', { stroke: null })]);
    const res = await request(app).get('/api/v1/relations/actuators');
    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.count).toBe(2);
    expect(res.body.total).toBe(2);
  });

  it('applies each floor independently and reports total alongside count', async () => {
    mockList([
      la('long-strong', {}),
      la('short', { stroke: { value: 100, unit: 'mm' } }),
      la('weak', { max_push_force: { value: 50, unit: 'N' } }),
    ]);
    const res = await request(app).get(
      '/api/v1/relations/actuators?min_stroke_mm=200&min_peak_force_n=175',
    );
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(1);
    expect(res.body.total).toBe(3);
    expect(res.body.data[0].product_id).toBe('long-strong');
  });

  it('excludes rows missing the field or in a non-canonical unit when a floor is set', async () => {
    mockList([
      la('no-stroke', { stroke: null }),
      la('inches', { stroke: { value: 12, unit: 'in' } }),
      la('ok', {}),
    ]);
    const res = await request(app).get('/api/v1/relations/actuators?min_stroke_mm=1');
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(1);
    expect(res.body.data[0].product_id).toBe('ok');
  });

  it('attaches a _distribution_position block ranked over the filtered candidate set', async () => {
    // Five candidates: three at 300mm, two at 200mm, one at 100mm.
    // Rank 1 = 300mm cluster (size 3), rank 2 = 200mm (size 2),
    // rank 3 = 100mm (size 1). Sparse row (no stroke) gets no badge.
    mockList([
      la('a300-1', {}),
      la('a300-2', {}),
      la('a300-3', {}),
      la('a200-1', { stroke: { value: 200, unit: 'mm' } }),
      la('a200-2', { stroke: { value: 200, unit: 'mm' } }),
      la('a100', { stroke: { value: 100, unit: 'mm' } }),
      la('sparse', { stroke: null }),
    ]);
    const res = await request(app).get('/api/v1/relations/actuators');
    expect(res.status).toBe(200);
    const byId = Object.fromEntries(
      res.body.data.map((r: { product_id: string }) => [r.product_id, r]),
    );
    expect(byId['a300-1']._distribution_position).toEqual({
      spec: 'stroke',
      rank: 1,
      cluster_count: 3,
    });
    expect(byId['a200-1']._distribution_position).toEqual({
      spec: 'stroke',
      rank: 2,
      cluster_count: 2,
    });
    expect(byId['a100']._distribution_position).toEqual({
      spec: 'stroke',
      rank: 3,
      cluster_count: 1,
    });
    expect(byId['sparse']._distribution_position).toBeUndefined();
  });

  it('accepts but does not filter on min_duty_cycle and orientation', async () => {
    mockList([la('a-1', {})]);
    const res = await request(app).get(
      '/api/v1/relations/actuators?min_duty_cycle=0.5&orientation=vertical',
    );
    expect(res.status).toBe(200);
    expect(res.body.count).toBe(1);
  });

  it('rejects negative floors, out-of-range duty cycle, and bad orientation', async () => {
    for (const qs of [
      'min_stroke_mm=-1',
      'min_peak_force_n=abc',
      'min_duty_cycle=1.5',
      'orientation=diagonal',
    ]) {
      const res = await request(app).get(`/api/v1/relations/actuators?${qs}`);
      expect(res.status).toBe(400);
      expect(res.body.success).toBe(false);
    }
  });
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

  it('meetsFloor requires presence, canonical unit, and >= floor', () => {
    expect(_predicates.meetsFloor({ value: 200, unit: 'mm' }, 200, 'mm')).toBe(true);
    expect(_predicates.meetsFloor({ value: 199.9, unit: 'mm' }, 200, 'mm')).toBe(false);
    expect(_predicates.meetsFloor({ value: 12, unit: 'in' }, 1, 'mm')).toBe(false);
    expect(_predicates.meetsFloor(null, 0, 'mm')).toBe(false);
    expect(_predicates.meetsFloor({ value: null, unit: 'mm' }, 0, 'mm')).toBe(false);
  });

  it('compatibleActuators mirrors the Python floor semantics', () => {
    const rows = [
      {
        product_id: 'full',
        product_type: 'linear_actuator' as const,
        stroke: { value: 300, unit: 'mm' },
        max_push_force: { value: 200, unit: 'N' },
        max_linear_speed: { value: 500, unit: 'mm/s' },
      },
      {
        product_id: 'sparse',
        product_type: 'linear_actuator' as const,
      },
    ];
    // No floors → everything passes, sparse rows included.
    expect(_predicates.compatibleActuators(rows, {})).toHaveLength(2);
    // Any set floor excludes the sparse row.
    expect(
      _predicates.compatibleActuators(rows, { minPeakVelocityMmS: 100 }).map(r => r.product_id),
    ).toEqual(['full']);
    // Floor above the rating excludes the full row too.
    expect(_predicates.compatibleActuators(rows, { minPeakForceN: 201 })).toHaveLength(0);
  });

  it('attachStrokeDistributionPositions ranks clusters by size and skips sparse rows', () => {
    const la = (id: string, stroke: ValueUnit | null) =>
      ({ product_id: id, product_type: 'linear_actuator', stroke } as ActuatorRecord);
    type ValueUnit = { value?: number | null; unit?: string | null };
    type ActuatorRecord = {
      product_id: string;
      product_type: 'linear_actuator' | 'electric_cylinder';
      stroke?: ValueUnit | null;
      [key: string]: unknown;
    };
    const rows: ActuatorRecord[] = [
      la('big-1', { value: 500, unit: 'mm' }),
      la('big-2', { value: 500, unit: 'mm' }),
      la('small', { value: 100, unit: 'mm' }),
      la('inches', { value: 10, unit: 'in' }), // non-canonical → no badge
      la('missing', null), // sparse → no badge
    ];
    const annotated = _predicates.attachStrokeDistributionPositions(rows);
    const byId = Object.fromEntries(annotated.map(r => [r.product_id, r]));
    expect(byId['big-1']._distribution_position).toEqual({
      spec: 'stroke',
      rank: 1,
      cluster_count: 2,
    });
    expect(byId['big-2']._distribution_position).toEqual({
      spec: 'stroke',
      rank: 1,
      cluster_count: 2,
    });
    expect(byId['small']._distribution_position).toEqual({
      spec: 'stroke',
      rank: 2,
      cluster_count: 1,
    });
    expect(byId['inches']._distribution_position).toBeUndefined();
    expect(byId['missing']._distribution_position).toBeUndefined();
  });

  it('attachStrokeDistributionPositions breaks ties on stroke ascending', () => {
    type ValueUnit = { value?: number | null; unit?: string | null };
    type ActuatorRecord = {
      product_id: string;
      product_type: 'linear_actuator' | 'electric_cylinder';
      stroke?: ValueUnit | null;
      [key: string]: unknown;
    };
    const mk = (id: string, mm: number): ActuatorRecord => ({
      product_id: id,
      product_type: 'linear_actuator',
      stroke: { value: mm, unit: 'mm' },
    });
    // Two same-size singleton clusters at 150mm and 300mm — the 150mm
    // cluster should rank ahead (ascending tiebreak). Deterministic
    // ordering matters so a Build user reload doesn't shuffle the badge.
    const annotated = _predicates.attachStrokeDistributionPositions([
      mk('a300', 300),
      mk('a150', 150),
    ]);
    const byId = Object.fromEntries(annotated.map(r => [r.product_id, r]));
    expect(byId['a150']._distribution_position?.rank).toBe(1);
    expect(byId['a300']._distribution_position?.rank).toBe(2);
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
