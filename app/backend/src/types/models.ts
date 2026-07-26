/**
 * TypeScript types for the application data models.
 */

export interface ValueUnit {
  value: number;
  unit: string;
}

export interface MinMaxUnit {
  min: number;
  max: number;
  unit: string;
}

export interface Datasheet {
  datasheet_id?: string;
  url: string;
  pages?: number[];
  product_type: string;
  product_name: string;
  product_family?: string;
  category?: string;
  manufacturer: string;
  release_year?: number;
  warranty?: ValueUnit;
  PK?: string;
  SK?: string;
  last_scraped?: string;
}

export interface Dimensions {
  length?: ValueUnit;
  width?: ValueUnit;
  height?: ValueUnit;
}

/**
 * Base interface for all products
 */
export interface ProductBase {
  product_id: string;
  product_type: string;
  product_family?: string;
  component_type?: string;
  manufacturer: string;
  product_name?: string;
  part_number?: string;
  dimensions?: Dimensions;
  weight?: ValueUnit;
  // Expected delivery / lead time. Mirrors specodex/models/product.py
  // ProductBase.lead_time — typically a ValueUnit with unit='days'.
  lead_time?: ValueUnit;
  datasheet_url?: string;
  pages?: number[];
  PK: string;
  SK: string;
}

/**
 * Motor types
 */
export type MotorType =
  | 'brushless dc'
  | 'brushed dc'
  | 'ac induction'
  | 'ac synchronous'
  | 'ac servo'
  | 'permanent magnet'
  | 'hybrid';

/**
 * Mounting-standard designator for a dimensional-drawing-sourced flange.
 * Mirrors specodex/models/common.py MountingStandard.
 */
export type MountingStandard = 'NEMA' | 'IEC' | 'JIS' | 'DIN' | 'proprietary';

/**
 * Enumerated motor-shaft end modification. Mirrors
 * specodex/models/common.py ShaftModification.
 */
export type ShaftModification =
  | 'none'
  | 'keyway'
  | 'double_keyway'
  | 'flat'
  | 'double_flat'
  | 'splined'
  | 'tapped_hole'
  | 'tapered'
  | 'other';

/**
 * Motor-mount flange geometry (bolt pattern + pilot/register fit), read off
 * a dimensional drawing. Shared shape between Motor.mounting_flange and
 * Gearhead.input_mounting_flange. Mirrors specodex/models/common.py
 * FlangeMountingDimensions.
 */
export interface FlangeMountingDimensions {
  bolt_circle_diameter?: ValueUnit;
  bolt_hole_diameter?: ValueUnit;
  bolt_hole_count?: number;
  pilot_diameter?: ValueUnit;
  pilot_depth?: ValueUnit;
  mounting_standard?: MountingStandard;
}

/**
 * Motor model matching specodex/models/motor.py
 */
export interface Motor extends ProductBase {
  product_type: 'motor';
  type?: MotorType;
  series?: string;
  rated_voltage?: MinMaxUnit;
  rated_speed?: ValueUnit;
  rated_torque?: ValueUnit;
  peak_torque?: ValueUnit;
  rated_power?: ValueUnit;
  encoder_feedback_support?: string;
  poles?: number;
  rated_current?: ValueUnit;
  peak_current?: ValueUnit;
  voltage_constant?: ValueUnit;
  torque_constant?: ValueUnit;
  resistance?: ValueUnit;
  inductance?: ValueUnit;
  ip_rating?: number;
  rotor_inertia?: ValueUnit;
  shaft_diameter?: ValueUnit;
  frame_size?: string;
  // Dimensional-drawing fields — see specodex/mounting/extract.py and
  // relations.py mounting_conflicts().
  shaft_length?: ValueUnit;
  shaft_modification?: ShaftModification[];
  mounting_flange?: FlangeMountingDimensions;
}

/**
 * Drive types
 */
export type DriveType = 'servo' | 'variable frequency';

/**
 * Ethernet-based industrial fieldbus protocols supported on drives.
 */
export type CommunicationProtocol =
  | 'EtherCAT'
  | 'EtherNet/IP'
  | 'PROFINET'
  | 'Modbus TCP'
  | 'POWERLINK'
  | 'Sercos III'
  | 'CC-Link IE';

/**
 * Drive model matching specodex/models/drive.py
 */
export interface Drive extends ProductBase {
  product_type: 'drive';
  type?: DriveType;
  series?: string;
  input_voltage?: MinMaxUnit;
  input_voltage_frequency?: ValueUnit[];
  input_voltage_phases?: number[];
  rated_current?: ValueUnit;
  peak_current?: ValueUnit;
  rated_power?: ValueUnit;
  switching_frequency?: ValueUnit[];
  fieldbus?: CommunicationProtocol[];
  control_modes?: string[];
  encoder_feedback_support?: string[];
  ethernet_ports?: number;
  digital_inputs?: number;
  digital_outputs?: number;
  analog_inputs?: number;
  analog_outputs?: number;
  safety_features?: string[];
  safety_rating?: string[];
  approvals?: string[];
  max_humidity?: number;
  ip_rating?: number;
  operating_temp?: MinMaxUnit;
}

/**
 * Gearhead model matching specodex/models/gearhead.py
 */
export interface Gearhead extends ProductBase {
  product_type: 'gearhead';
  gear_ratio?: number;
  gear_type?: string;
  stages?: number;
  nominal_input_speed?: ValueUnit;
  max_input_speed?: ValueUnit;
  rated_torque?: ValueUnit;
  peak_torque?: ValueUnit;
  backlash?: ValueUnit;
  efficiency?: number;
  torsional_rigidity?: ValueUnit;
  rotor_inertia?: ValueUnit;
  noise_level?: ValueUnit;
  frame_size?: string;
  input_shaft_diameter?: ValueUnit;
  output_shaft_diameter?: ValueUnit;
  max_radial_load?: ValueUnit;
  max_axial_load?: ValueUnit;
  ip_rating?: number;
  operating_temp?: MinMaxUnit;
  service_life?: ValueUnit;
  lubrication_type?: string;
  // Input-flange dimensional-drawing geometry — see
  // specodex/mounting/extract.py and relations.py mounting_conflicts().
  input_mounting_flange?: FlangeMountingDimensions;
}

/**
 * Robot Arm model matching specodex/models/robot_arm.py
 */
export interface RobotArm extends ProductBase {
  product_type: 'robot_arm';
  payload?: ValueUnit;
  reach?: ValueUnit;
  degrees_of_freedom?: number;
  pose_repeatability?: ValueUnit;
  max_tcp_speed?: ValueUnit;
  ip_rating?: number;
  cleanroom_class?: string;
  noise_level?: ValueUnit;
  mounting_position?: string;
  operating_temp?: MinMaxUnit;
  materials?: string[];
  safety_certifications?: string[];
}

/**
 * Contactor subtypes
 */
export type ContactorType =
  | 'ac operated'
  | 'dc operated'
  | 'mechanically latched'
  | 'reversing'
  | 'delay open'
  | 'solid state'
  | 'vacuum'
  | 'definite purpose';

export interface ContactorPowerRating {
  voltage?: ValueUnit;
  voltage_group?: string;
  current?: ValueUnit;
  power_kw?: ValueUnit;
  power_hp?: ValueUnit;
  ambient_temp?: ValueUnit;
}

export interface ContactorIcwRating {
  duration: ValueUnit;
  current: ValueUnit;
}

/**
 * Contactor model matching specodex/models/contactor.py
 */
export interface Contactor extends ProductBase {
  product_type: 'contactor';
  type?: ContactorType;
  series?: string;

  vendor_frame_size?: string;
  nema_size?: string;

  rated_insulation_voltage?: ValueUnit;
  rated_impulse_withstand_voltage?: ValueUnit;
  rated_operational_voltage_max?: ValueUnit;
  rated_frequency?: string;
  pollution_degree?: number;

  ie_ac3_400v?: ValueUnit;
  motor_power_ac3_400v_kw?: ValueUnit;
  motor_power_ac3_480v_hp?: ValueUnit;

  ratings_ac3?: ContactorPowerRating[];
  ratings_ac1?: ContactorPowerRating[];
  ratings_ac4?: ContactorPowerRating[];

  conventional_thermal_current?: ValueUnit;
  short_circuit_withstand_icw?: ContactorIcwRating[];
  sccr?: ValueUnit;

  coil_voltage_range_ac?: MinMaxUnit;
  coil_voltage_range_dc?: MinMaxUnit;
  coil_voltage_options?: string[];
  coil_pickup_factor?: MinMaxUnit;
  coil_dropout_factor?: MinMaxUnit;
  coil_time_constant?: ValueUnit;
  coil_power_consumption_sealed?: ValueUnit;
  coil_power_consumption_inrush?: ValueUnit;

  number_of_poles?: number;
  auxiliary_contact_configuration?: string;

  mechanical_durability?: ValueUnit;
  electrical_durability_ac3?: ValueUnit;
  operating_frequency_ac3?: ValueUnit;
  making_capacity?: ValueUnit;
  breaking_capacity?: ValueUnit;
  operating_time_close?: MinMaxUnit;
  operating_time_open?: MinMaxUnit;

  operating_temp?: MinMaxUnit;
  storage_temp?: MinMaxUnit;
  ip_rating?: number;
  altitude_max?: ValueUnit;

  standards_compliance?: string[];
  certifications?: string[];
  mounting_types?: string[];
}

/**
 * ElectricCylinder model matching specodex/models/electric_cylinder.py
 */
export interface ElectricCylinder extends ProductBase {
  product_type: 'electric_cylinder';
  type?: 'linear actuator' | 'linear servo' | 'micro linear actuator' | 'tubular linear motor';
  series?: string;
  stroke?: ValueUnit;
  max_push_force?: ValueUnit;
  max_pull_force?: ValueUnit;
  continuous_force?: ValueUnit;
  max_linear_speed?: ValueUnit;
  linear_speed_at_rated_load?: ValueUnit;
  positioning_repeatability?: ValueUnit;
  rated_voltage?: MinMaxUnit;
  rated_current?: ValueUnit;
  peak_current?: ValueUnit;
  rated_power?: ValueUnit;
  motor_type?: string;
  lead_screw_pitch?: ValueUnit;
  gear_ratio?: number;
  backlash?: ValueUnit;
  max_radial_load?: ValueUnit;
  max_axial_load?: ValueUnit;
  encoder_feedback_support?: string;
  fieldbus?: string;
  ip_rating?: number;
  operating_temp?: MinMaxUnit;
  service_life?: ValueUnit;
  noise_level?: ValueUnit;
}

/**
 * LinearActuator model matching specodex/models/linear_actuator.py
 *
 * Rodless linear-motion modules — carriage rides on a guided rail.
 * Distinct from ElectricCylinder (which pushes/pulls from a rod).
 */
export interface LinearActuator extends ProductBase {
  product_type: 'linear_actuator';
  type?: 'linear_slide' | 'linear_stage' | 'rodless_screw' | 'rodless_belt' | 'lm_guide_actuator';
  series?: string;
  stroke?: ValueUnit;
  max_work_load?: ValueUnit;
  max_push_force?: ValueUnit;
  holding_force?: ValueUnit;
  dynamic_load_rating?: ValueUnit;
  static_load_rating?: ValueUnit;
  max_linear_speed?: ValueUnit;
  max_acceleration?: ValueUnit;
  positioning_repeatability?: ValueUnit;
  backlash?: ValueUnit;
  actuation_mechanism?: 'ball_screw' | 'lead_screw' | 'belt' | 'linear_motor';
  lead_screw_pitch?: ValueUnit;
  screw_diameter?: ValueUnit;
  static_allowable_moment_pitching?: ValueUnit;
  static_allowable_moment_yawing?: ValueUnit;
  static_allowable_moment_rolling?: ValueUnit;
  rotor_inertia?: ValueUnit;
  motor_type?: 'step_motor' | 'servo_motor' | 'motorless';
  encoder_feedback_support?: string[];
  rated_voltage?: MinMaxUnit;
  rated_current?: ValueUnit;
  peak_current?: ValueUnit;
  rated_power?: ValueUnit;
  ip_rating?: number;
  operating_temp?: MinMaxUnit;
  operating_humidity_range?: ValueUnit;
  cleanroom_class?: string;
}

/**
 * Union type for all products
 */
export type Product = Motor | Drive | Gearhead | RobotArm | Contactor | ElectricCylinder | LinearActuator | Datasheet;
export type ProductType = 'motor' | 'drive' | 'gearhead' | 'robot_arm' | 'contactor' | 'electric_cylinder' | 'linear_actuator' | 'datasheet' | 'all';

/**
 * Manufacturer record — first-class entity in the single-table design.
 * PK = "MANUFACTURER", SK = `MANUFACTURER#{id}`. Mirrors
 * specodex/models/manufacturer.py.
 */
export interface Manufacturer {
  id: string;
  name: string;
  website?: string;
  offered_product_types?: string[];
  PK?: string;
  SK?: string;
}

/**
 * Reference to a product inside a Project. Stored as a tuple, not a
 * snapshot — reads dereference against the live products table so a
 * later edit to the product propagates without a backfill. Same shape
 * the rest of the app uses to key products.
 */
export interface ProductRef {
  product_type: string;
  product_id: string;
}

/**
 * Project — a user-owned, named collection of product refs.
 * PK = `USER#{owner_sub}`, SK = `PROJECT#{id}`. Per-user partition
 * scopes list queries cleanly without a GSI.
 */
export interface Project {
  id: string;
  name: string;
  owner_sub: string;
  product_refs: ProductRef[];
  created_at: string;
  updated_at: string;
  PK?: string;
  SK?: string;
}
