/**
 * Relations route — device compatibility queries.
 * GET /api/v1/relations/motors-for-actuator?id=<uuid>&type=linear_actuator|electric_cylinder
 * GET /api/v1/relations/drives-for-motor?id=<uuid>
 * GET /api/v1/relations/gearheads-for-motor?id=<uuid>
 *
 * Mirrors the predicates in `specodex/relations.py` (SCHEMA Phase 3a).
 * Two implementations are intentional during the Express → Python backend
 * migration (todo/PYTHON_BACKEND.md). The Python module is the future
 * source of truth; this file goes away when Express does. Until then,
 * the response shape mirrors `/api/v1/search` so the frontend table can
 * render results without new code.
 */

import { Router, Request, Response } from 'express';
import { z } from 'zod';
import { DynamoDBService } from '../db/dynamodb';
import config from '../config';

const router = Router();
const db = new DynamoDBService({ tableName: config.dynamodb.tableName });

// ---------------------------------------------------------------------------
// Local types — narrow shapes for the fields the predicates read. Phase 1
// fields (motor_mount_pattern, compatible_motor_mounts, input_motor_mount,
// output_motor_mount) are not yet in app/backend/src/types/models.ts; that
// hand-typed mirror retires with the Express deletion in PYTHON_BACKEND.md
// Phase 3, so adding them there now is wasted work.
// ---------------------------------------------------------------------------

type ValueUnit = { value?: number | null; unit?: string | null };
type MinMaxUnit = { min?: number | null; max?: number | null; unit?: string | null };

interface MotorRecord {
  product_id: string;
  product_type: 'motor';
  motor_mount_pattern?: string | null;
  rated_voltage?: MinMaxUnit | null;
  rated_current?: ValueUnit | null;
  rated_torque?: ValueUnit | null;
  rated_speed?: ValueUnit | null;
  shaft_diameter?: ValueUnit | null;
  encoder_feedback_support?: string | null;
  [key: string]: unknown;
}

interface DriveRecord {
  product_id: string;
  product_type: 'drive';
  input_voltage?: MinMaxUnit | null;
  rated_current?: ValueUnit | null;
  encoder_feedback_support?: string[] | null;
  [key: string]: unknown;
}

interface GearheadRecord {
  product_id: string;
  product_type: 'gearhead';
  input_motor_mount?: string[] | null;
  output_motor_mount?: string | null;
  input_shaft_diameter?: ValueUnit | null;
  [key: string]: unknown;
}

interface ActuatorRecord {
  product_id: string;
  product_type: 'linear_actuator' | 'electric_cylinder';
  // LinearActuator: list of accepted mounts.
  compatible_motor_mounts?: string[] | null;
  // ElectricCylinder: single integrated mount.
  motor_mount_pattern?: string | null;
  // Requirements-narrowing fields (compatible_actuators floors).
  stroke?: ValueUnit | null;
  max_push_force?: ValueUnit | null;
  max_linear_speed?: ValueUnit | null;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Predicates — port of specodex/relations.py. "Exclude on missing data"
// philosophy: any None / null / missing field returns false. Recall loss
// from incomplete records is the right failure mode for compatibility
// queries.
// ---------------------------------------------------------------------------

function valueGte(a: ValueUnit | null | undefined, b: ValueUnit | null | undefined): boolean {
  if (!a || !b) return false;
  if (a.value == null || b.value == null) return false;
  if (!a.unit || !b.unit || a.unit !== b.unit) return false;
  return a.value >= b.value;
}

function rangeWithin(
  inner: MinMaxUnit | null | undefined,
  outer: MinMaxUnit | null | undefined,
): boolean {
  if (!inner || !outer) return false;
  if (!inner.unit || !outer.unit || inner.unit !== outer.unit) return false;
  if (inner.min != null && outer.min != null && inner.min < outer.min) return false;
  if (inner.max != null && outer.max != null && inner.max > outer.max) return false;
  if (inner.min == null && inner.max == null) return false;
  return true;
}

function shaftCompatible(
  motorShaft: ValueUnit | null | undefined,
  gearheadInput: ValueUnit | null | undefined,
): boolean {
  if (!motorShaft || !gearheadInput) return false;
  if (motorShaft.value == null || gearheadInput.value == null) return false;
  if (!motorShaft.unit || !gearheadInput.unit) return false;
  if (motorShaft.unit !== gearheadInput.unit) return false;
  return Math.abs(motorShaft.value - gearheadInput.value) <= 0.1;
}

function encoderIntersect(motor: MotorRecord, drive: DriveRecord): boolean {
  const motorProto = motor.encoder_feedback_support;
  const driveProtos = drive.encoder_feedback_support;
  if (!motorProto || !driveProtos || driveProtos.length === 0) return false;
  return driveProtos.includes(motorProto);
}

/**
 * Port of relations.py `_meets_floor`: present, in canonical `unit`,
 * and >= `floor`. A different unit means a different physical quantity
 * or an un-normalised field — excluded either way (precision over
 * recall).
 */
function meetsFloor(
  value: ValueUnit | null | undefined,
  floor: number,
  unit: string,
): boolean {
  if (!value || value.value == null || !value.unit) return false;
  if (value.unit !== unit) return false;
  return value.value >= floor;
}

/**
 * Distribution-position metadata attached to each actuator candidate
 * in the `/actuators` response. Drives todo/BUILD.md Part 3's
 * "8th most common stroke in catalogue" badge. Computed once per
 * request from the candidate set (not the full catalogue), so the
 * rank is meaningful relative to what passed the filter.
 *
 * `spec` is fixed to `stroke` for the first cut — Build's most
 * user-facing dimension, and the one the doc's example targets.
 * Future expansion can pick per-request (peak_force_rating, peak_velocity_rating)
 * via a query parameter; not needed for Phase 1.
 */
type DistributionPosition = {
  spec: 'stroke' | 'peak_force_rating' | 'peak_velocity_rating';
  rank: number;
  cluster_count: number;
};

/**
 * Group actuators by integer-millimetre stroke, rank the resulting
 * clusters by size (1 = most populous), and attach a
 * `_distribution_position` block to each candidate.
 *
 * Actuators missing the field or carrying it in a non-canonical unit
 * receive no badge — same precision-over-recall rule the predicates
 * follow. Ties in cluster size are broken by the spec value
 * (ascending) so a stable rank survives across calls.
 */
function attachStrokeDistributionPositions(
  candidates: ActuatorRecord[],
): Array<ActuatorRecord & { _distribution_position?: DistributionPosition }> {
  const bucketKey = (v: ValueUnit | null | undefined): number | null => {
    if (!v || v.value == null || v.unit !== 'mm') return null;
    return Math.round(v.value);
  };

  const counts = new Map<number, number>();
  for (const c of candidates) {
    const k = bucketKey(c.stroke);
    if (k == null) continue;
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }

  // Sort clusters by size descending, tiebreak on the stroke value
  // ascending so the rank is deterministic across identical-size
  // clusters.
  const ranked = [...counts.entries()].sort((a, b) => {
    if (b[1] !== a[1]) return b[1] - a[1];
    return a[0] - b[0];
  });
  const rankByBucket = new Map<number, number>();
  ranked.forEach(([bucket], i) => rankByBucket.set(bucket, i + 1));

  return candidates.map(c => {
    const k = bucketKey(c.stroke);
    if (k == null) return c;
    const rank = rankByBucket.get(k);
    const cluster_count = counts.get(k);
    if (rank == null || cluster_count == null) return c;
    return {
      ...c,
      _distribution_position: { spec: 'stroke', rank, cluster_count },
    };
  });
}

/**
 * Port of relations.py `compatible_actuators` — the requirements-first
 * entry point for Build's slot-fill sequence (todo/BUILD.md Part 4).
 * Each floor is optional and independent; an unset floor applies no
 * constraint (Build's "blank = no constraint applied" rule). A set
 * floor excludes actuators missing the field or carrying it in a
 * non-canonical unit.
 */
function compatibleActuators(
  actuatorDb: ActuatorRecord[],
  floors: {
    minStrokeMm?: number;
    minPeakForceN?: number;
    minPeakVelocityMmS?: number;
  },
): ActuatorRecord[] {
  return actuatorDb.filter(a => {
    if (floors.minStrokeMm != null && !meetsFloor(a.stroke, floors.minStrokeMm, 'mm')) {
      return false;
    }
    if (
      floors.minPeakForceN != null &&
      !meetsFloor(a.max_push_force, floors.minPeakForceN, 'N')
    ) {
      return false;
    }
    if (
      floors.minPeakVelocityMmS != null &&
      !meetsFloor(a.max_linear_speed, floors.minPeakVelocityMmS, 'mm/s')
    ) {
      return false;
    }
    return true;
  });
}

function compatibleMotors(
  actuator: ActuatorRecord,
  motorDb: MotorRecord[],
): MotorRecord[] {
  let mounts: Set<string>;
  if (actuator.product_type === 'linear_actuator') {
    mounts = new Set(actuator.compatible_motor_mounts || []);
  } else {
    mounts = actuator.motor_mount_pattern
      ? new Set([actuator.motor_mount_pattern])
      : new Set();
  }
  if (mounts.size === 0) return [];
  return motorDb.filter(m => m.motor_mount_pattern && mounts.has(m.motor_mount_pattern));
}

function compatibleDrives(motor: MotorRecord, driveDb: DriveRecord[]): DriveRecord[] {
  if (!motor.rated_voltage || !motor.rated_current) return [];
  return driveDb.filter(d => {
    if (!rangeWithin(motor.rated_voltage, d.input_voltage)) return false;
    if (!valueGte(d.rated_current, motor.rated_current)) return false;
    if (!encoderIntersect(motor, d)) return false;
    return true;
  });
}

function compatibleGearheads(
  motor: MotorRecord,
  gearheadDb: GearheadRecord[],
): GearheadRecord[] {
  if (!motor.motor_mount_pattern) return [];
  return gearheadDb.filter(g => {
    if (!g.input_motor_mount || g.input_motor_mount.length === 0) return false;
    if (!g.input_motor_mount.includes(motor.motor_mount_pattern!)) return false;
    if (!shaftCompatible(motor.shaft_diameter, g.input_shaft_diameter)) return false;
    return true;
  });
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

const ActuatorIdQuery = z.object({
  id: z.string().min(1),
  type: z.enum(['linear_actuator', 'electric_cylinder']),
});

// Floors arrive as query strings; coerce + reject negatives/NaN. All
// optional — Build's progressive-narrowing rule. `min_duty_cycle` and
// `orientation` are accepted for the BUILD.md ActuatorQuery contract
// but deliberately don't filter: duty cycle is a motor-thermal concept
// and orientation a downstream derating hint (see relations.py
// compatible_actuators docstring).
const ActuatorsQuery = z.object({
  min_stroke_mm: z.coerce.number().nonnegative().optional(),
  min_peak_force_n: z.coerce.number().nonnegative().optional(),
  min_peak_velocity_mm_s: z.coerce.number().nonnegative().optional(),
  min_duty_cycle: z.coerce.number().min(0).max(1).optional(),
  orientation: z.enum(['horizontal', 'vertical']).optional(),
});

const MotorIdQuery = z.object({
  id: z.string().min(1),
});

router.get('/actuators', async (req: Request, res: Response): Promise<void> => {
  const parsed = ActuatorsQuery.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({
      success: false,
      error: 'Invalid query parameters',
      details: parsed.error.issues.map(i => `${i.path.join('.')}: ${i.message}`),
    });
    return;
  }
  const q = parsed.data;
  console.log(
    `[relations] actuators stroke>=${q.min_stroke_mm ?? '-'} force>=${q.min_peak_force_n ?? '-'} velocity>=${q.min_peak_velocity_mm_s ?? '-'}`,
  );

  const actuators = (await db.list('linear_actuator')) as unknown as ActuatorRecord[];
  const matches = compatibleActuators(actuators, {
    minStrokeMm: q.min_stroke_mm,
    minPeakForceN: q.min_peak_force_n,
    minPeakVelocityMmS: q.min_peak_velocity_mm_s,
  });
  const annotated = attachStrokeDistributionPositions(matches);
  res.json({
    success: true,
    data: annotated,
    count: annotated.length,
    total: actuators.length,
  });
});

router.get('/motors-for-actuator', async (req: Request, res: Response): Promise<void> => {
  const parsed = ActuatorIdQuery.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({
      success: false,
      error: 'Invalid query parameters',
      details: parsed.error.issues.map(i => `${i.path.join('.')}: ${i.message}`),
    });
    return;
  }
  const { id, type } = parsed.data;

  const safeId = id.replace(/\r|\n/g, '');
  console.log(`[relations] motors-for-actuator id=${safeId} type=${type}`);

  const actuator = (await db.read(id, type)) as ActuatorRecord | null;
  if (!actuator) {
    res.status(404).json({ success: false, error: 'Actuator not found' });
    return;
  }
  const motors = (await db.list('motor')) as unknown as MotorRecord[];
  const matches = compatibleMotors(actuator, motors);
  res.json({ success: true, data: matches, count: matches.length });
});

router.get('/drives-for-motor', async (req: Request, res: Response): Promise<void> => {
  const parsed = MotorIdQuery.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({
      success: false,
      error: 'Invalid query parameters',
      details: parsed.error.issues.map(i => `${i.path.join('.')}: ${i.message}`),
    });
    return;
  }
  const { id } = parsed.data;

  const safeId = id.replace(/\r|\n/g, '');
  console.log(`[relations] drives-for-motor id=${safeId}`);

  const motor = (await db.read(id, 'motor')) as MotorRecord | null;
  if (!motor) {
    res.status(404).json({ success: false, error: 'Motor not found' });
    return;
  }
  const drives = (await db.list('drive')) as unknown as DriveRecord[];
  const matches = compatibleDrives(motor, drives);
  res.json({ success: true, data: matches, count: matches.length });
});

router.get('/gearheads-for-motor', async (req: Request, res: Response): Promise<void> => {
  const parsed = MotorIdQuery.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({
      success: false,
      error: 'Invalid query parameters',
      details: parsed.error.issues.map(i => `${i.path.join('.')}: ${i.message}`),
    });
    return;
  }
  const { id } = parsed.data;

  const safeId = id.replace(/\r|\n/g, '');
  console.log(`[relations] gearheads-for-motor id=${safeId}`);

  const motor = (await db.read(id, 'motor')) as MotorRecord | null;
  if (!motor) {
    res.status(404).json({ success: false, error: 'Motor not found' });
    return;
  }
  const gearheads = (await db.list('gearhead')) as unknown as GearheadRecord[];
  const matches = compatibleGearheads(motor, gearheads);
  res.json({ success: true, data: matches, count: matches.length });
});

// Internal predicates exported for jest tests; not part of the route surface.
export const _predicates = {
  valueGte,
  rangeWithin,
  shaftCompatible,
  encoderIntersect,
  meetsFloor,
  compatibleActuators,
  compatibleMotors,
  compatibleDrives,
  compatibleGearheads,
  attachStrokeDistributionPositions,
};

export default router;
