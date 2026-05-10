/**
 * Client-side pairwise compatibility — mirrors app/backend/src/services/compat.ts.
 *
 * Why duplicated: the build-tray filter narrows a list of N candidates against
 * each anchor in the build. Doing this server-side would mean an O(N) request
 * fan-out or a new endpoint; doing it client-side is O(N) work in the browser
 * over data we already have in AppContext. For the current catalog (drives ≤ 4,
 * motors ~100, gearheads ~30) it's a few ms.
 *
 * Returns the strict report (`ok` | `partial` | `fail`). The detail-modal
 * compat checker still goes through the API and gets a softened report.
 */
import { Drive, Gearhead, MinMaxUnit, Motor, Product, ValueUnit } from '../types/models';

export type StrictStatus = 'ok' | 'partial' | 'fail';

export interface StrictCheckResult {
  field: string;
  status: StrictStatus;
  detail: string;
}

export interface StrictCompatResult {
  from_port: string;
  to_port: string;
  status: StrictStatus;
  checks: StrictCheckResult[];
}

export interface StrictCompatibilityReport {
  from_type: string;
  to_type: string;
  status: StrictStatus;
  results: StrictCompatResult[];
}

const SUPPORTED_PAIRS = new Set(['drive|motor', 'motor|drive', 'motor|gearhead', 'gearhead|motor']);

export const ADJACENT_TYPES: Record<string, ('drive' | 'motor' | 'gearhead')[]> = {
  drive: ['motor'],
  motor: ['drive', 'gearhead'],
  gearhead: ['motor'],
};

export const BUILD_SLOTS = ['drive', 'motor', 'gearhead'] as const;
export type BuildSlot = (typeof BUILD_SLOTS)[number];

export function isPairSupported(aType: string, bType: string): boolean {
  return SUPPORTED_PAIRS.has(`${aType}|${bType}`);
}

const isMinMaxUnit = (v: unknown): v is MinMaxUnit =>
  !!v && typeof v === 'object' && 'min' in (v as object) && 'max' in (v as object) && 'unit' in (v as object);

const ok = (field: string, detail: string): StrictCheckResult => ({ field, status: 'ok', detail });
const partial = (field: string, detail: string): StrictCheckResult => ({ field, status: 'partial', detail });
const fail = (field: string, detail: string): StrictCheckResult => ({ field, status: 'fail', detail });

function rollUp(checks: { status: StrictStatus }[]): StrictStatus {
  if (checks.some(c => c.status === 'fail')) return 'fail';
  if (checks.some(c => c.status === 'partial')) return 'partial';
  return 'ok';
}

// Helper-input types: generated TS marks Optional fields as `?: T | null`,
// so each helper accepts the wider `T | null | undefined` and treats null
// the same as undefined.
type Maybe<T> = T | null | undefined;

function checkVoltageFits(supply: Maybe<MinMaxUnit>, demand: Maybe<MinMaxUnit | ValueUnit>): StrictCheckResult {
  if (!supply || !demand) return partial('voltage', 'one side missing voltage');
  const dMin = isMinMaxUnit(demand) ? demand.min : demand.value;
  const dMax = isMinMaxUnit(demand) ? demand.max : demand.value;
  const dUnit = demand.unit;
  if (supply.unit !== dUnit) return fail('voltage', `unit mismatch: ${supply.unit} vs ${dUnit}`);
  if (dMin == null || dMax == null || supply.min == null || supply.max == null) {
    return partial('voltage', 'voltage range incomplete');
  }
  if (dMin < supply.min || dMax > supply.max) {
    return fail('voltage', `demand ${dMin}-${dMax} outside supply ${supply.min}-${supply.max} ${supply.unit}`);
  }
  return ok('voltage', `${dMin}-${dMax} within ${supply.min}-${supply.max} ${supply.unit}`);
}

function checkSupplyGeDemand(supply: Maybe<ValueUnit>, demand: Maybe<ValueUnit>, field: string): StrictCheckResult {
  if (!supply || !demand) return partial(field, `one side missing ${field}`);
  if (supply.unit !== demand.unit) return fail(field, `unit mismatch: ${supply.unit} vs ${demand.unit}`);
  if (supply.value < demand.value) {
    return fail(field, `supply ${supply.value} < demand ${demand.value} ${supply.unit}`);
  }
  return ok(field, `supply ${supply.value} ≥ demand ${demand.value} ${supply.unit}`);
}

function checkDemandLeMax(demand: Maybe<ValueUnit>, max: Maybe<ValueUnit>, field: string): StrictCheckResult {
  return checkSupplyGeDemand(max, demand, field);
}

function checkEqualString(a: Maybe<string>, b: Maybe<string>, field: string): StrictCheckResult {
  if (!a || !b) return partial(field, `one side missing ${field}`);
  if (a.trim().toLowerCase() !== b.trim().toLowerCase()) return fail(field, `${a} != ${b}`);
  return ok(field, a);
}

function checkMembership(value: Maybe<string>, options: Maybe<string[]>, field: string): StrictCheckResult {
  if (!value || !options || options.length === 0) return partial(field, 'one side missing');
  const v = value.trim().toLowerCase();
  if (options.some(o => o.trim().toLowerCase() === v)) return ok(field, `${value} in supported list`);
  return fail(field, `${value} not in [${options.join(', ')}]`);
}

function checkShaftFit(motorShaft: Maybe<ValueUnit>, bore: Maybe<ValueUnit>): StrictCheckResult {
  if (!motorShaft || !bore) return partial('shaft_diameter', 'one side missing');
  if (motorShaft.unit !== bore.unit) return fail('shaft_diameter', `unit mismatch: ${motorShaft.unit} vs ${bore.unit}`);
  if (Math.abs(motorShaft.value - bore.value) > 0.1) {
    return fail('shaft_diameter', `motor ${motorShaft.value} ${motorShaft.unit} ≠ gearhead bore ${bore.value} ${bore.unit}`);
  }
  return ok('shaft_diameter', `${motorShaft.value} ${motorShaft.unit} matches bore`);
}

function compareDriveMotorPower(drive: Drive, motor: Motor): StrictCompatResult {
  const checks: StrictCheckResult[] = [
    checkVoltageFits(drive.input_voltage, motor.rated_voltage),
    checkSupplyGeDemand(drive.rated_current, motor.rated_current, 'current'),
    checkSupplyGeDemand(drive.rated_power, motor.rated_power, 'power'),
  ];
  return { from_port: 'Drive.motor_output', to_port: 'Motor.power_input', status: rollUp(checks), checks };
}

function compareDriveMotorFeedback(drive: Drive, motor: Motor): StrictCompatResult {
  // Post-DOUBLE_TAP: motor.encoder_feedback_support is now a structured
  // EncoderFeedback (or null), and drive.encoder_feedback_support is
  // List[EncoderProtocol]. The wire-protocol identity is what has to
  // line up; the device behind the wire is the motor's problem.
  const motorProtocol = motor.encoder_feedback_support?.protocol ?? null;
  const checks: StrictCheckResult[] = [
    checkMembership(motorProtocol, drive.encoder_feedback_support, 'encoder_type'),
  ];
  return { from_port: 'Drive.feedback', to_port: 'Motor.feedback', status: rollUp(checks), checks };
}

function compareMotorGearheadShaft(motor: Motor, gearhead: Gearhead): StrictCompatResult {
  const checks: StrictCheckResult[] = [
    checkEqualString(motor.frame_size, gearhead.frame_size, 'frame_size'),
    checkShaftFit(motor.shaft_diameter, gearhead.input_shaft_diameter),
    checkDemandLeMax(motor.rated_speed, gearhead.max_input_speed, 'speed'),
  ];
  return { from_port: 'Motor.shaft_output', to_port: 'Gearhead.shaft_input', status: rollUp(checks), checks };
}

/**
 * Strict client-side compat. Throws if the pair isn't supported.
 */
export function check(a: Product, b: Product): StrictCompatibilityReport {
  const key = `${a.product_type}|${b.product_type}`;
  if (!SUPPORTED_PAIRS.has(key)) {
    throw new Error(`Unsupported product pair: ${a.product_type} + ${b.product_type}`);
  }
  const reverse = key === 'motor|drive' || key === 'gearhead|motor';
  const [first, second] = reverse ? [b, a] : [a, b];
  const results: StrictCompatResult[] = [];
  if (first.product_type === 'drive' && second.product_type === 'motor') {
    results.push(compareDriveMotorPower(first as Drive, second as Motor));
    results.push(compareDriveMotorFeedback(first as Drive, second as Motor));
  } else if (first.product_type === 'motor' && second.product_type === 'gearhead') {
    results.push(compareMotorGearheadShaft(first as Motor, second as Gearhead));
  }
  return {
    from_type: first.product_type,
    to_type: second.product_type,
    status: results.length === 0 ? 'partial' : rollUp(results),
    results,
  };
}
