// =============================================================================
// Form / Fit / Function lenses for the results table.
//
// The engineering triad for "will this part work for me":
//   - Function — what it does: torque, speed, power, force, backlash,
//     current, accuracy. The default question for motion parts.
//   - Form     — what it physically is: envelope, largest dimension,
//     weight, frame/housing size.
//   - Fit      — how it connects: mechanical interfaces (shaft, flange,
//     mount pattern), electrical supply, protocols and I/O, software
//     features, standards and ratings. Sparse on purely mechanical parts,
//     dense on drives and sensors.
//
// A lens is optional and they combine: no lens = the normal curated view;
// one lens snaps the table to that lens's populated columns; several give
// their union (e.g. Function + Form = torque beside length).
// =============================================================================

import type { AttributeMetadata } from './filters';
import { getCategoryForKey } from './filters';

export type Lens = 'form' | 'fit' | 'function';

export const LENS_ORDER: readonly Lens[] = ['function', 'form', 'fit'];

export const LENS_LABELS: Record<Lens, string> = {
  function: 'Function',
  form: 'Form',
  fit: 'Fit',
};

export const LENS_HINTS: Record<Lens, string> = {
  function: 'Performance: torque, speed, power, force, accuracy',
  form: 'Physical: size, largest dimension, weight, frame',
  fit: 'Interfaces: shaft, mounting, supply, protocols, I/O, standards',
};

// Explicit placements. Anything not listed falls back by category (below).
const LENS_BY_KEY: Record<string, Lens> = {
  // ----- Form -----
  max_dimension: 'form',
  dimensions: 'form',
  weight: 'form',
  housing_size: 'form',
  housing_length: 'form',
  housing_material: 'form',
  materials: 'form',
  vendor_frame_size: 'form',
  sensing_face_material: 'form',

  // ----- Fit: mechanical interface -----
  frame_size: 'fit',
  nema_size: 'fit',
  motor_mount_pattern: 'fit',
  mounting_flange: 'fit',
  input_mounting_flange: 'fit',
  output_flange: 'fit',
  shaft_diameter: 'fit',
  shaft_length: 'fit',
  shaft_modification: 'fit',
  input_shaft_diameter: 'fit',
  output_shaft_diameter: 'fit',
  compatible_motor_frames: 'fit',
  mounting: 'fit',
  mounting_types: 'fit',
  mounting_position: 'fit',
  // ----- Fit: electrical supply + connection -----
  rated_voltage: 'fit',
  input_voltage: 'fit',
  input_voltage_phases: 'fit',
  input_voltage_frequency: 'fit',
  supply_voltage: 'fit',
  coil_voltage_options: 'fit',
  coil_voltage_range_ac: 'fit',
  coil_voltage_range_dc: 'fit',
  output_type: 'fit',
  output_function: 'fit',
  connection_type: 'fit',
  connection: 'fit',
  wiring: 'fit',
  connector_pins: 'fit',
  cable_length: 'fit',
  io_link: 'fit',
  max_load_current: 'fit',
  min_load_current: 'fit',
  no_load_current: 'fit',
  leakage_current: 'fit',
  voltage_drop: 'fit',
  short_circuit_protection: 'fit',
  reverse_polarity_protection: 'fit',
  tightening_torque: 'fit',
  // ----- Fit: features, protocols, standards -----
  encoder_feedback_support: 'fit',
  control_modes: 'fit',
  fieldbus: 'fit',
  ip_rating: 'fit',

  // ----- Function: placements the category fallback would get wrong -----
  rated_power: 'function',
  rated_current: 'function',
  peak_current: 'function',
  payload: 'function',
  reach: 'function',
  stroke: 'function',
  degrees_of_freedom: 'function',
  max_continuous_torque: 'function',
  max_peak_torque: 'function',
  sensing_distance: 'function',
  assured_sensing_distance: 'function',
  hysteresis: 'function',
  repeatability: 'function',
};

/**
 * Which lens an attribute belongs to; `null` for identification keys
 * (manufacturer, series…), which stay pinned in every lens view.
 */
export const getLensForKey = (key: string): Lens | null => {
  const explicit = LENS_BY_KEY[key];
  if (explicit) return explicit;
  // Nested keys (e.g. 'encoder_feedback_support.resolution_counts_per_rev')
  // follow their parent.
  const root = key.split('.')[0];
  if (root !== key && LENS_BY_KEY[root]) return LENS_BY_KEY[root];
  switch (getCategoryForKey(root)) {
    case 'identification':
      return null;
    case 'mechanical':
    case 'electrical':
      return 'function';
    case 'software':
    case 'network':
    case 'environment':
      return 'fit';
    default:
      return null;
  }
};

// A lens is something the user asked for explicitly, so its columns are
// shown even when sparse — only the effectively-empty ones are dropped.
export const LENS_MIN_FILL_RATE = 0.05;

// Identification columns kept leading in every lens view.
const PINNED_KEYS = new Set(['manufacturer']);

/**
 * Columns to render while one or more lenses are active.
 *
 * Contract:
 *   - `lenses` empty → returns `null` (caller uses the normal view).
 *   - Pinned identification keys first (when present in `columnAttributes`).
 *   - Then every attribute whose lens is selected, populated at ≥
 *     `minFillRate` over the loaded rows (a key missing from `fillRates`
 *     counts as populated), minus `userHiddenKeys`.
 *   - Input order is preserved within that (callers pass the
 *     `orderColumnAttributes` order, so authored COLUMN_ORDER leads).
 *   - At most `maxVisible` columns, pinned ones included.
 */
export const computeLensColumns = (
  columnAttributes: readonly AttributeMetadata[],
  lenses: readonly Lens[],
  userHiddenKeys: readonly string[],
  maxVisible: number,
  fillRates?: ReadonlyMap<string, number>,
  minFillRate: number = LENS_MIN_FILL_RATE,
): AttributeMetadata[] | null => {
  if (lenses.length === 0) return null;
  const active = new Set(lenses);
  const hidden = new Set(userHiddenKeys);
  const fillOf = (k: string) => fillRates?.get(k) ?? 1;

  const pinned = columnAttributes.filter(a => PINNED_KEYS.has(a.key));
  const inLens = columnAttributes.filter(a => {
    if (PINNED_KEYS.has(a.key) || hidden.has(a.key)) return false;
    const lens = getLensForKey(a.key);
    return lens !== null && active.has(lens) && fillOf(a.key) >= minFillRate;
  });
  return [...pinned, ...inLens].slice(0, Math.max(0, maxVisible));
};

export const isLens = (v: unknown): v is Lens =>
  v === 'form' || v === 'fit' || v === 'function';

export const isLensArray = (v: unknown): v is Lens[] =>
  Array.isArray(v) && v.every(isLens);
