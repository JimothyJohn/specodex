/**
 * Filters & Sorting: Advanced Product Filtering System
 *
 * This module provides a comprehensive client-side filtering and sorting system
 * with support for complex data types, multi-level sorting, and natural alphanumeric ordering.
 *
 * Key Features:
 * - Multi-attribute filtering with include/exclude modes
 * - Numeric comparison operators (=, >, <, >=, <=, !=)
 * - Natural alphanumeric sorting (handles "abc2" vs "abc10" correctly)
 * - Multi-level sorting (sort by attribute1, then attribute2, then attribute3)
 * - Support for nested objects (ValueUnit, MinMaxUnit, Dimensions)
 * - Array filtering (fieldbus, control_modes, etc.)
 * - Type-aware attribute metadata (20 motor attributes + 23 drive attributes)
 *
 * Performance:
 * - Client-side filtering: ~10-50ms for 1000 products with 5 filters
 * - Multi-level sorting: ~20-100ms for 1000 products with 3 sort levels
 *
 * @module filters
 */

import { Product, ProductType } from './models';

// ========== Type Definitions ==========

/**
 * Filter mode determines how a filter criterion is applied
 *
 * - 'include': Product MUST match this filter (AND logic)
 * - 'exclude': Product MUST NOT match this filter (NOT logic)
 * - 'neutral': Filter is ignored (useful for temporarily disabling)
 *
 * Example: Filter by manufacturer="ACME" (include) + voltage>200 (include)
 * → Only products from ACME with voltage > 200V
 */
export type FilterMode = 'include' | 'exclude' | 'neutral';

/**
 * Comparison operators for filtering
 *
 * Supported operations:
 * - '=': Equal to (exact match)
 * - '>': Greater than
 * - '<': Less than
 * - '>=': Greater than or equal to
 * - '<=': Less than or equal to
 * - '!=': Not equal to
 *
 * Used for: voltage, current, power, speed, weight, etc.
 */
export type ComparisonOperator = '=' | '>' | '<' | '>=' | '<=' | '!=';

/**
 * Valid filter value types
 *
 * - string: Text matching (case-insensitive, partial match)
 * - string[]: Multiple string values (OR logic - matches any)
 * - number: Numeric comparison with operators
 * - boolean: True/false matching
 * - [number, number]: Range matching (min, max)
 */
export type FilterValue = string | string[] | number | number[] | boolean | [number, number];

/**
 * A single filter criterion
 *
 * Represents one filter condition in the filter chain.
 * Multiple criteria are combined with AND logic.
 *
 * Examples:
 * 1. { attribute: 'manufacturer', mode: 'include', value: 'ACME', displayName: 'Manufacturer' }
 *    → Only show products from ACME
 *
 * 2. { attribute: 'rated_voltage.min', mode: 'include', value: 200, operator: '>=', displayName: 'Rated Voltage' }
 *    → Only show products with rated voltage min >= 200V
 *
 * 3. { attribute: 'type', mode: 'exclude', value: 'servo', displayName: 'Motor Type' }
 *    → Hide all servo motors
 */
export interface FilterCriterion {
  attribute: string;          // Dot-notation path (e.g., 'rated_voltage.min')
  mode: FilterMode;           // Include/exclude/neutral
  value?: FilterValue;        // Filter value (optional for existence checks)
  operator?: ComparisonOperator; // Comparison operator (for numbers)
  displayName: string;        // Human-readable attribute name for UI
}

/**
 * Sort configuration for a single sort level
 *
 * Multi-level sorting: Apply sorts in array order
 * Example: [{ attr: 'manufacturer', dir: 'asc' }, { attr: 'power', dir: 'desc' }]
 * → Sort by manufacturer A-Z, then by power high-to-low within each manufacturer
 */
export interface SortConfig {
  attribute: string;          // Attribute to sort by (dot-notation supported)
  direction: 'asc' | 'desc';  // Sort direction
  displayName: string;        // Human-readable name for UI
}

/**
 * Attribute metadata for UI components
 *
 * Provides type information, display names, and units for each filterable attribute.
 * Used by AttributeSelector, FilterBar, and ProductList components.
 *
 * Type mappings:
 * - 'string': Text fields (manufacturer, part_number, series)
 * - 'number': Simple numeric fields (poles, ethernet_ports, ip_rating)
 * - 'boolean': True/false fields (currently unused but supported)
 * - 'range': MinMaxUnit objects ({ min: number, max: number, unit: string })
 * - 'array': Array fields (fieldbus, control_modes, safety_features)
 * - 'object': ValueUnit objects ({ value: number, unit: string })
 */
export interface AttributeMetadata {
  key: string;                // Attribute key (matches Product interface)
  displayName: string;        // Human-readable name for UI
  type: 'string' | 'number' | 'boolean' | 'range' | 'array' | 'object';
  // Product types this attribute applies to. Widened to `string[]` so
  // record-derived attributes can tag themselves with whatever product_type
  // appears at runtime (see `deriveAttributesFromRecords`).
  applicableTypes: string[];
  nested?: boolean;           // True for ValueUnit and MinMaxUnit types
  unit?: string;              // Unit of measurement (e.g., "V", "W", "A", "rpm", "kg")
  // Expert curation override for the frontend's default-visible column
  // set. Tri-state:
  //   true  — force visible by default (even for non-unit kinds)
  //   false — force hidden by default (even for unit-bearing kinds,
  //           e.g. voltage_constant is a ValueUnit but nobody compares
  //           motors by it, so a motor record shouldn't flood the
  //           table with it)
  //   undefined — fall through to the nested rule (unit-bearing visible,
  //           everything else hidden)
  // The frontend visibility predicate lives in ProductList.tsx.
  defaultVisible?: boolean;
  // When true, pre-populate this attribute as a filter chip the moment its
  // product type is selected. The chip lands without a value, exposing the
  // operator + slider so the user can dial in a constraint immediately —
  // this is for the 1-2 specs that *every* selection of this type starts
  // with (e.g. motor → rated torque + rated speed). Keep it tightly
  // curated; default chips that nobody touches become noise.
  defaultFilter?: boolean;
}

// ========== Attribute Categories ==========
//
// Attributes are grouped into semantic sections in the AttributeSelector
// dropdown so an integrator scanning for "voltage" doesn't have to scroll
// past 20 mechanical specs to find it. The category is intrinsic to the
// attribute *key*, not to its value payload — `rated_power` is electrical
// regardless of whether it carries a `ValueUnit` or a bare number — so this
// map is the single source of truth and lives separately from the per-type
// curated metadata. Keys not in the map fall through to 'other', which
// renders as a clearly-labelled bucket at the end so schema evolution
// surfaces uncategorized fields instead of silently dropping them.
//
// Section render order is fixed in CATEGORY_ORDER below.

export type AttributeCategory =
  | 'identification'
  | 'mechanical'
  | 'electrical'
  | 'software'
  | 'network'
  | 'environment'
  | 'other';

// Order is intentional: mechanical / electrical / environment lead because
// integrators sort by physical specs first, then drill into integration
// (software/network) and lookup metadata (identification). The first three
// are also the default-expanded set in AttributeSelector.tsx.
export const CATEGORY_ORDER: readonly AttributeCategory[] = [
  'mechanical',
  'electrical',
  'environment',
  'software',
  'network',
  'identification',
  'other',
];

// Sections expanded on first open. Matches the lead-three of CATEGORY_ORDER.
// Mirrored in AttributeSelector.tsx as the initial state.
export const DEFAULT_EXPANDED_CATEGORIES: readonly AttributeCategory[] = [
  'mechanical',
  'electrical',
  'environment',
];

export const CATEGORY_LABELS: Record<AttributeCategory, string> = {
  identification: 'Identification',
  mechanical: 'Mechanical',
  electrical: 'Electrical',
  software: 'Software',
  network: 'Network',
  environment: 'Environment',
  other: 'Other',
};

const CATEGORY_BY_KEY: Record<string, AttributeCategory> = {
  // ----- Identification -----
  manufacturer: 'identification',
  part_number: 'identification',
  product_name: 'identification',
  product_family: 'identification',
  component_type: 'identification',
  type: 'identification',
  series: 'identification',
  frame_size: 'identification',
  vendor_frame_size: 'identification',
  nema_size: 'identification',
  motor_type: 'identification',
  gear_type: 'identification',

  // ----- Mechanical -----
  rated_torque: 'mechanical',
  peak_torque: 'mechanical',
  rated_speed: 'mechanical',
  nominal_input_speed: 'mechanical',
  max_input_speed: 'mechanical',
  rotor_inertia: 'mechanical',
  gear_ratio: 'mechanical',
  stages: 'mechanical',
  backlash: 'mechanical',
  torsional_rigidity: 'mechanical',
  efficiency: 'mechanical',
  input_shaft_diameter: 'mechanical',
  output_shaft_diameter: 'mechanical',
  shaft_diameter: 'mechanical',
  max_radial_load: 'mechanical',
  max_axial_load: 'mechanical',
  weight: 'mechanical',
  payload: 'mechanical',
  reach: 'mechanical',
  degrees_of_freedom: 'mechanical',
  pose_repeatability: 'mechanical',
  max_tcp_speed: 'mechanical',
  mounting_position: 'mechanical',
  mounting_types: 'mechanical',
  materials: 'mechanical',
  stroke: 'mechanical',
  max_push_force: 'mechanical',
  max_pull_force: 'mechanical',
  continuous_force: 'mechanical',
  max_linear_speed: 'mechanical',
  linear_speed_at_rated_load: 'mechanical',
  lead_screw_pitch: 'mechanical',
  positioning_repeatability: 'mechanical',
  lubrication_type: 'mechanical',
  poles: 'mechanical',
  dimensions: 'mechanical',
  mechanical_durability: 'mechanical',

  // ----- Electrical -----
  rated_power: 'electrical',
  rated_voltage: 'electrical',
  input_voltage: 'electrical',
  input_voltage_phases: 'electrical',
  input_voltage_frequency: 'electrical',
  rated_current: 'electrical',
  peak_current: 'electrical',
  switching_frequency: 'electrical',
  voltage_constant: 'electrical',
  torque_constant: 'electrical',
  resistance: 'electrical',
  inductance: 'electrical',
  ie_ac3_400v: 'electrical',
  motor_power_ac3_400v_kw: 'electrical',
  motor_power_ac3_480v_hp: 'electrical',
  conventional_thermal_current: 'electrical',
  rated_insulation_voltage: 'electrical',
  rated_impulse_withstand_voltage: 'electrical',
  rated_operational_voltage_max: 'electrical',
  rated_frequency: 'electrical',
  sccr: 'electrical',
  coil_voltage_range_ac: 'electrical',
  coil_voltage_range_dc: 'electrical',
  coil_voltage_options: 'electrical',
  coil_pickup_factor: 'electrical',
  coil_dropout_factor: 'electrical',
  coil_time_constant: 'electrical',
  coil_power_consumption_sealed: 'electrical',
  coil_power_consumption_inrush: 'electrical',
  making_capacity: 'electrical',
  breaking_capacity: 'electrical',
  operating_time_close: 'electrical',
  operating_time_open: 'electrical',
  electrical_durability_ac3: 'electrical',
  operating_frequency_ac3: 'electrical',
  number_of_poles: 'electrical',
  auxiliary_contact_configuration: 'electrical',

  // ----- Software (control + feedback) -----
  control_modes: 'software',
  encoder_feedback_support: 'software',

  // ----- Network (fieldbus + I/O) -----
  fieldbus: 'network',
  ethernet_ports: 'network',
  digital_inputs: 'network',
  digital_outputs: 'network',
  analog_inputs: 'network',
  analog_outputs: 'network',

  // ----- Environment -----
  operating_temp: 'environment',
  storage_temp: 'environment',
  altitude_max: 'environment',
  max_humidity: 'environment',
  ip_rating: 'environment',
  cleanroom_class: 'environment',
  noise_level: 'environment',
  service_life: 'environment',
  approvals: 'environment',
  certifications: 'environment',
  standards_compliance: 'environment',
  safety_features: 'environment',
  safety_rating: 'environment',
  safety_certifications: 'environment',
  pollution_degree: 'environment',
};

/**
 * Map an attribute key to its semantic section in the AttributeSelector
 * dropdown. Unmapped keys fall through to 'other' — visible at the end of
 * the list as a clearly-labelled bucket so new schema fields don't vanish.
 */
export const getCategoryForKey = (key: string): AttributeCategory =>
  CATEGORY_BY_KEY[key] ?? 'other';

// ========== Attribute Metadata Functions ==========

/**
 * Get all filterable attributes for motors
 *
 * Returns 20 motor-specific attributes with metadata for filtering/sorting.
 * Each attribute includes display name, data type, and unit of measurement.
 *
 * Categories:
 * - Identification: manufacturer, part_number, type, series
 * - Electrical: rated_voltage, rated_current, peak_current, resistance, inductance
 * - Mechanical: rated_speed, rated_torque, peak_torque, poles, rotor_inertia
 * - Power: rated_power, voltage_constant, torque_constant
 * - Physical: weight, ip_rating
 * - Feedback: encoder_feedback_support
 *
 * @returns Array of 20 motor attribute metadata objects
 */
// Motor default-visible selection (defaultVisible: true) prioritizes
// the specs engineers actually sort motors by: power, torque, speed,
// voltage. Motor-designer-facing specs (voltage_constant, torque_constant,
// resistance, inductance) are pushed behind opt-in restore — they're
// unit-bearing but not comparison-useful for product selection. See
// specodex/models/motor.md.
export const getMotorAttributes = (): AttributeMetadata[] => [
  { key: 'manufacturer', displayName: 'Manufacturer', type: 'string', applicableTypes: ['motor'], defaultVisible: true },
  { key: 'part_number', displayName: 'Part Number', type: 'string', applicableTypes: ['motor'] },
  { key: 'type', displayName: 'Motor Type', type: 'string', applicableTypes: ['motor'] },
  { key: 'series', displayName: 'Series', type: 'string', applicableTypes: ['motor'] },
  { key: 'rated_power', displayName: 'Rated Power', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'W', defaultVisible: true },
  { key: 'rated_torque', displayName: 'Rated Torque', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'Nm', defaultVisible: true, defaultFilter: true },
  { key: 'peak_torque', displayName: 'Peak Torque', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'Nm', defaultVisible: true },
  { key: 'rated_speed', displayName: 'Rated Speed', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'rpm', defaultVisible: true, defaultFilter: true },
  { key: 'rated_voltage', displayName: 'Rated Voltage', type: 'range', applicableTypes: ['motor'], nested: true, unit: 'V', defaultVisible: true },
  { key: 'rated_current', displayName: 'Rated Current', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'A', defaultVisible: true },
  { key: 'rotor_inertia', displayName: 'Rotor Inertia', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'kg·cm²', defaultVisible: true },
  { key: 'encoder_feedback_support', displayName: 'Encoder Feedback', type: 'string', applicableTypes: ['motor'] },
  { key: 'poles', displayName: 'Poles', type: 'number', applicableTypes: ['motor'] },
  { key: 'peak_current', displayName: 'Peak Current', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'A', defaultVisible: false },
  { key: 'voltage_constant', displayName: 'Voltage Constant', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'V/krpm', defaultVisible: false },
  { key: 'torque_constant', displayName: 'Torque Constant', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'Nm/A', defaultVisible: false },
  { key: 'resistance', displayName: 'Resistance', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'Ω', defaultVisible: false },
  { key: 'inductance', displayName: 'Inductance', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'mH', defaultVisible: false },
  { key: 'ip_rating', displayName: 'IP Rating', type: 'number', applicableTypes: ['motor'] },
  { key: 'shaft_diameter', displayName: 'Shaft Diameter', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'mm', defaultVisible: false },
  { key: 'frame_size', displayName: 'Frame Size', type: 'string', applicableTypes: ['motor'] },
  { key: 'weight', displayName: 'Weight', type: 'object', applicableTypes: ['motor'], nested: true, unit: 'kg', defaultVisible: false },
];

/**
 * Get all filterable attributes for robot arms
 *
 * Returns robot arm-specific attributes with metadata for filtering/sorting.
 * Each attribute includes display name, data type, and unit of measurement.
 *
 * Categories:
 * - Identification: manufacturer, part_number, product_family
 * - Performance: payload, reach, degrees_of_freedom, pose_repeatability, max_tcp_speed
 * - Environmental: ip_rating, cleanroom_class, noise_level, mounting_position, operating_temp
 * - Materials & Safety: materials, safety_certifications
 * - Physical: weight
 *
 * @returns Array of robot arm attribute metadata objects
 */
// Robot arm default-visible set: payload, reach, DOF, pose repeatability,
// and max TCP speed are the core capability metrics every manufacturer
// leads with and every integrator cross-compares. Environmental details
// (noise, operating_temp) and safety metadata are hidden by default —
// reachable via restore. See specodex/models/robot_arm.md.
export const getRobotArmAttributes = (): AttributeMetadata[] => [
  { key: 'manufacturer', displayName: 'Manufacturer', type: 'string', applicableTypes: ['robot_arm'], defaultVisible: true },
  { key: 'part_number', displayName: 'Part Number', type: 'string', applicableTypes: ['robot_arm'] },
  { key: 'product_family', displayName: 'Product Family', type: 'string', applicableTypes: ['robot_arm'] },
  { key: 'payload', displayName: 'Payload', type: 'object', applicableTypes: ['robot_arm'], nested: true, unit: 'kg', defaultVisible: true },
  { key: 'reach', displayName: 'Reach', type: 'object', applicableTypes: ['robot_arm'], nested: true, unit: 'mm', defaultVisible: true },
  { key: 'degrees_of_freedom', displayName: 'Degrees of Freedom', type: 'number', applicableTypes: ['robot_arm'], defaultVisible: true },
  { key: 'pose_repeatability', displayName: 'Pose Repeatability', type: 'object', applicableTypes: ['robot_arm'], nested: true, unit: 'mm', defaultVisible: true },
  { key: 'max_tcp_speed', displayName: 'Max TCP Speed', type: 'object', applicableTypes: ['robot_arm'], nested: true, unit: 'm/s', defaultVisible: true },
  { key: 'ip_rating', displayName: 'IP Rating', type: 'number', applicableTypes: ['robot_arm'], defaultVisible: true },
  { key: 'cleanroom_class', displayName: 'Cleanroom Class', type: 'string', applicableTypes: ['robot_arm'] },
  { key: 'noise_level', displayName: 'Noise Level', type: 'object', applicableTypes: ['robot_arm'], nested: true, unit: 'dB(A)', defaultVisible: false },
  { key: 'mounting_position', displayName: 'Mounting Position', type: 'string', applicableTypes: ['robot_arm'] },
  { key: 'operating_temp', displayName: 'Operating Temperature', type: 'range', applicableTypes: ['robot_arm'], nested: true, unit: '°C', defaultVisible: false },
  { key: 'materials', displayName: 'Materials', type: 'array', applicableTypes: ['robot_arm'] },
  { key: 'safety_certifications', displayName: 'Safety Certifications', type: 'array', applicableTypes: ['robot_arm'] },
  { key: 'weight', displayName: 'Weight', type: 'object', applicableTypes: ['robot_arm'], nested: true, unit: 'kg', defaultVisible: false },
];

/**
 * Get all filterable attributes for gearheads
 *
 * Returns gearhead-specific attributes with metadata for filtering/sorting.
 * Each attribute includes display name, data type, and unit of measurement.
 *
 * Categories:
 * - Identification: manufacturer, part_number, frame_size
 * - Performance: gear_ratio, gear_type, stages, nominal_input_speed, max_input_speed
 * - Torque & Power: rated_torque, peak_torque, efficiency
 * - Mechanical: backlash, torsional_rigidity, rotor_inertia
 * - Shafts & Loads: input_shaft_diameter, output_shaft_diameter, max_radial_load, max_axial_load
 * - Environmental: ip_rating, operating_temp, noise_level, service_life
 * - Maintenance: lubrication_type
 * - Physical: weight
 *
 * @returns Array of gearhead attribute metadata objects
 */
// Gearhead default-visible set: ratio + torque + backlash + efficiency
// is the four-field summary every servo-system designer uses for
// selection. Installation-specific loads (radial/axial), shaft diameters,
// and lubrication/service-life marketing numbers are hidden by default.
// See specodex/models/gearhead.md.
export const getGearheadAttributes = (): AttributeMetadata[] => [
  { key: 'manufacturer', displayName: 'Manufacturer', type: 'string', applicableTypes: ['gearhead'], defaultVisible: true },
  { key: 'part_number', displayName: 'Part Number', type: 'string', applicableTypes: ['gearhead'] },
  { key: 'gear_ratio', displayName: 'Gear Ratio', type: 'number', applicableTypes: ['gearhead'], defaultVisible: true },
  { key: 'gear_type', displayName: 'Gear Type', type: 'string', applicableTypes: ['gearhead'], defaultVisible: true },
  { key: 'rated_torque', displayName: 'Rated Torque', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'Nm', defaultVisible: true },
  { key: 'peak_torque', displayName: 'Peak Torque', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'Nm', defaultVisible: true },
  { key: 'backlash', displayName: 'Backlash', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'arcmin', defaultVisible: true },
  { key: 'efficiency', displayName: 'Efficiency', type: 'number', applicableTypes: ['gearhead'], unit: '%', defaultVisible: true },
  { key: 'nominal_input_speed', displayName: 'Nominal Input Speed', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'rpm', defaultVisible: false },
  { key: 'max_input_speed', displayName: 'Max Input Speed', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'rpm', defaultVisible: false },
  { key: 'stages', displayName: 'Stages', type: 'number', applicableTypes: ['gearhead'] },
  { key: 'torsional_rigidity', displayName: 'Torsional Rigidity', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'Nm/arcmin', defaultVisible: false },
  { key: 'rotor_inertia', displayName: 'Rotor Inertia', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'kg·cm²', defaultVisible: false },
  { key: 'noise_level', displayName: 'Noise Level', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'dBA', defaultVisible: false },
  { key: 'frame_size', displayName: 'Frame Size', type: 'string', applicableTypes: ['gearhead'] },
  { key: 'input_shaft_diameter', displayName: 'Input Shaft Diameter', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'mm', defaultVisible: false },
  { key: 'output_shaft_diameter', displayName: 'Output Shaft Diameter', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'mm', defaultVisible: false },
  { key: 'max_radial_load', displayName: 'Max Radial Load', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'N', defaultVisible: false },
  { key: 'max_axial_load', displayName: 'Max Axial Load', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'N', defaultVisible: false },
  { key: 'ip_rating', displayName: 'IP Rating', type: 'number', applicableTypes: ['gearhead'] },
  { key: 'operating_temp', displayName: 'Operating Temperature', type: 'range', applicableTypes: ['gearhead'], nested: true, unit: '°C', defaultVisible: false },
  { key: 'service_life', displayName: 'Service Life', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'hours', defaultVisible: false },
  { key: 'lubrication_type', displayName: 'Lubrication Type', type: 'string', applicableTypes: ['gearhead'] },
  { key: 'weight', displayName: 'Weight', type: 'object', applicableTypes: ['gearhead'], nested: true, unit: 'kg', defaultVisible: false },
];

/**
 * Get all filterable attributes for datasheets
 *
 * Returns datasheet-specific attributes with metadata for filtering/sorting.
 *
 * Categories:
 * - Identification: manufacturer, product_name, product_family, product_type
 * - Content: url, pages
 *
 * @returns Array of datasheet attribute metadata objects
 */
export const getDatasheetAttributes = (): AttributeMetadata[] => [
  { key: 'manufacturer', displayName: 'Manufacturer', type: 'string', applicableTypes: ['datasheet'], defaultVisible: true },
  { key: 'part_number', displayName: 'Part Number', type: 'string', applicableTypes: ['datasheet'] },
  { key: 'product_name', displayName: 'Product Name', type: 'string', applicableTypes: ['datasheet'] },
  { key: 'product_family', displayName: 'Product Family', type: 'string', applicableTypes: ['datasheet'] },
  { key: 'component_type', displayName: 'Product Type', type: 'string', applicableTypes: ['datasheet'] },
];

// Contactor attributes — mirrors the generalized specodex/models/
// contactor.py schema (IEC 60947-4-1 vocabulary, not Mitsubishi-specific
// columns). Default-visible set surfaces the IEC headline scalars
// (ie_ac3_400v, motor_power_ac3_400v_kw, motor_power_ac3_480v_hp) plus
// thermal/insulation ratings every vendor publishes. List-of-ratings
// fields (ratings_ac3 etc.) aren't in the filter UI at all — they're
// the detail view you drill into per-product. See
// specodex/models/contactor.md.
export const getContactorAttributes = (): AttributeMetadata[] => [
  { key: 'manufacturer', displayName: 'Manufacturer', type: 'string', applicableTypes: ['contactor'], defaultVisible: true },
  { key: 'part_number', displayName: 'Part Number', type: 'string', applicableTypes: ['contactor'] },
  { key: 'type', displayName: 'Contactor Type', type: 'string', applicableTypes: ['contactor'] },
  { key: 'series', displayName: 'Series', type: 'string', applicableTypes: ['contactor'] },
  { key: 'vendor_frame_size', displayName: 'Vendor Frame Size', type: 'string', applicableTypes: ['contactor'] },
  { key: 'nema_size', displayName: 'NEMA Size', type: 'string', applicableTypes: ['contactor'] },
  // Headline ratings (what users sort by)
  { key: 'ie_ac3_400v', displayName: 'Ie AC-3 @ 400 V', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'A', defaultVisible: true },
  { key: 'motor_power_ac3_400v_kw', displayName: 'Motor Power AC-3 @ 400 V', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'kW', defaultVisible: true },
  { key: 'motor_power_ac3_480v_hp', displayName: 'Motor Power AC-3 @ 480 V', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'hp', defaultVisible: true },
  { key: 'conventional_thermal_current', displayName: 'Thermal Current (Ith)', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'A', defaultVisible: true },
  { key: 'rated_insulation_voltage', displayName: 'Rated Insulation Voltage', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'V', defaultVisible: true },
  { key: 'operating_temp', displayName: 'Operating Temperature', type: 'range', applicableTypes: ['contactor'], nested: true, unit: '°C', defaultVisible: true },
  { key: 'number_of_poles', displayName: 'Number of Poles', type: 'number', applicableTypes: ['contactor'], defaultVisible: true },
  // Insulation / voltage (detail)
  { key: 'rated_impulse_withstand_voltage', displayName: 'Rated Impulse Withstand Voltage', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'kV', defaultVisible: false },
  { key: 'rated_operational_voltage_max', displayName: 'Max Operational Voltage', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'V', defaultVisible: false },
  { key: 'rated_frequency', displayName: 'Rated Frequency', type: 'string', applicableTypes: ['contactor'] },
  { key: 'pollution_degree', displayName: 'Pollution Degree', type: 'number', applicableTypes: ['contactor'] },
  // Short-circuit
  { key: 'sccr', displayName: 'SCCR', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'kA', defaultVisible: false },
  // Coil (detail)
  { key: 'coil_voltage_range_ac', displayName: 'Coil Voltage Range (AC)', type: 'range', applicableTypes: ['contactor'], nested: true, unit: 'V', defaultVisible: false },
  { key: 'coil_voltage_range_dc', displayName: 'Coil Voltage Range (DC)', type: 'range', applicableTypes: ['contactor'], nested: true, unit: 'V', defaultVisible: false },
  { key: 'coil_voltage_options', displayName: 'Coil Voltage Options', type: 'array', applicableTypes: ['contactor'] },
  { key: 'coil_pickup_factor', displayName: 'Coil Pickup Factor', type: 'range', applicableTypes: ['contactor'], nested: true, unit: '×Uc', defaultVisible: false },
  { key: 'coil_dropout_factor', displayName: 'Coil Drop-out Factor', type: 'range', applicableTypes: ['contactor'], nested: true, unit: '×Uc', defaultVisible: false },
  { key: 'coil_time_constant', displayName: 'Coil Time Constant', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'ms', defaultVisible: false },
  { key: 'coil_power_consumption_sealed', displayName: 'Coil Power (Sealed)', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'W', defaultVisible: false },
  { key: 'coil_power_consumption_inrush', displayName: 'Coil Inrush', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'VA', defaultVisible: false },
  // Contacts / durability (detail)
  { key: 'auxiliary_contact_configuration', displayName: 'Auxiliary Contacts', type: 'string', applicableTypes: ['contactor'] },
  { key: 'mechanical_durability', displayName: 'Mechanical Durability', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'operations', defaultVisible: false },
  { key: 'electrical_durability_ac3', displayName: 'Electrical Durability (AC-3)', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'operations', defaultVisible: false },
  { key: 'operating_frequency_ac3', displayName: 'Operating Frequency (AC-3)', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'ops/h', defaultVisible: false },
  { key: 'making_capacity', displayName: 'Making Capacity', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'A', defaultVisible: false },
  { key: 'breaking_capacity', displayName: 'Breaking Capacity', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'A', defaultVisible: false },
  { key: 'operating_time_close', displayName: 'Operating Time (Close)', type: 'range', applicableTypes: ['contactor'], nested: true, unit: 'ms', defaultVisible: false },
  { key: 'operating_time_open', displayName: 'Operating Time (Open)', type: 'range', applicableTypes: ['contactor'], nested: true, unit: 'ms', defaultVisible: false },
  // Environmental / certifications
  { key: 'ip_rating', displayName: 'IP Rating', type: 'number', applicableTypes: ['contactor'] },
  { key: 'storage_temp', displayName: 'Storage Temperature', type: 'range', applicableTypes: ['contactor'], nested: true, unit: '°C', defaultVisible: false },
  { key: 'altitude_max', displayName: 'Max Altitude', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'm', defaultVisible: false },
  { key: 'standards_compliance', displayName: 'Standards', type: 'array', applicableTypes: ['contactor'] },
  { key: 'certifications', displayName: 'Certifications', type: 'array', applicableTypes: ['contactor'] },
  { key: 'mounting_types', displayName: 'Mounting', type: 'array', applicableTypes: ['contactor'] },
  { key: 'weight', displayName: 'Weight', type: 'object', applicableTypes: ['contactor'], nested: true, unit: 'kg', defaultVisible: false },
];

/**
 * Get all filterable attributes for drives
 *
 * Returns 23 drive-specific attributes with metadata for filtering/sorting.
 * Each attribute includes display name, data type, and unit of measurement.
 *
 * Categories:
 * - Identification: manufacturer, part_number, type, series
 * - Electrical: input_voltage, input_voltage_phases, rated_current, peak_current, rated_power
 * - I/O & Connectivity: ethernet_ports, digital_inputs, digital_outputs, analog_inputs, analog_outputs
 * - Communication: fieldbus, control_modes, encoder_feedback_support
 * - Safety & Ratings: safety_features, safety_rating, approvals, ip_rating
 * - Environmental: max_humidity, operating_temp
 * - Physical: weight
 *
 * @returns Array of 23 drive attribute metadata objects
 */
// Drive default-visible set: rated_power + input_voltage + currents
// + IP rating. I/O counts (digital_inputs/outputs, analog_*,
// ethernet_ports) are buried detail that nobody sorts the table by —
// they matter for a specific integration, not product discovery. Same
// logic for the safety/approvals arrays. See
// specodex/models/drive.md.
export const getDriveAttributes = (): AttributeMetadata[] => [
  { key: 'manufacturer', displayName: 'Manufacturer', type: 'string', applicableTypes: ['drive'], defaultVisible: true },
  { key: 'part_number', displayName: 'Part Number', type: 'string', applicableTypes: ['drive'] },
  { key: 'type', displayName: 'Drive Type', type: 'string', applicableTypes: ['drive'] },
  { key: 'series', displayName: 'Series', type: 'string', applicableTypes: ['drive'] },
  { key: 'rated_power', displayName: 'Rated Power', type: 'object', applicableTypes: ['drive'], nested: true, unit: 'W', defaultVisible: true },
  { key: 'input_voltage', displayName: 'Input Voltage', type: 'range', applicableTypes: ['drive'], nested: true, unit: 'V', defaultVisible: true },
  { key: 'input_voltage_phases', displayName: 'Input Voltage Phases', type: 'array', applicableTypes: ['drive'], defaultVisible: true },
  { key: 'rated_current', displayName: 'Rated Current', type: 'object', applicableTypes: ['drive'], nested: true, unit: 'A', defaultVisible: true },
  { key: 'peak_current', displayName: 'Peak Current', type: 'object', applicableTypes: ['drive'], nested: true, unit: 'A', defaultVisible: true },
  { key: 'ip_rating', displayName: 'IP Rating', type: 'number', applicableTypes: ['drive'], defaultVisible: true },
  { key: 'fieldbus', displayName: 'Fieldbus', type: 'array', applicableTypes: ['drive'] },
  { key: 'control_modes', displayName: 'Control Modes', type: 'array', applicableTypes: ['drive'] },
  { key: 'encoder_feedback_support', displayName: 'Encoder Feedback', type: 'array', applicableTypes: ['drive'] },
  { key: 'ethernet_ports', displayName: 'Ethernet Ports', type: 'number', applicableTypes: ['drive'] },
  { key: 'digital_inputs', displayName: 'Digital Inputs', type: 'number', applicableTypes: ['drive'] },
  { key: 'digital_outputs', displayName: 'Digital Outputs', type: 'number', applicableTypes: ['drive'] },
  { key: 'analog_inputs', displayName: 'Analog Inputs', type: 'number', applicableTypes: ['drive'] },
  { key: 'analog_outputs', displayName: 'Analog Outputs', type: 'number', applicableTypes: ['drive'] },
  { key: 'safety_features', displayName: 'Safety Features', type: 'array', applicableTypes: ['drive'] },
  { key: 'safety_rating', displayName: 'Safety Rating', type: 'array', applicableTypes: ['drive'] },
  { key: 'approvals', displayName: 'Approvals', type: 'array', applicableTypes: ['drive'] },
  { key: 'max_humidity', displayName: 'Max Humidity', type: 'number', applicableTypes: ['drive'], unit: '%' },
  { key: 'operating_temp', displayName: 'Operating Temperature', type: 'range', applicableTypes: ['drive'], nested: true, unit: '°C', defaultVisible: false },
  { key: 'weight', displayName: 'Weight', type: 'object', applicableTypes: ['drive'], nested: true, unit: 'kg', defaultVisible: false },
];

// Electric cylinder default-visible set: stroke + force + speed +
// voltage is the four-field comparison profile integrators start with.
// Secondary mechanical specs (lead screw, backlash, gear ratio) are
// hidden by default. See specodex/models/electric_cylinder.md.
export const getElectricCylinderAttributes = (): AttributeMetadata[] => [
  { key: 'manufacturer', displayName: 'Manufacturer', type: 'string', applicableTypes: ['electric_cylinder'], defaultVisible: true },
  { key: 'part_number', displayName: 'Part Number', type: 'string', applicableTypes: ['electric_cylinder'] },
  { key: 'type', displayName: 'Type', type: 'string', applicableTypes: ['electric_cylinder'] },
  { key: 'series', displayName: 'Series', type: 'string', applicableTypes: ['electric_cylinder'] },
  { key: 'stroke', displayName: 'Stroke', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'mm', defaultVisible: true },
  { key: 'max_push_force', displayName: 'Max Push Force', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'N', defaultVisible: true },
  { key: 'continuous_force', displayName: 'Continuous Force', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'N', defaultVisible: true },
  { key: 'max_linear_speed', displayName: 'Max Linear Speed', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'mm/s', defaultVisible: true },
  { key: 'rated_voltage', displayName: 'Rated Voltage', type: 'range', applicableTypes: ['electric_cylinder'], nested: true, unit: 'V', defaultVisible: true },
  { key: 'positioning_repeatability', displayName: 'Positioning Repeatability', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'mm', defaultVisible: true },
  { key: 'max_pull_force', displayName: 'Max Pull Force', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'N', defaultVisible: false },
  { key: 'linear_speed_at_rated_load', displayName: 'Linear Speed @ Rated Load', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'mm/s', defaultVisible: false },
  { key: 'rated_current', displayName: 'Rated Current', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'A', defaultVisible: false },
  { key: 'peak_current', displayName: 'Peak Current', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'A', defaultVisible: false },
  { key: 'rated_power', displayName: 'Rated Power', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'W', defaultVisible: false },
  { key: 'motor_type', displayName: 'Motor Type', type: 'string', applicableTypes: ['electric_cylinder'] },
  { key: 'lead_screw_pitch', displayName: 'Lead Screw Pitch', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'mm/rev', defaultVisible: false },
  { key: 'gear_ratio', displayName: 'Gear Ratio', type: 'number', applicableTypes: ['electric_cylinder'] },
  { key: 'backlash', displayName: 'Backlash', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'mm', defaultVisible: false },
  { key: 'max_radial_load', displayName: 'Max Radial Load', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'N', defaultVisible: false },
  { key: 'max_axial_load', displayName: 'Max Axial Load', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'N', defaultVisible: false },
  { key: 'encoder_feedback_support', displayName: 'Encoder Feedback', type: 'string', applicableTypes: ['electric_cylinder'] },
  { key: 'fieldbus', displayName: 'Fieldbus', type: 'string', applicableTypes: ['electric_cylinder'] },
  { key: 'ip_rating', displayName: 'IP Rating', type: 'number', applicableTypes: ['electric_cylinder'] },
  { key: 'operating_temp', displayName: 'Operating Temperature', type: 'range', applicableTypes: ['electric_cylinder'], nested: true, unit: '°C', defaultVisible: false },
  { key: 'service_life', displayName: 'Service Life', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'h', defaultVisible: false },
  { key: 'noise_level', displayName: 'Noise Level', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'dBA', defaultVisible: false },
  { key: 'weight', displayName: 'Weight', type: 'object', applicableTypes: ['electric_cylinder'], nested: true, unit: 'kg', defaultVisible: false },
];

/**
 * Get all attributes for a specific product type
 *
 * Smart attribute selection based on current product type:
 * - null: Returns empty array (no product type selected)
 * - 'motor': Returns 20 motor-specific attributes
 * - 'drive': Returns 23 drive-specific attributes
 * - 'robot_arm': Returns robot arm-specific attributes
 * - 'gearhead': Returns gearhead-specific attributes
 * - 'all': Returns intersection of all product type attributes (common attributes)
 *
 * The 'all' mode finds attributes that exist across multiple product types:
 * - manufacturer, part_number, weight, etc.
 *
 * This prevents showing irrelevant attributes when viewing mixed product types.
 *
 * Performance: O(n*m) where n=attrs1, m=attrs2
 * Cached by components, so performance impact is minimal.
 *
 * @param productType - Product type filter ('motor', 'drive', 'robot_arm', 'gearhead', 'datasheet', 'all', or null)
 * @returns Array of applicable attribute metadata
 */
export const getAttributesForType = (productType: ProductType): AttributeMetadata[] => {
  // Handle null productType (no selection)
  if (productType === null) return [];

  // Fast path: type-specific attributes
  if (productType === 'motor') return getMotorAttributes();
  if (productType === 'drive') return getDriveAttributes();
  if (productType === 'robot_arm') return getRobotArmAttributes();
  if (productType === 'gearhead') return getGearheadAttributes();
  if (productType === 'contactor') return getContactorAttributes();
  if ((productType as string) === 'electric_cylinder') return getElectricCylinderAttributes();
  if (productType === 'datasheet') return getDatasheetAttributes();

  // ===== COMPUTE COMMON ATTRIBUTES =====
  // For 'all' type, find intersection across all product types
  const motorAttrs = getMotorAttributes();
  const driveAttrs = getDriveAttributes();
  const robotArmAttrs = getRobotArmAttributes();
  const gearheadAttrs = getGearheadAttributes();
  const datasheetAttrs = getDatasheetAttributes();
  const commonKeys = new Set<string>();
  const commonAttrs: AttributeMetadata[] = [];

  // Find attributes that exist in multiple product type schemas
  // Start with motor attributes and check if they exist in other types
  motorAttrs.forEach(attr => {
    const inDrive = driveAttrs.some(d => d.key === attr.key);
    const inRobotArm = robotArmAttrs.some(r => r.key === attr.key);
    const inGearhead = gearheadAttrs.some(g => g.key === attr.key);
    const inDatasheet = datasheetAttrs.some(d => d.key === attr.key);

    // If attribute exists in at least 2 product types, include it
    const count = [inDrive, inRobotArm, inGearhead, inDatasheet].filter(Boolean).length + 1; // +1 for motor
    if (count >= 2 && !commonKeys.has(attr.key)) {
      commonKeys.add(attr.key);
      const applicableTypes: ('motor' | 'drive' | 'robot_arm' | 'gearhead' | 'datasheet')[] = ['motor'];
      if (inDrive) applicableTypes.push('drive');
      if (inRobotArm) applicableTypes.push('robot_arm');
      if (inGearhead) applicableTypes.push('gearhead');
      if (inDatasheet) applicableTypes.push('datasheet');

      commonAttrs.push({
        ...attr,
        applicableTypes
      });
    }
  });

  // Check drive attributes that weren't in motor
  driveAttrs.forEach(attr => {
    if (commonKeys.has(attr.key)) return;

    const inRobotArm = robotArmAttrs.some(r => r.key === attr.key);
    const inGearhead = gearheadAttrs.some(g => g.key === attr.key);
    const inDatasheet = datasheetAttrs.some(d => d.key === attr.key);

    const count = [inRobotArm, inGearhead, inDatasheet].filter(Boolean).length + 1; // +1 for drive
    if (count >= 2) {
      commonKeys.add(attr.key);
      const applicableTypes: ('motor' | 'drive' | 'robot_arm' | 'gearhead' | 'datasheet')[] = ['drive'];
      if (inRobotArm) applicableTypes.push('robot_arm');
      if (inGearhead) applicableTypes.push('gearhead');
      if (inDatasheet) applicableTypes.push('datasheet');

      commonAttrs.push({
        ...attr,
        applicableTypes
      });
    }
  });

  // Check robot_arm attributes that weren't in motor or drive
  robotArmAttrs.forEach(attr => {
    if (commonKeys.has(attr.key)) return;

    const inGearhead = gearheadAttrs.some(g => g.key === attr.key);
    const inDatasheet = datasheetAttrs.some(d => d.key === attr.key);

    if (inGearhead || inDatasheet) {
      commonKeys.add(attr.key);
      const applicableTypes: ('motor' | 'drive' | 'robot_arm' | 'gearhead' | 'datasheet')[] = ['robot_arm'];
      if (inGearhead) applicableTypes.push('gearhead');
      if (inDatasheet) applicableTypes.push('datasheet');
      
      commonAttrs.push({
        ...attr,
        applicableTypes
      });
    }
  });

  console.log(`[filters] Found ${commonAttrs.length} common attributes for 'all' type`);
  return commonAttrs;
};

/**
 * Build the seed FilterCriterion[] for a freshly-selected product type.
 *
 * Returns one chip per attribute marked `defaultFilter: true` in the type's
 * static metadata. Numerics land with operator `>` (lower-bound) — the value
 * is filled in by ProductList once products load, at the
 * DEFAULT_FILTER_FLOOR_PERCENTILE of the distribution. The slider opens at
 * the 10th percentile so the bottom decile (the smallest / weakest parts)
 * is excluded by default — the visible result set should exceed user
 * expectations rather than start at the floor of the catalog. Strings get
 * `=` and stay valueless.
 *
 * Curated, not auto: keep the per-type `defaultFilter` set tight (1-2 specs
 * the user almost certainly wants to filter on) so the sidebar opens with
 * intent, not noise.
 */
export const DEFAULT_FILTER_FLOOR_PERCENTILE = 0.10;

export const buildDefaultFiltersForType = (
  productType: ProductType,
): FilterCriterion[] => {
  const attrs = getAttributesForType(productType);
  return attrs
    .filter(a => a.defaultFilter)
    .map(a => {
      const wantsRange = a.nested || a.type === 'number' || a.type === 'range' || a.type === 'object';
      return {
        attribute: a.key,
        mode: 'include' as const,
        operator: (wantsRange ? '>=' : '=') as ComparisonOperator,
        displayName: a.displayName,
      };
    });
};

/**
 * Sister of buildDefaultFiltersForType — sort the table descending on every
 * default-filter attribute so users see the ceiling of their selection at
 * the top. With the slider seeded at P90 and operator `<`, the first row is
 * the part closest to (but still under) the user's threshold; the very top
 * of the catalog is right where the trim bites.
 */
export const buildDefaultSortsForType = (
  productType: ProductType,
): SortConfig[] => {
  const attrs = getAttributesForType(productType);
  return attrs
    .filter(a => a.defaultFilter)
    .map(a => ({
      attribute: a.key,
      direction: 'desc' as const,
      displayName: a.displayName,
    }));
};

// =====================================================================
// Dynamic attribute derivation
// =====================================================================
//
// The static per-type getXxxAttributes() lists above provide rich display
// names and units, but they drift every time a new product type lands —
// the contactor rollout had to patch four allowlists before filter chips
// showed up. `deriveAttributesFromRecords` walks the actual returned
// records, infers each field's kind + unit from the value shape, and
// produces a ready-to-render AttributeMetadata[]. `mergeAttributesByKey`
// combines static (rich) and derived (complete) lists, preferring static
// metadata when keys collide so existing UI doesn't regress.

/** Keys the filter UI should never offer (identity / bookkeeping). */
const DERIVATION_EXCLUDED_KEYS: ReadonlySet<string> = new Set([
  'PK',
  'SK',
  'product_id',
  'product_type',
  'datasheet_url',
  'pages',
  'msrp_source_url',
  'msrp_fetched_at',
]);

function toDisplayName(snake: string): string {
  return snake
    .split('_')
    .map(w => (w.length ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(' ');
}

function hasKey<K extends string>(obj: object, key: K): obj is Record<K, unknown> {
  return key in obj;
}

function isValueUnitShape(v: unknown): v is { value: unknown; unit: unknown } {
  return v !== null && typeof v === 'object' && hasKey(v, 'value') && hasKey(v, 'unit');
}

function isMinMaxUnitShape(v: unknown): v is { min: unknown; max: unknown; unit: unknown } {
  return (
    v !== null &&
    typeof v === 'object' &&
    hasKey(v, 'min') &&
    hasKey(v, 'max') &&
    hasKey(v, 'unit')
  );
}

function inferAttribute(
  key: string,
  value: unknown,
  productType: string,
): AttributeMetadata | null {
  if (value === null || value === undefined) return null;
  const displayName = toDisplayName(key);
  const applicableTypes = [productType];

  if (Array.isArray(value)) {
    return { key, displayName, type: 'array', applicableTypes };
  }
  // Order matters: check MinMaxUnit before ValueUnit because both share the
  // 'unit' key.
  if (isMinMaxUnitShape(value)) {
    const unit = typeof value.unit === 'string' ? value.unit : undefined;
    return { key, displayName, type: 'range', applicableTypes, nested: true, unit };
  }
  if (isValueUnitShape(value)) {
    const unit = typeof value.unit === 'string' ? value.unit : undefined;
    return { key, displayName, type: 'object', applicableTypes, nested: true, unit };
  }
  if (typeof value === 'string') {
    return { key, displayName, type: 'string', applicableTypes };
  }
  if (typeof value === 'number') {
    return { key, displayName, type: 'number', applicableTypes };
  }
  if (typeof value === 'boolean') {
    return { key, displayName, type: 'boolean', applicableTypes };
  }
  // Unknown shape — skip rather than emit a broken chip.
  return null;
}

/**
 * Walk `records` and produce an AttributeMetadata[] derived from the
 * keys and value shapes actually present. First-seen-value wins for each
 * key, so fields that first appear as `null`/`undefined` fall through to
 * later records that have a real value.
 */
export const deriveAttributesFromRecords = (
  records: readonly unknown[],
  productType: ProductType,
): AttributeMetadata[] => {
  if (!records.length || productType === null || productType === 'all') return [];

  const discovered = new Map<string, AttributeMetadata>();
  for (const record of records) {
    if (!record || typeof record !== 'object') continue;
    for (const [key, value] of Object.entries(record as Record<string, unknown>)) {
      if (DERIVATION_EXCLUDED_KEYS.has(key)) continue;
      if (discovered.has(key)) continue;
      const attr = inferAttribute(key, value, productType);
      if (attr) discovered.set(key, attr);
    }
  }
  return Array.from(discovered.values());
};

/**
 * Combine a static (rich metadata) list with a derived (complete coverage)
 * list. Entries from `primary` win on key collision, so existing per-type
 * display names and tuned units are preserved; entries unique to
 * `secondary` are appended so new fields from a schema evolution show up
 * without a code change.
 */
export const mergeAttributesByKey = (
  primary: AttributeMetadata[],
  secondary: AttributeMetadata[],
): AttributeMetadata[] => {
  const primaryKeys = new Set(primary.map(a => a.key));
  const extras = secondary.filter(a => !primaryKeys.has(a.key));
  return [...primary, ...extras];
};

// Authored column order for the results table lives in a sibling file
// so it's trivially findable: app/frontend/src/types/columnOrder.ts.
export { COLUMN_ORDER, orderColumnAttributes } from './columnOrder';

/**
 * Get available comparison operators for an attribute based on actual data values
 *
 * Analyzes the actual values found in products for the given attribute and
 * determines which comparison operators make sense based on the datatype.
 *
 * Rules:
 * - Numeric values (or extractable from objects): All operators (=, >, <, >=, <=, !=)
 * - String values: Equality only (=, !=)
 * - Boolean values: Equality only (=)
 * - Array values: Equality/contains (=, !=)
 * - Mixed types: Defaults to equality only (=, !=)
 *
 * @param products - Array of products to analyze
 * @param attribute - Attribute key (supports dot notation)
 * @returns Array of valid comparison operators for this attribute
 */
export const getAvailableOperators = (products: Product[], attribute: string): ComparisonOperator[] => {
  // If no products, default to all operators
  if (products.length === 0) {
    return ['=', '>', '<', '>=', '<=', '!='];
  }

  // Extract all non-null values for this attribute
  const values = products
    .map(product => getNestedValue(product, attribute))
    .filter(val => val !== null && val !== undefined);

  // If no values found, default to equality operators
  if (values.length === 0) {
    return ['=', '!='];
  }

  // Analyze value types
  let hasNumeric = false;
  let hasString = false;
  let hasBoolean = false;
  let hasArray = false;
  let hasObject = false;

  for (const value of values) {
    if (typeof value === 'number') {
      hasNumeric = true;
    } else if (typeof value === 'string') {
      hasString = true;
    } else if (typeof value === 'boolean') {
      hasBoolean = true;
    } else if (Array.isArray(value)) {
      hasArray = true;
    } else if (typeof value === 'object') {
      // Check if we can extract numeric value from object (ValueUnit, MinMaxUnit)
      const numVal = extractNumericValue(value);
      if (numVal !== null) {
        hasNumeric = true;
      } else {
        hasObject = true;
      }
    }
  }

  // Determine operators based on datatypes found
  // If all values are numeric or can be converted to numeric, allow all operators
  if (hasNumeric && !hasString && !hasBoolean && !hasArray && !hasObject) {
    return ['=', '>', '<', '>=', '<=', '!='];
  }

  // If only strings, allow equality operators
  if (hasString && !hasNumeric && !hasBoolean && !hasArray && !hasObject) {
    return ['=', '!='];
  }

  // If only booleans, allow equality only
  if (hasBoolean && !hasNumeric && !hasString && !hasArray && !hasObject) {
    return ['='];
  }

  // If only arrays, allow equality/contains
  if (hasArray && !hasNumeric && !hasString && !hasBoolean && !hasObject) {
    return ['=', '!='];
  }

  // Mixed types or complex objects: default to equality operators
  return ['=', '!='];
};

// ========== Filtering Functions ==========

/**
 * Apply filters to products (client-side filtering)
 *
 * Implements multi-criteria AND logic filtering:
 * - All 'include' filters must match
 * - No 'exclude' filters can match
 * - 'neutral' filters are ignored
 *
 * Supports:
 * - Nested object paths (e.g., 'rated_voltage.min')
 * - Numeric comparisons (=, >, <, >=, <=, !=)
 * - String matching (case-insensitive, partial match)
 * - Array contains checks (fieldbus, control_modes)
 * - ValueUnit and MinMaxUnit objects
 *
 * Performance: O(n * f) where n=products, f=filters
 * Typical: ~10-50ms for 1000 products with 5 filters
 *
 * Example:
 * ```typescript
 * applyFilters(products, [
 *   { attribute: 'manufacturer', mode: 'include', value: 'ACME', operator: '=', displayName: 'Manufacturer' },
 *   { attribute: 'rated_voltage.min', mode: 'include', value: 200, operator: '>=', displayName: 'Voltage' }
 * ])
 * // Returns only ACME products with voltage >= 200V
 * ```
 *
 * @param products - Array of products to filter
 * @param filters - Array of filter criteria (AND logic)
 * @returns Filtered array of products
 */
export const applyFilters = (products: Product[], filters: FilterCriterion[]): Product[] => {
  console.log(`[filters] Applying ${filters.length} filters to ${products.length} products`);

  const filtered = products.filter(product => {
    // ===== CHECK EACH FILTER =====
    // All filters must pass for product to be included
    for (const filter of filters) {
      // Skip neutral filters (temporarily disabled)
      if (filter.mode === 'neutral') continue;

      // Extract value using dot notation (e.g., 'rated_voltage.min')
      const value = getNestedValue(product, filter.attribute);

      // ===== HANDLE MISSING VALUES =====
      if (value === undefined || value === null) {
        // For 'include' mode: missing attribute = exclude product
        if (filter.mode === 'include') return false;
        // For 'exclude' mode: missing attribute = can't match = skip filter
        continue;
      }

      // ===== CHECK IF VALUE MATCHES FILTER =====
      const matches = matchesFilter(value, filter);

      // Apply filter logic
      if (filter.mode === 'include' && !matches) return false; // Must match
      if (filter.mode === 'exclude' && matches) return false;  // Must NOT match
    }

    // All filters passed
    return true;
  });

  console.log(`[filters] Filtered to ${filtered.length} products`);
  return filtered;
};

// ========== Sorting Functions ==========

/**
 * Sort products by multiple attributes with natural alphanumeric sorting
 *
 * Features:
 * - Multi-level sorting: Sort by attr1, then attr2, then attr3
 * - Natural alphanumeric ordering: "abc2" < "abc10" (not lexicographic)
 * - Null handling: null/undefined values always sort last
 * - Numeric extraction: Handles ValueUnit and MinMaxUnit objects
 * - Direction support: 'asc' or 'desc' for each level
 *
 * Sorting Algorithm:
 * 1. For each sort level (in order):
 *    a. Extract values from both products
 *    b. Handle null/undefined (null always last)
 *    c. Extract numbers from ValueUnit/MinMaxUnit if applicable
 *    d. Compare: numeric if both numbers, else natural alphanumeric
 *    e. Apply direction (asc/desc)
 *    f. If equal, continue to next sort level
 * 2. If all levels equal, maintain original order
 *
 * Performance: O(n log n * s) where n=products, s=sort levels
 * Typical: ~20-100ms for 1000 products with 3 sort levels
 *
 * Example:
 * ```typescript
 * sortProducts(products, [
 *   { attribute: 'manufacturer', direction: 'asc', displayName: 'Manufacturer' },
 *   { attribute: 'rated_power.value', direction: 'desc', displayName: 'Power' }
 * ])
 * // Groups by manufacturer A-Z, then by power high-to-low within each manufacturer
 * ```
 *
 * @param products - Array of products to sort
 * @param sort - Single sort config or array of sort configs (null = no sort)
 * @returns New sorted array (original array unchanged)
 */
export const sortProducts = (products: Product[], sort: SortConfig | SortConfig[] | null): Product[] => {
  if (!sort) return products;

  // Normalize to array for uniform handling
  const sorts = Array.isArray(sort) ? sort : [sort];
  if (sorts.length === 0) return products;

  console.log(`[filters] Sorting ${products.length} products by ${sorts.length} levels:`,
    sorts.map(s => `${s.displayName} (${s.direction})`).join(', '));

  // Create new array to avoid mutating original
  return [...products].sort((a, b) => {
    // ===== TRY EACH SORT LEVEL =====
    // Continue until we find a difference
    for (const sortConfig of sorts) {
      const aVal = getNestedValue(a, sortConfig.attribute);
      const bVal = getNestedValue(b, sortConfig.attribute);

      // ===== HANDLE NULL/UNDEFINED =====
      // Null values always sort last (regardless of direction)
      if (aVal === undefined || aVal === null) {
        if (bVal === undefined || bVal === null) continue; // Both null, try next sort
        return 1; // a is null, b is not → a goes after b
      }
      if (bVal === undefined || bVal === null) return -1; // b is null → a goes before b

      // ===== EXTRACT NUMERIC VALUES =====
      // Handle ValueUnit ({ value: 100, unit: 'V' }) and MinMaxUnit ({ min: 200, max: 240, unit: 'V' })
      const aNum = extractNumericValue(aVal);
      const bNum = extractNumericValue(bVal);

      let comparison = 0;

      // ===== COMPARE VALUES =====
      if (typeof aNum === 'number' && typeof bNum === 'number') {
        // Pure numeric comparison (fast)
        comparison = aNum - bNum;
      } else {
        // Natural alphanumeric sorting (handles "abc2" vs "abc10" correctly)
        comparison = naturalCompare(String(aVal), String(bVal));
      }

      // ===== APPLY DIRECTION =====
      comparison = sortConfig.direction === 'asc' ? comparison : -comparison;

      // If we found a difference, return it
      if (comparison !== 0) return comparison;

      // If equal, continue to next sort level
    }

    // All sort levels equal → maintain original order
    return 0;
  });
};

// ========== Helper Functions ==========

/**
 * Natural alphanumeric comparison
 *
 * Handles strings with embedded numbers intelligently:
 * - "abc2" < "abc10" (not "abc10" < "abc2" like lexicographic sort)
 * - "part1a" < "part2a"
 * - "v1.2.3" < "v1.10.0"
 *
 * Algorithm:
 * 1. Split strings into alternating number/non-number parts
 * 2. Compare each part pair:
 *    - If both are numbers: numeric comparison
 *    - Otherwise: case-insensitive string comparison
 * 3. Return first non-zero difference
 *
 * Performance: O(n) where n = average string length
 *
 * Examples:
 * - naturalCompare("abc2", "abc10") → -1 (abc2 < abc10)
 * - naturalCompare("ABC", "abc") → 0 (case-insensitive)
 * - naturalCompare("v1.9", "v1.10") → -1 (v1.9 < v1.10)
 *
 * @param a - First string
 * @param b - Second string
 * @returns Negative if a < b, positive if a > b, zero if equal
 */
const naturalCompare = (a: string, b: string): number => {
  // Split strings into parts: digits or non-digits
  // Example: "abc123def456" → ["abc", "123", "def", "456"]
  const aParts = a.match(/(\d+|\D+)/g) || [];
  const bParts = b.match(/(\d+|\D+)/g) || [];

  // Compare part by part
  for (let i = 0; i < Math.max(aParts.length, bParts.length); i++) {
    const aPart = aParts[i] || '';
    const bPart = bParts[i] || '';

    // Check if both parts are pure numbers
    const aIsNum = /^\d+$/.test(aPart);
    const bIsNum = /^\d+$/.test(bPart);

    if (aIsNum && bIsNum) {
      // ===== NUMERIC COMPARISON =====
      const diff = parseInt(aPart, 10) - parseInt(bPart, 10);
      if (diff !== 0) return diff;
    } else {
      // ===== STRING COMPARISON =====
      // Case-insensitive for better UX
      const diff = aPart.toLowerCase().localeCompare(bPart.toLowerCase());
      if (diff !== 0) return diff;
    }
  }

  // All parts equal
  return 0;
};

/**
 * Get nested value from object using dot notation
 *
 * Supports deep property access with dot-separated paths.
 * Safely handles undefined/null values in the path.
 *
 * Examples:
 * - getNestedValue(product, 'manufacturer') → product.manufacturer
 * - getNestedValue(product, 'rated_voltage.min') → product.rated_voltage.min
 * - getNestedValue(product, 'dimensions.length.value') → product.dimensions.length.value
 *
 * @param obj - Object to extract value from
 * @param path - Dot-separated path (e.g., 'rated_voltage.min')
 * @returns Value at path, or undefined if path doesn't exist
 */
const getNestedValue = (obj: any, path: string): any => {
  const keys = path.split('.');
  let value = obj;

  // Traverse the path
  for (const key of keys) {
    if (value === undefined || value === null) return undefined;
    value = value[key];
  }

  return value;
};

/**
 * Extract numeric value from ValueUnit or MinMaxUnit objects
 *
 * Handles different data structures:
 * - number: Return as-is
 * - ValueUnit: { value: 100, unit: 'V' } → 100
 * - MinMaxUnit: { min: 200, max: 240, unit: 'V' } → 220 (average)
 * - other: null
 *
 * Used for numeric sorting and filtering.
 *
 * @param value - Value to extract number from
 * @returns Numeric value or null if not extractable
 */
const extractNumericValue = (value: any): number | null => {
  // Already a number
  if (typeof value === 'number') return value;

  // Object types
  if (typeof value === 'object' && value !== null) {
    // ValueUnit: { value: number, unit: string }
    if ('value' in value) return value.value;

    // MinMaxUnit: { min: number, max: number, unit: string }
    // Use average for sorting/filtering
    if ('min' in value && 'max' in value) {
      return (value.min + value.max) / 2;
    }
  }

  return null;
};

/**
 * Check if a value matches a filter criterion
 *
 * Implements comprehensive matching logic for all data types:
 *
 * 1. Existence check: If no filter value, just check attribute exists
 * 2. Array matching: Check if any array element contains filter value (case-insensitive)
 * 3. ValueUnit matching: Extract value and apply numeric/string comparison
 * 4. MinMaxUnit matching: Use average value for numeric comparison
 * 5. String matching: Case-insensitive partial match (contains)
 * 6. Number matching: Use comparison operators (=, >, <, >=, <=, !=)
 * 7. Fallback: Convert to string and check contains
 *
 * Examples:
 * - matchesFilter(['EtherCAT', 'CANopen'], { value: 'ether' }) → true
 * - matchesFilter({ value: 240, unit: 'V' }, { value: 200, operator: '>' }) → true
 * - matchesFilter('ACME Motors', { value: 'acme' }) → true
 *
 * @param value - Product attribute value to check
 * @param filter - Filter criterion to match against
 * @returns True if value matches filter, false otherwise
 */
const matchesFilter = (value: any, filter: FilterCriterion): boolean => {
  // ===== EXISTENCE CHECK =====
  if (filter.value === undefined) {
    // Just checking if attribute exists (no value specified)
    return value !== undefined && value !== null;
  }

  // ===== MULTI-SELECT STRING MATCHING =====
  // When filter has multiple values (string[]), match if product value matches ANY of them (OR logic)
  if (Array.isArray(filter.value)) {
    const filterValues = filter.value; // Extract to help TypeScript
    // Handle case where product value is also an array
    if (Array.isArray(value)) {
      return value.some(v =>
        filterValues.some((fv: any) =>
          String(v).toLowerCase().includes(String(fv).toLowerCase())
        )
      );
    }
    // Product value is a single value, check if it matches any filter value
    const valueStr = String(value).toLowerCase();
    return filterValues.some((fv: any) => valueStr.includes(String(fv).toLowerCase()));
  }

  // ===== ARRAY MATCHING =====
  // For arrays (fieldbus, control_modes, safety_features, etc.)
  // Check if ANY element contains the filter value
  if (Array.isArray(value)) {
    return value.some(v =>
      String(v).toLowerCase().includes(String(filter.value).toLowerCase())
    );
  }

  // ===== OBJECT MATCHING =====
  if (typeof value === 'object' && value !== null) {
    // ValueUnit: { value: number, unit: string }
    if ('value' in value) {
      const numValue = value.value;
      if (typeof filter.value === 'number' && typeof numValue === 'number') {
        // Numeric comparison with operator
        return compareNumbers(numValue, filter.operator || '=', filter.value);
      }
      // String matching on value
      return String(numValue).toLowerCase().includes(String(filter.value).toLowerCase());
    }

    // MinMaxUnit: { min: number, max: number, unit: string }
    // Strict-bound matching — every value in the row's range must satisfy
    // the threshold:
    //   >= / > : compare against MIN (the bottom of the range clears the bar)
    //   <= / < : compare against MAX (the top of the range stays under)
    //   = / != : midpoint (representative point comparison)
    // This is stricter than midpoint-only matching: a row whose displayed
    // range straddles the threshold is excluded, so no cell ever shows a
    // bound that contradicts the filter direction. Slider thumb and sort
    // key still use midpoint, so the slider scale is unchanged.
    if ('min' in value && 'max' in value) {
      if (typeof filter.value === 'number') {
        const op = filter.operator || '=';
        let representative: number;
        if (op === '>=' || op === '>') {
          representative = value.min;
        } else if (op === '<=' || op === '<') {
          representative = value.max;
        } else {
          representative = (value.min + value.max) / 2;
        }
        return compareNumbers(representative, op, filter.value);
      }
    }
  }

  // ===== STRING MATCHING =====
  // Case-insensitive partial match (contains)
  if (typeof value === 'string') {
    return value.toLowerCase().includes(String(filter.value).toLowerCase());
  }

  // ===== NUMBER MATCHING =====
  // Use comparison operators (=, >, <, >=, <=, !=)
  if (typeof value === 'number' && typeof filter.value === 'number') {
    return compareNumbers(value, filter.operator || '=', filter.value);
  }

  // ===== FALLBACK =====
  // Convert to string and check contains (case-insensitive)
  return String(value).toLowerCase().includes(String(filter.value).toLowerCase());
};

/**
 * Compare two numbers using a comparison operator
 *
 * Supports all standard comparison operators:
 * - '=': Equal to (exact match)
 * - '>': Greater than
 * - '<': Less than
 * - '>=': Greater than or equal to
 * - '<=': Less than or equal to
 * - '!=': Not equal to
 *
 * Used by matchesFilter for numeric comparisons.
 *
 * Examples:
 * - compareNumbers(240, '>', 200) → true
 * - compareNumbers(100, '<=', 100) → true
 * - compareNumbers(50, '!=', 60) → true
 *
 * @param value - Actual numeric value
 * @param operator - Comparison operator
 * @param target - Target value to compare against
 * @returns True if comparison is satisfied, false otherwise
 */
const compareNumbers = (value: number, operator: ComparisonOperator, target: number): boolean => {
  switch (operator) {
    case '=':
      return value === target;
    case '>':
      return value > target;
    case '<':
      return value < target;
    case '>=':
      return value >= target;
    case '<=':
      return value <= target;
    case '!=':
      return value !== target;
    default:
      // Fallback to equality
      return value === target;
  }
};
