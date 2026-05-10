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

const MotorIdQuery = z.object({
  id: z.string().min(1),
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
  compatibleMotors,
  compatibleDrives,
  compatibleGearheads,
};

export default router;
