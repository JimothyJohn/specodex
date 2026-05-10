/* eslint-disable */
/**
 * AUTO-GENERATED — do not edit by hand.
 * Regenerate with: ./Quickstart gen-types
 * Source: specodex/models/*.py (Pydantic BaseModel subclasses)
 * Plan:   todo/PYTHON_BACKEND.md
 */

/* tslint:disable */
/* eslint-disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

/**
 * Electromagnetic contactor (magnetic contactor) or solid-state
 * contactor — an electromechanical or semiconductor switching device
 * used to control power circuits for motors, heaters, lighting, and
 * capacitor loads. Modelled after the IEC 60947-4-1 utilization
 * categories (AC-1 through AC-4, DC-1, DC-13) with parallel UL 508
 * fields (hp ratings, SCCR) for the North-American market.
 *
 * Covers AC-operated, DC-operated, mechanically-latched, reversing,
 * delay-open, and solid-state variants.
 */
export interface Contactor {
  /**
   * Unique identifier (auto-generated)
   */
  product_id?: string;
  product_type?: "contactor";
  /**
   * Product name
   */
  product_name: string;
  /**
   * Product family or sub-series
   */
  product_family?: string | null;
  /**
   * Part number
   */
  part_number?: string | null;
  /**
   * Manufacturer name
   */
  manufacturer: string;
  release_year?: number | null;
  dimensions?: Dimensions | null;
  weight?: ValueUnit | null;
  msrp?: ValueUnit | null;
  /**
   * URL the MSRP was scraped from. Populated by price-enrich.
   */
  msrp_source_url?: string | null;
  /**
   * ISO 8601 timestamp when MSRP was last fetched.
   */
  msrp_fetched_at?: string | null;
  warranty?: ValueUnit | null;
  /**
   * Expected delivery / lead time for the product. Typically a ValueUnit with unit='days' (e.g. {'value': 30, 'unit': 'days'}). Sourced from manufacturer or distributor data, not the datasheet.
   */
  lead_time?: ValueUnit | null;
  /**
   * URL of the source datasheet
   */
  datasheet_url?: string | null;
  /**
   * 1-indexed PDF pages where this product's specs were found. Used for #page=N deep-linking.
   */
  pages?: number[] | null;
  type?:
    | (
        | "ac operated"
        | "dc operated"
        | "mechanically latched"
        | "reversing"
        | "delay open"
        | "solid state"
        | "vacuum"
        | "definite purpose"
      )
    | null;
  series?: string | null;
  /**
   * Vendor-specific frame/size code as printed on the datasheet (e.g. ABB 'AF09', Siemens 'S00', Fuji 'SC-N1', Mitsubishi 'T10'). Not portable across vendors; kept only for cross-referencing a specific datasheet.
   */
  vendor_frame_size?: string | null;
  /**
   * NEMA size designation ('00', '0', '1', ..., '9'). Only US-market datasheets (ABB North America, Allen-Bradley) publish this.
   */
  nema_size?: string | null;
  /**
   * Rated insulation voltage Ui (V). Typically 690 V IEC / 600 V UL.
   */
  rated_insulation_voltage?: ValueUnit | null;
  /**
   * Rated impulse withstand voltage Uimp (kV). Typically 6 kV.
   */
  rated_impulse_withstand_voltage?: ValueUnit | null;
  /**
   * Maximum rated operational voltage Ue (V). Distinct from Ui; defines the upper bound of the utilization-category ratings.
   */
  rated_operational_voltage_max?: ValueUnit | null;
  /**
   * Line frequency of the main circuit (e.g. '50/60 Hz').
   */
  rated_frequency?: string | null;
  /**
   * Pollution degree per IEC 60947-1 (typically 3).
   */
  pollution_degree?: number | null;
  /**
   * Rated operational current at AC-3, Ue = 400 V (A). IEC headline figure — use this for filtering / comparing.
   */
  ie_ac3_400v?: ValueUnit | null;
  /**
   * Rated motor power at AC-3, Ue = 400 V (kW). Appears in the product name on most Siemens/Schneider SKUs.
   */
  motor_power_ac3_400v_kw?: ValueUnit | null;
  /**
   * Rated motor power at AC-3, Ue = 480 V (hp). NEMA/UL headline figure for the US market.
   */
  motor_power_ac3_480v_hp?: ValueUnit | null;
  /**
   * Per-voltage AC-3 rating table (squirrel-cage motor, starting). Captures the full voltage/current/power matrix the datasheet publishes.
   */
  ratings_ac3?: ContactorPowerRating[] | null;
  /**
   * Per-voltage AC-1 rating table (non-inductive or resistive heating loads). Rows may also differ by ambient temperature.
   */
  ratings_ac1?: ContactorPowerRating[] | null;
  /**
   * Per-voltage AC-4 rating table (inching/plugging duty). Many vendors publish only life curves for AC-4 and omit the table.
   */
  ratings_ac4?: ContactorPowerRating[] | null;
  /**
   * Conventional free-air thermal current Ith (A) — continuous current the main contacts carry without exceeding rated temperature rise.
   */
  conventional_thermal_current?: ValueUnit | null;
  /**
   * Short-time withstand current curve (duration/current pairs). ABB/Allen-Bradley publish 4+ points; capture the full curve.
   */
  short_circuit_withstand_icw?: ContactorIcwRating[] | null;
  /**
   * UL short-circuit current rating (kA). North-American metric distinct from Icw; single scalar.
   */
  sccr?: ValueUnit | null;
  /**
   * AC coil voltage range offered across the SKU family (e.g. 24–500 V AC). Individual SKUs will have a single Uc.
   */
  coil_voltage_range_ac?: MinMaxUnit | null;
  /**
   * DC coil voltage range offered across the SKU family (V).
   */
  coil_voltage_range_dc?: MinMaxUnit | null;
  /**
   * Explicit coil voltage designations offered (e.g. '24V AC', '230V AC', '24V DC'). Populated when the datasheet lists discrete options rather than a continuous range.
   */
  coil_voltage_options?: string[] | null;
  /**
   * Operating range for reliable pickup as a fraction of Uc (e.g. 0.85–1.1 ×Uc). Unitless ratio — use unit='×Uc'.
   */
  coil_pickup_factor?: MinMaxUnit | null;
  /**
   * Drop-out voltage as a fraction of Uc (e.g. 0.2–0.75 ×Uc). Unitless ratio.
   */
  coil_dropout_factor?: MinMaxUnit | null;
  /**
   * DC coil time constant (ms). Schneider TeSys D publishes this for DC coils; rarely meaningful for AC.
   */
  coil_time_constant?: ValueUnit | null;
  /**
   * Sealed (energized steady-state) coil consumption. W for DC, VA for AC.
   */
  coil_power_consumption_sealed?: ValueUnit | null;
  /**
   * Inrush coil consumption at pickup. VA for AC, W for DC.
   */
  coil_power_consumption_inrush?: ValueUnit | null;
  /**
   * Number of main-circuit poles (typically 3 or 4).
   */
  number_of_poles?: number | null;
  /**
   * Aux contact arrangement, e.g. '1NO', '1NO+1NC', '2a2b'. Vendor notation varies; store as printed.
   */
  auxiliary_contact_configuration?: string | null;
  /**
   * Mechanical endurance (operations) without load.
   */
  mechanical_durability?: ValueUnit | null;
  /**
   * Electrical endurance under AC-3 duty (operations).
   */
  electrical_durability_ac3?: ValueUnit | null;
  /**
   * Maximum switching frequency under AC-3 (operations/hour).
   */
  operating_frequency_ac3?: ValueUnit | null;
  /**
   * Making (closing) current capacity at rated voltage (A).
   */
  making_capacity?: ValueUnit | null;
  /**
   * Breaking (opening) current capacity at rated voltage (A).
   */
  breaking_capacity?: ValueUnit | null;
  /**
   * Close operate time from coil-on to main-contact-on (ms).
   */
  operating_time_close?: MinMaxUnit | null;
  /**
   * Open operate time from coil-off to main-contact-off (ms).
   */
  operating_time_open?: MinMaxUnit | null;
  /**
   * Operating ambient temperature range (°C).
   */
  operating_temp?: MinMaxUnit | null;
  /**
   * Storage temperature range (°C).
   */
  storage_temp?: MinMaxUnit | null;
  /**
   * IP protection rating (typically 20 for front of panel).
   */
  ip_rating?: number | null;
  /**
   * Maximum operating altitude without derating (m).
   */
  altitude_max?: ValueUnit | null;
  /**
   * Formal standards the device claims compliance with — IEC 60947-4-1, UL 508, CSA 22.2 No. 14, GB 14048.4, EN 60947-4-1. Separate from marketing certifications.
   */
  standards_compliance?: string[] | null;
  /**
   * Marks and third-party certifications: CE, CCC, cULus, UL Listed, BV, DNV, GL, EAC. Separate from standards.
   */
  certifications?: string[] | null;
  /**
   * Supported mounting styles, e.g. ['din_rail_35mm', 'panel_screw'].
   */
  mounting_types?: string[] | null;
}
/**
 * Represents physical dimensions of an object.
 */
export interface Dimensions {
  width?: number | null;
  length?: number | null;
  height?: number | null;
  unit?: string | null;
}
/**
 * A numeric value paired with a unit — the canonical scalar spec shape.
 *
 * Pydantic accepts forgiving input forms (dicts with extra keys,
 * space-separated strings, qualifier-prefixed numbers) and normalises
 * the unit to its canonical form (mNm → Nm) on construction. The
 * serialised form is always ``{"value": <float>, "unit": "<str>"}``.
 */
export interface ValueUnit {
  value: number;
  unit: string;
}
/**
 * A single row from a contactor's utilization-category rating table.
 *
 * Contactor datasheets publish AC-3 / AC-1 / AC-4 ratings as multi-row
 * tables keyed by voltage (e.g. 220V / 400V / 500V / 690V). This type
 * captures one row; ``Contactor.ratings_ac3`` holds the full table.
 * Power is reported in both kW (IEC) and hp (UL/NEMA); we store both
 * because every vendor publishes both and which one the user filters
 * on depends on the market.
 */
export interface ContactorPowerRating {
  /**
   * Operational voltage for this rating row (e.g. 400 V).
   */
  voltage?: ValueUnit | null;
  /**
   * Vendor voltage-bin label when the row represents a range (e.g. '380-415', '220-240', '500-525').
   */
  voltage_group?: string | null;
  /**
   * Rated operational current Ie at this voltage (A).
   */
  current?: ValueUnit | null;
  /**
   * Motor power rating at this voltage (kW, IEC).
   */
  power_kw?: ValueUnit | null;
  /**
   * Motor power rating at this voltage (hp, UL/NEMA market). Store as float; fractional hp like '7-1/2 hp' → 7.5.
   */
  power_hp?: ValueUnit | null;
  /**
   * Ambient temperature the row applies to (°C). AC-1 tables on ABB and Rockwell publish different Ie at 40/60/70 °C.
   */
  ambient_temp?: ValueUnit | null;
}
/**
 * Short-time withstand current at a specific duration.
 *
 * ABB and Allen-Bradley publish Icw as a curve (1 s / 10 s / 30 s / 1 min
 * / 10 min); a scalar Icw loses that shape. Distinct from SCCR (UL
 * short-circuit current rating), which is a single value.
 */
export interface ContactorIcwRating {
  /**
   * Withstand duration, e.g. 10 s.
   */
  duration: ValueUnit | null;
  /**
   * Withstand current for the duration (A).
   */
  current: ValueUnit | null;
}
/**
 * A numeric range paired with a shared unit — canonical range spec shape.
 *
 * At least one of ``min`` / ``max`` must be present; either may be
 * ``None`` for half-open intervals (e.g. ``max=85, min=None`` for "up
 * to 85 °C"). Serialised form is ``{"min": <num|null>, "max": <num|null>,
 * "unit": "<str>"}``.
 */
export interface MinMaxUnit {
  min?: number | null;
  max?: number | null;
  unit: string;
}
/**
 * Defines the specifications for the robot's control box.
 */
export interface Controller {
  /**
   * IP rating of the control box
   */
  ip_rating?: number | null;
  /**
   * Cleanroom classification (ISO 14644-1)
   */
  cleanroom_class?: string | null;
  /**
   * Operating temperature range for the controller
   */
  operating_temp?: MinMaxUnit | null;
  /**
   * I/O ports on the controller
   */
  io_ports?: ControllerIO | null;
  /**
   * List of supported communication protocols
   */
  communication_protocols?: string[] | null;
  /**
   * Main power source requirements (e.g., 100-240VAC)
   */
  power_source?: MinMaxUnit | null;
}
/**
 * Defines the I/O ports available in the main control box.
 */
export interface ControllerIO {
  /**
   * Number of digital inputs
   */
  digital_in?: number;
  /**
   * Number of digital outputs
   */
  digital_out?: number;
  /**
   * Number of analog inputs
   */
  analog_in?: number;
  /**
   * Number of analog outputs
   */
  analog_out?: number;
  /**
   * Number of quadrature digital inputs
   */
  quadrature_inputs?: number;
  /**
   * I/O power supply current at 24V
   */
  power_supply?: ValueUnit | null;
}
/**
 * Represents information about a product datasheet.
 */
export interface Datasheet {
  url?: string | null;
  pages?: number[] | null;
}
/**
 * A Pydantic model representing the specifications of a servo drive.
 */
export interface Drive {
  /**
   * Unique identifier (auto-generated)
   */
  product_id?: string;
  product_type?: "drive";
  /**
   * Product name
   */
  product_name: string;
  /**
   * Product family or sub-series
   */
  product_family?: string | null;
  /**
   * Part number
   */
  part_number?: string | null;
  /**
   * Manufacturer name
   */
  manufacturer: string;
  release_year?: number | null;
  dimensions?: Dimensions | null;
  weight?: ValueUnit | null;
  msrp?: ValueUnit | null;
  /**
   * URL the MSRP was scraped from. Populated by price-enrich.
   */
  msrp_source_url?: string | null;
  /**
   * ISO 8601 timestamp when MSRP was last fetched.
   */
  msrp_fetched_at?: string | null;
  warranty?: ValueUnit | null;
  /**
   * Expected delivery / lead time for the product. Typically a ValueUnit with unit='days' (e.g. {'value': 30, 'unit': 'days'}). Sourced from manufacturer or distributor data, not the datasheet.
   */
  lead_time?: ValueUnit | null;
  /**
   * URL of the source datasheet
   */
  datasheet_url?: string | null;
  /**
   * 1-indexed PDF pages where this product's specs were found. Used for #page=N deep-linking.
   */
  pages?: number[] | null;
  type?: ("servo" | "variable frequency") | null;
  series?: string | null;
  input_voltage?: MinMaxUnit | null;
  input_voltage_frequency?: (MinMaxUnit | null)[] | null;
  input_voltage_phases?: number[] | null;
  rated_current?: ValueUnit | null;
  peak_current?: ValueUnit | null;
  rated_power?: ValueUnit | null;
  switching_frequency?: (ValueUnit | null)[] | null;
  fieldbus?:
    | ("EtherCAT" | "EtherNet/IP" | "PROFINET" | "Modbus TCP" | "POWERLINK" | "Sercos III" | "CC-Link IE")[]
    | null;
  encoder_feedback_support?:
    | (
        | "quadrature_ttl"
        | "open_collector"
        | "hall_uvw"
        | "sin_cos_1vpp"
        | "ssi"
        | "biss_c"
        | "endat_2_1"
        | "endat_2_2"
        | "hiperface"
        | "hiperface_dsl"
        | "tamagawa_t_format"
        | "mitsubishi_j3"
        | "mitsubishi_j4"
        | "mitsubishi_j5"
        | "panasonic_a6"
        | "yaskawa_sigma"
        | "fanuc_serial"
        | "drive_cliq"
        | "oct_beckhoff"
        | "resolver_analog"
        | "proprietary_other"
        | "unknown"
      )[]
    | null;
  ethernet_ports?: number | null;
  digital_inputs?: number | null;
  digital_outputs?: number | null;
  analog_inputs?: number | null;
  analog_outputs?: number | null;
  safety_rating?: string[] | null;
  approvals?: string[] | null;
  max_humidity?: number | null;
  ip_rating?: number | null;
  operating_temp?: MinMaxUnit | null;
}
/**
 * Linear actuator with integrated motor — produces force (N), not torque (Nm).
 *
 * Covers products like Faulhaber L-series linear actuators that combine
 * a motor, gearhead, and lead screw into a single unit producing linear
 * motion. Key differentiator from motors: output is force/stroke, not
 * torque/speed.
 */
export interface ElectricCylinder {
  /**
   * Unique identifier (auto-generated)
   */
  product_id?: string;
  product_type?: "electric_cylinder";
  /**
   * Product name
   */
  product_name: string;
  /**
   * Product family or sub-series
   */
  product_family?: string | null;
  /**
   * Part number
   */
  part_number?: string | null;
  /**
   * Manufacturer name
   */
  manufacturer: string;
  release_year?: number | null;
  dimensions?: Dimensions | null;
  weight?: ValueUnit | null;
  msrp?: ValueUnit | null;
  /**
   * URL the MSRP was scraped from. Populated by price-enrich.
   */
  msrp_source_url?: string | null;
  /**
   * ISO 8601 timestamp when MSRP was last fetched.
   */
  msrp_fetched_at?: string | null;
  warranty?: ValueUnit | null;
  /**
   * Expected delivery / lead time for the product. Typically a ValueUnit with unit='days' (e.g. {'value': 30, 'unit': 'days'}). Sourced from manufacturer or distributor data, not the datasheet.
   */
  lead_time?: ValueUnit | null;
  /**
   * URL of the source datasheet
   */
  datasheet_url?: string | null;
  /**
   * 1-indexed PDF pages where this product's specs were found. Used for #page=N deep-linking.
   */
  pages?: number[] | null;
  type?: ("linear actuator" | "linear servo" | "micro linear actuator" | "tubular linear motor") | null;
  series?: string | null;
  /**
   * Maximum linear travel (e.g., in mm)
   */
  stroke?: ValueUnit | null;
  /**
   * Maximum push/extend force (e.g., in N)
   */
  max_push_force?: ValueUnit | null;
  /**
   * Maximum pull/retract force (e.g., in N)
   */
  max_pull_force?: ValueUnit | null;
  /**
   * Continuous rated force (e.g., in N)
   */
  continuous_force?: ValueUnit | null;
  /**
   * Maximum linear speed unloaded (e.g., in mm/s)
   */
  max_linear_speed?: ValueUnit | null;
  /**
   * Repeatability of positioning (e.g., in mm)
   */
  positioning_repeatability?: ValueUnit | null;
  /**
   * Rated input voltage (e.g., in V)
   */
  rated_voltage?: MinMaxUnit | null;
  /**
   * Rated current draw (e.g., in A)
   */
  rated_current?: ValueUnit | null;
  /**
   * Peak current draw (e.g., in A)
   */
  peak_current?: ValueUnit | null;
  /**
   * Rated motor power (e.g., in W)
   */
  rated_power?: ValueUnit | null;
  /**
   * Type of integrated motor (e.g., 'brushless dc', 'brushed dc')
   */
  motor_type?: string | null;
  /**
   * Motor frame designator the cylinder mounts to (e.g. 'NEMA 23').
   */
  motor_mount_pattern?:
    | (
        | "NEMA 8"
        | "NEMA 11"
        | "NEMA 14"
        | "NEMA 17"
        | "NEMA 23"
        | "NEMA 34"
        | "NEMA 42"
        | "IEC 56"
        | "IEC 63"
        | "IEC 71"
        | "IEC 80"
        | "IEC 90"
        | "IEC 100"
        | "IEC 112"
        | "IEC 132"
        | "MAX 8"
        | "MAX 13"
        | "MAX 16"
        | "MAX 20"
        | "MAX 25"
        | "MAX 30"
        | "MAX 35"
        | "MAX 40"
        | "custom"
      )
    | null;
  /**
   * Lead screw pitch (e.g., in mm/rev)
   */
  lead_screw_pitch?: ValueUnit | null;
  /**
   * Mechanical backlash (e.g., in mm)
   */
  backlash?: ValueUnit | null;
  /**
   * Maximum radial load on output shaft (e.g., in N)
   */
  max_radial_load?: ValueUnit | null;
  /**
   * Maximum static axial load (e.g., in N)
   */
  max_axial_load?: ValueUnit | null;
  /**
   * Encoder or position feedback type (structured)
   */
  encoder_feedback_support?: EncoderFeedback | null;
  /**
   * Communication interface (e.g., 'CANopen', 'RS-232')
   */
  fieldbus?: string | null;
  /**
   * Ingress Protection rating
   */
  ip_rating?: number | null;
  /**
   * Operating temperature range
   */
  operating_temp?: MinMaxUnit | null;
  /**
   * Expected service life (e.g., in hours or cycles)
   */
  service_life?: ValueUnit | null;
  /**
   * Noise level (e.g., in dBA)
   */
  noise_level?: ValueUnit | null;
}
/**
 * One encoder-feedback specification (motor / actuator side).
 *
 * All fields except ``device`` are optional because real catalogs
 * publish wildly varying levels of detail. The verifier flags rows
 * with ``device="unknown"`` (or with a populated ``raw`` indicating
 * legacy free-text shim) for a primed second-pass extraction.
 *
 * Drives don't use this full model — they use ``Optional[List[
 * EncoderProtocol]]`` directly because the wire format is what has
 * to line up for compatibility.
 */
export interface EncoderFeedback {
  /**
   * Physical sensor type. Use 'incremental_optical' for plain quadrature optical encoders; 'absolute_optical' for single-turn absolute optical (BiSS-C, EnDat, etc.); 'absolute_optical_multiturn' when the spec mentions multi-turn or 'MT'; 'resolver' for resolvers; 'none' for sensorless / open-loop; 'unknown' only when the catalog text doesn't fit any enum.
   */
  device?:
    | "incremental_optical"
    | "absolute_optical"
    | "absolute_optical_multiturn"
    | "incremental_magnetic"
    | "absolute_magnetic"
    | "sin_cos_analog"
    | "resolver"
    | "inductive"
    | "capacitive"
    | "tachometer_dc"
    | "hall_only"
    | "none"
    | "unknown";
  /**
   * Wire / digital interface protocol. Map vendor names to enums: 'EnDat 2.2'→'endat_2_2', 'BiSS-C'→'biss_c', 'Hiperface DSL'→'hiperface_dsl' (vs bare 'Hiperface'→'hiperface'), 'Mitsubishi MR-J5'→'mitsubishi_j5'. Bare 'EnDat' with no version → 'endat_2_2'. Bare 'N-bit absolute' with no vendor name → leave null and set bits_per_turn=N — DO NOT guess the protocol from the bit count alone.
   */
  protocol?:
    | (
        | "quadrature_ttl"
        | "open_collector"
        | "hall_uvw"
        | "sin_cos_1vpp"
        | "ssi"
        | "biss_c"
        | "endat_2_1"
        | "endat_2_2"
        | "hiperface"
        | "hiperface_dsl"
        | "tamagawa_t_format"
        | "mitsubishi_j3"
        | "mitsubishi_j4"
        | "mitsubishi_j5"
        | "panasonic_a6"
        | "yaskawa_sigma"
        | "fanuc_serial"
        | "drive_cliq"
        | "oct_beckhoff"
        | "resolver_analog"
        | "proprietary_other"
        | "unknown"
      )
    | null;
  /**
   * 'incremental' (relative position) or 'absolute'.
   */
  mode?: ("incremental" | "absolute") | null;
  /**
   * True for multi-turn encoders (track revolution count), False for single-turn (reset every revolution). Only meaningful for absolute encoders.
   */
  multiturn?: boolean | null;
  /**
   * Number of revolution-counting bits, when multiturn=true. Common values: 12, 16, 20. Leave null if not stated — industry default varies by vendor (don't guess).
   */
  multiturn_bits?: number | null;
  /**
   * True for battery-backed multi-turn encoders, False for true (mechanical / Wiegand) batteryless multi-turn (Panasonic A6, Mitsubishi MR-J5). Omit when unknown.
   */
  multiturn_battery_backed?: boolean | null;
  /**
   * Single-turn resolution in bits, for absolute encoders. Examples: 17, 20, 22, 23, 24, 26.
   */
  bits_per_turn?: number | null;
  /**
   * Pulses per revolution, for incremental encoders (PPR). Quote the catalog value before edge multiplication — 2,500 PPR not 10,000 CPR.
   */
  pulses_per_rev?: number | null;
  /**
   * Lines per revolution, for sin/cos analog encoders. Common values: 1,024 / 2,048 / 4,096.
   */
  lines_per_rev?: number | null;
  /**
   * Resolver pole-pair count (1, 2, 4...). Only meaningful for device='resolver'. If unstated for a 'resolver' entry, industry default is 1 (1X).
   */
  resolver_pole_pairs?: number | null;
  /**
   * Original catalog text. Populated by the back-compat shim when legacy free-text payloads are coerced; the verifier uses it to drive the primed second-pass extraction.
   */
  raw?: string | null;
  [k: string]: unknown;
}
/**
 * Defines the specifications of the built-in force/torque sensor.
 */
export interface ForceTorqueSensor {
  /**
   * Measurement range for force (e.g., in N)
   */
  force_range?: ValueUnit | null;
  /**
   * Precision (repeatability) of force measurement (e.g., in N)
   */
  force_precision?: ValueUnit | null;
  /**
   * Measurement range for torque (e.g., in Nm)
   */
  torque_range?: ValueUnit | null;
  /**
   * Precision (repeatability) of torque measurement (e.g., in Nm)
   */
  torque_precision?: ValueUnit | null;
}
/**
 * A Pydantic model representing a gearhead.
 *
 * This model extends the ProductBase to include attributes specific to
 * gearheads, which are crucial for engineering and selection processes.
 * This model is pre-populated with defaults for the Sesame PHL series.
 */
export interface Gearhead {
  /**
   * Unique identifier (auto-generated)
   */
  product_id?: string;
  product_type?: "gearhead";
  /**
   * Product name
   */
  product_name: string;
  /**
   * Product family or sub-series
   */
  product_family?: string | null;
  /**
   * Part number
   */
  part_number?: string | null;
  /**
   * Manufacturer name
   */
  manufacturer: string;
  release_year?: number | null;
  dimensions?: Dimensions | null;
  weight?: ValueUnit | null;
  msrp?: ValueUnit | null;
  /**
   * URL the MSRP was scraped from. Populated by price-enrich.
   */
  msrp_source_url?: string | null;
  /**
   * ISO 8601 timestamp when MSRP was last fetched.
   */
  msrp_fetched_at?: string | null;
  warranty?: ValueUnit | null;
  /**
   * Expected delivery / lead time for the product. Typically a ValueUnit with unit='days' (e.g. {'value': 30, 'unit': 'days'}). Sourced from manufacturer or distributor data, not the datasheet.
   */
  lead_time?: ValueUnit | null;
  /**
   * URL of the source datasheet
   */
  datasheet_url?: string | null;
  /**
   * 1-indexed PDF pages where this product's specs were found. Used for #page=N deep-linking.
   */
  pages?: number[] | null;
  /**
   * The ratio of input speed to output speed (e.g., 10.0 for 10:1)
   */
  gear_ratio?: number | null;
  /**
   * Type of gear mechanism (e.g., 'Planetary', 'Spur', 'Helical')
   */
  gear_type?: string | null;
  /**
   * Number of gear stages (e.g., 1 or 2)
   */
  stages?: number | null;
  /**
   * Nominal continuous input speed (e.g., in rpm)
   */
  nominal_input_speed?: ValueUnit | null;
  /**
   * Maximum allowable input speed (e.g., in rpm)
   */
  max_input_speed?: ValueUnit | null;
  /**
   * Nominal continuous output torque (T2N) (e.g., in Nm)
   */
  max_continuous_torque?: ValueUnit | null;
  /**
   * Emergency-stop / transient peak torque (T2NOT) (e.g., in Nm)
   */
  max_peak_torque?: ValueUnit | null;
  /**
   * Rotational lost motion (e.g., in arcminutes)
   */
  backlash?: ValueUnit | null;
  /**
   * Efficiency of the gearhead as a ratio (e.g., 0.97 for 97%)
   */
  efficiency?: number | null;
  /**
   * Torsional rigidity (e.g., in Nm/arcmin)
   */
  torsional_rigidity?: ValueUnit | null;
  /**
   * Moment of inertia for the gearbox (e.g., in kg.cm²)
   */
  rotor_inertia?: ValueUnit | null;
  /**
   * Noise level at 1m distance (e.g., in dBA)
   */
  noise_level?: ValueUnit | null;
  /**
   * Gearbox frame size, corresponding to flange (e.g., 42, 60)
   */
  frame_size?: string | null;
  /**
   * Diameter of the input shaft (motor specific) (e.g., in mm)
   */
  input_shaft_diameter?: ValueUnit | null;
  /**
   * Diameter of the output shaft (e.g., in mm)
   */
  output_shaft_diameter?: ValueUnit | null;
  /**
   * Motor frames this gearhead accepts on its input flange (e.g. ['NEMA 23', 'NEMA 34']).
   */
  input_motor_mount?:
    | (
        | "NEMA 8"
        | "NEMA 11"
        | "NEMA 14"
        | "NEMA 17"
        | "NEMA 23"
        | "NEMA 34"
        | "NEMA 42"
        | "IEC 56"
        | "IEC 63"
        | "IEC 71"
        | "IEC 80"
        | "IEC 90"
        | "IEC 100"
        | "IEC 112"
        | "IEC 132"
        | "MAX 8"
        | "MAX 13"
        | "MAX 16"
        | "MAX 20"
        | "MAX 25"
        | "MAX 30"
        | "MAX 35"
        | "MAX 40"
        | "custom"
      )[]
    | null;
  /**
   * Output flange pattern (matches downstream device's input mount).
   */
  output_motor_mount?:
    | (
        | "NEMA 8"
        | "NEMA 11"
        | "NEMA 14"
        | "NEMA 17"
        | "NEMA 23"
        | "NEMA 34"
        | "NEMA 42"
        | "IEC 56"
        | "IEC 63"
        | "IEC 71"
        | "IEC 80"
        | "IEC 90"
        | "IEC 100"
        | "IEC 112"
        | "IEC 132"
        | "MAX 8"
        | "MAX 13"
        | "MAX 16"
        | "MAX 20"
        | "MAX 25"
        | "MAX 30"
        | "MAX 35"
        | "MAX 40"
        | "custom"
      )
    | null;
  /**
   * Maximum radial load (F2m) (e.g., in N)
   */
  max_radial_load?: ValueUnit | null;
  /**
   * Maximum axial load (F2ab) (e.g., in N)
   */
  max_axial_load?: ValueUnit | null;
  /**
   * Ingress Protection (IP) rating
   */
  ip_rating?: number | null;
  /**
   * Operating temperature range
   */
  operating_temp?: MinMaxUnit | null;
  /**
   * Expected service life (e.g., in hours)
   */
  service_life?: ValueUnit | null;
  /**
   * Type of lubrication used
   */
  lubrication_type?: string | null;
}
/**
 * Defines the specifications for a single robot joint.
 */
export interface JointSpecs {
  /**
   * Name of the joint (e.g., 'Base', 'Wrist 1')
   */
  joint_name: string;
  /**
   * The rotational range of the joint
   */
  working_range?: ValueUnit | null;
  /**
   * Maximum speed of the joint
   */
  max_speed?: ValueUnit | null;
}
/**
 * Rodless linear-motion module — carriage moving along a guided rail.
 *
 * Distinct from ``ElectricCylinder`` (which pushes/pulls from a rod tip):
 * a linear actuator's payload rides on the body of the unit and travels
 * along a guided rail or stage. Drive can be ball screw, lead screw,
 * belt, or linear motor; many are sold motorless for pairing with the
 * customer's servo.
 */
export interface LinearActuator {
  /**
   * Unique identifier (auto-generated)
   */
  product_id?: string;
  product_type?: "linear_actuator";
  /**
   * Product name
   */
  product_name: string;
  /**
   * Product family or sub-series
   */
  product_family?: string | null;
  /**
   * Part number
   */
  part_number?: string | null;
  /**
   * Manufacturer name
   */
  manufacturer: string;
  release_year?: number | null;
  dimensions?: Dimensions | null;
  weight?: ValueUnit | null;
  msrp?: ValueUnit | null;
  /**
   * URL the MSRP was scraped from. Populated by price-enrich.
   */
  msrp_source_url?: string | null;
  /**
   * ISO 8601 timestamp when MSRP was last fetched.
   */
  msrp_fetched_at?: string | null;
  warranty?: ValueUnit | null;
  /**
   * Expected delivery / lead time for the product. Typically a ValueUnit with unit='days' (e.g. {'value': 30, 'unit': 'days'}). Sourced from manufacturer or distributor data, not the datasheet.
   */
  lead_time?: ValueUnit | null;
  /**
   * URL of the source datasheet
   */
  datasheet_url?: string | null;
  /**
   * 1-indexed PDF pages where this product's specs were found. Used for #page=N deep-linking.
   */
  pages?: number[] | null;
  /**
   * Form factor of the linear-motion module.
   */
  type?: ("linear_slide" | "linear_stage" | "rodless_screw" | "rodless_belt" | "lm_guide_actuator") | null;
  series?: string | null;
  /**
   * Maximum linear travel (e.g., in mm)
   */
  stroke?: ValueUnit | null;
  /**
   * Maximum payload mass the carriage can move (e.g., in kg)
   */
  max_work_load?: ValueUnit | null;
  /**
   * Maximum thrust force on the carriage (e.g., in N)
   */
  max_push_force?: ValueUnit | null;
  /**
   * Force exerted by an optional holding brake (e.g., in N)
   */
  holding_force?: ValueUnit | null;
  /**
   * Dynamic load rating for bearing life calculations (e.g., in N)
   */
  dynamic_load_rating?: ValueUnit | null;
  /**
   * Static load rating for bearing capacity (e.g., in N)
   */
  static_load_rating?: ValueUnit | null;
  /**
   * Maximum linear speed (e.g., in mm/s)
   */
  max_linear_speed?: ValueUnit | null;
  /**
   * Maximum linear acceleration (e.g., in mm/s²)
   */
  max_acceleration?: ValueUnit | null;
  /**
   * Repeatability of positioning (e.g., in mm)
   */
  positioning_repeatability?: ValueUnit | null;
  /**
   * Mechanical backlash (e.g., in mm or arcmin)
   */
  backlash?: ValueUnit | null;
  /**
   * Primary drive mechanism for linear motion.
   */
  actuation_mechanism?: ("ball_screw" | "lead_screw" | "belt" | "linear_motor") | null;
  /**
   * Lead screw pitch (e.g., in mm/rev)
   */
  lead_screw_pitch?: ValueUnit | null;
  /**
   * Nominal diameter of the lead screw (e.g., in mm)
   */
  screw_diameter?: ValueUnit | null;
  /**
   * Static allowable pitching moment (e.g., in Nm)
   */
  static_allowable_moment_pitching?: ValueUnit | null;
  /**
   * Static allowable yawing moment (e.g., in Nm)
   */
  static_allowable_moment_yawing?: ValueUnit | null;
  /**
   * Static allowable rolling moment (e.g., in Nm)
   */
  static_allowable_moment_rolling?: ValueUnit | null;
  /**
   * Mass moment of inertia of the moving parts (e.g., in kg·cm²)
   */
  rotor_inertia?: ValueUnit | null;
  /**
   * Type of integrated motor. 'motorless' for units sold without a motor (customer pairs their own servo).
   */
  motor_type?: ("step_motor" | "servo_motor" | "motorless") | null;
  /**
   * Types of encoder feedback supported.
   */
  encoder_feedback_support?: EncoderFeedback[] | null;
  /**
   * Motor frames this actuator can accept (e.g. ['NEMA 23', 'NEMA 34']). Drives compatible-motor queries on the /actuators page.
   */
  compatible_motor_mounts?:
    | (
        | "NEMA 8"
        | "NEMA 11"
        | "NEMA 14"
        | "NEMA 17"
        | "NEMA 23"
        | "NEMA 34"
        | "NEMA 42"
        | "IEC 56"
        | "IEC 63"
        | "IEC 71"
        | "IEC 80"
        | "IEC 90"
        | "IEC 100"
        | "IEC 112"
        | "IEC 132"
        | "MAX 8"
        | "MAX 13"
        | "MAX 16"
        | "MAX 20"
        | "MAX 25"
        | "MAX 30"
        | "MAX 35"
        | "MAX 40"
        | "custom"
      )[]
    | null;
  /**
   * Rated input voltage (e.g., in V)
   */
  rated_voltage?: MinMaxUnit | null;
  /**
   * Rated electrical current (e.g., in A)
   */
  rated_current?: ValueUnit | null;
  /**
   * Peak electrical current (e.g., in A)
   */
  peak_current?: ValueUnit | null;
  /**
   * Rated electrical power (e.g., in W)
   */
  rated_power?: ValueUnit | null;
  /**
   * Ingress Protection rating
   */
  ip_rating?: number | null;
  /**
   * Operating temperature range
   */
  operating_temp?: MinMaxUnit | null;
  /**
   * Operating humidity range (e.g., in %RH)
   */
  operating_humidity_range?: ValueUnit | null;
  /**
   * Cleanroom classification (e.g., 'ISO Class 5')
   */
  cleanroom_class?: string | null;
}
/**
 * A Pydantic model representing a manufacturer of industrial equipment.
 * Designed for DynamoDB single-table design.
 */
export interface Manufacturer {
  /**
   * Unique identifier (auto-generated)
   */
  id?: string;
  /**
   * Name of the manufacturer
   */
  name: string;
  /**
   * Official website URL
   */
  website?: string | null;
  /**
   * List of product types offered (e.g., 'motor', 'drive')
   */
  offered_product_types?:
    | ("motor" | "drive" | "gearhead" | "robot_arm" | "contactor" | "electric_cylinder" | "linear_actuator")[]
    | null;
}
/**
 * A Pydantic model representing the specifications of a motor.
 */
export interface Motor {
  /**
   * Unique identifier (auto-generated)
   */
  product_id?: string;
  product_type?: "motor";
  /**
   * Product name
   */
  product_name: string;
  /**
   * Product family or sub-series
   */
  product_family?: string | null;
  /**
   * Part number
   */
  part_number?: string | null;
  /**
   * Manufacturer name
   */
  manufacturer: string;
  release_year?: number | null;
  dimensions?: Dimensions | null;
  weight?: ValueUnit | null;
  msrp?: ValueUnit | null;
  /**
   * URL the MSRP was scraped from. Populated by price-enrich.
   */
  msrp_source_url?: string | null;
  /**
   * ISO 8601 timestamp when MSRP was last fetched.
   */
  msrp_fetched_at?: string | null;
  warranty?: ValueUnit | null;
  /**
   * Expected delivery / lead time for the product. Typically a ValueUnit with unit='days' (e.g. {'value': 30, 'unit': 'days'}). Sourced from manufacturer or distributor data, not the datasheet.
   */
  lead_time?: ValueUnit | null;
  /**
   * URL of the source datasheet
   */
  datasheet_url?: string | null;
  /**
   * 1-indexed PDF pages where this product's specs were found. Used for #page=N deep-linking.
   */
  pages?: number[] | null;
  type?:
    | ("brushless dc" | "brushed dc" | "ac induction" | "ac synchronous" | "ac servo" | "permanent magnet" | "hybrid")
    | null;
  series?: string | null;
  rated_voltage?: MinMaxUnit | null;
  rated_speed?: ValueUnit | null;
  max_speed?: ValueUnit | null;
  rated_torque?: ValueUnit | null;
  peak_torque?: ValueUnit | null;
  rated_power?: ValueUnit | null;
  encoder_feedback_support?: EncoderFeedback | null;
  poles?: number | null;
  rated_current?: ValueUnit | null;
  peak_current?: ValueUnit | null;
  voltage_constant?: ValueUnit | null;
  torque_constant?: ValueUnit | null;
  resistance?: ValueUnit | null;
  inductance?: ValueUnit | null;
  ip_rating?: number | null;
  rotor_inertia?: ValueUnit | null;
  axial_load_force_rating?: MinMaxUnit | null;
  radial_load_force_rating?: MinMaxUnit | null;
  shaft_diameter?: ValueUnit | null;
  frame_size?: string | null;
  motor_mount_pattern?:
    | (
        | "NEMA 8"
        | "NEMA 11"
        | "NEMA 14"
        | "NEMA 17"
        | "NEMA 23"
        | "NEMA 34"
        | "NEMA 42"
        | "IEC 56"
        | "IEC 63"
        | "IEC 71"
        | "IEC 80"
        | "IEC 90"
        | "IEC 100"
        | "IEC 112"
        | "IEC 132"
        | "MAX 8"
        | "MAX 13"
        | "MAX 16"
        | "MAX 20"
        | "MAX 25"
        | "MAX 30"
        | "MAX 35"
        | "MAX 40"
        | "custom"
      )
    | null;
}
/**
 * A base model for products with common attributes, designed for DynamoDB.
 *
 * This model uses a composite primary key (PK, SK) to align with DynamoDB's
 * single-table design best practices.
 *
 * Attributes:
 *     PK: The Partition Key. Formatted as 'PRODUCT#<product_type>'.
 *     SK: The Sort Key. Formatted as 'PRODUCT#<product_id>'.
 *     product_id: The unique identifier (UUID) for the product.
 */
export interface ProductBase {
  /**
   * Unique identifier (auto-generated)
   */
  product_id?: string;
  /**
   * Type of product (e.g., 'motor', 'drive')
   */
  product_type: "motor" | "drive" | "gearhead" | "robot_arm" | "contactor" | "electric_cylinder" | "linear_actuator";
  /**
   * Product name
   */
  product_name: string;
  /**
   * Product family or sub-series
   */
  product_family?: string | null;
  /**
   * Part number
   */
  part_number?: string | null;
  /**
   * Manufacturer name
   */
  manufacturer: string;
  release_year?: number | null;
  dimensions?: Dimensions | null;
  weight?: ValueUnit | null;
  msrp?: ValueUnit | null;
  /**
   * URL the MSRP was scraped from. Populated by price-enrich.
   */
  msrp_source_url?: string | null;
  /**
   * ISO 8601 timestamp when MSRP was last fetched.
   */
  msrp_fetched_at?: string | null;
  warranty?: ValueUnit | null;
  /**
   * Expected delivery / lead time for the product. Typically a ValueUnit with unit='days' (e.g. {'value': 30, 'unit': 'days'}). Sourced from manufacturer or distributor data, not the datasheet.
   */
  lead_time?: ValueUnit | null;
  /**
   * URL of the source datasheet
   */
  datasheet_url?: string | null;
  /**
   * 1-indexed PDF pages where this product's specs were found. Used for #page=N deep-linking.
   */
  pages?: number[] | null;
}
/**
 * A Pydantic model representing a collaborative robot arm.
 *
 * This model extends ProductBase to include detailed specifications
 * for the arm, controller, and teach pendant. Defaults are based
 * on the Universal Robots e-Series.
 */
export interface RobotArm {
  /**
   * Unique identifier (auto-generated)
   */
  product_id?: string;
  product_type?: "robot_arm";
  /**
   * Product name
   */
  product_name: string;
  /**
   * Product family or series
   */
  product_family?: string;
  /**
   * Part number
   */
  part_number?: string | null;
  /**
   * Manufacturer name
   */
  manufacturer?: string;
  release_year?: number | null;
  dimensions?: Dimensions | null;
  weight?: ValueUnit | null;
  msrp?: ValueUnit | null;
  /**
   * URL the MSRP was scraped from. Populated by price-enrich.
   */
  msrp_source_url?: string | null;
  /**
   * ISO 8601 timestamp when MSRP was last fetched.
   */
  msrp_fetched_at?: string | null;
  warranty?: ValueUnit | null;
  /**
   * Expected delivery / lead time for the product. Typically a ValueUnit with unit='days' (e.g. {'value': 30, 'unit': 'days'}). Sourced from manufacturer or distributor data, not the datasheet.
   */
  lead_time?: ValueUnit | null;
  /**
   * URL of the source datasheet
   */
  datasheet_url?: string | null;
  /**
   * 1-indexed PDF pages where this product's specs were found. Used for #page=N deep-linking.
   */
  pages?: number[] | null;
  /**
   * Rated payload capacity (e.g., in kg)
   */
  payload?: ValueUnit | null;
  /**
   * Maximum reach from center of base (e.g., in mm)
   */
  reach?: ValueUnit | null;
  /**
   * Number of rotating joints
   */
  degrees_of_freedom?: number;
  /**
   * Pose repeatability per ISO 9283 (e.g., in mm)
   */
  pose_repeatability?: ValueUnit | null;
  /**
   * Maximum speed of the Tool Center Point (e.g., in m/s)
   */
  max_tcp_speed?: ValueUnit | null;
  /**
   * IP rating of the robot arm
   */
  ip_rating?: number | null;
  /**
   * Cleanroom classification (ISO 14644-1)
   */
  cleanroom_class?: string | null;
  /**
   * Typical noise level (e.g., in dB(A))
   */
  noise_level?: ValueUnit | null;
  /**
   * Allowed mounting positions
   */
  mounting_position?: string | null;
  /**
   * Operating temperature range for the arm
   */
  operating_temp?: MinMaxUnit | null;
  /**
   * Main materials used in arm construction
   */
  materials?: string[] | null;
  /**
   * List of specifications for each joint
   */
  joints?: JointSpecs[] | null;
  /**
   * Specifications of the integrated F/T sensor
   */
  force_torque_sensor?: ForceTorqueSensor | null;
  /**
   * I/O and power at the tool flange
   */
  tool_io?: ToolIO | null;
  /**
   * Specifications of the control box
   */
  controller?: Controller | null;
  /**
   * Specifications of the teach pendant
   */
  teach_pendant?: TeachPendant | null;
  /**
   * List of safety certifications
   */
  safety_certifications?: string[] | null;
}
/**
 * Defines the I/O ports available at the tool (end-effector) flange.
 */
export interface ToolIO {
  /**
   * Number of digital inputs
   */
  digital_in?: number;
  /**
   * Number of digital outputs
   */
  digital_out?: number;
  /**
   * Number of analog inputs
   */
  analog_in?: number;
  /**
   * Selectable power supply voltage (e.g., 12V or 24V)
   */
  power_supply_voltage?: ValueUnit | null;
  /**
   * Maximum current for the tool power supply (e.g., in mA or A)
   */
  power_supply_current?: ValueUnit | null;
  /**
   * Physical connector type at the tool
   */
  connector_type?: string | null;
  communication_protocols?: string[] | null;
}
/**
 * Defines the specifications for the teach pendant.
 */
export interface TeachPendant {
  /**
   * IP rating of the teach pendant
   */
  ip_rating?: number | null;
  /**
   * Screen resolution in pixels
   */
  display_resolution?: string | null;
  /**
   * Diagonal screen size
   */
  display_size?: ValueUnit | null;
  /**
   * Weight of the pendant
   */
  weight?: ValueUnit | null;
  /**
   * Cable length
   */
  cable_length?: ValueUnit | null;
}

// ─────────────────────────────────────────────────────────────
// Generated constants — derived from SCHEMA_CHOICES in
// specodex/config.py (auto-discovered product types). Use the
// PRODUCT_TYPES tuple as the single source of truth in TS
// (e.g. for a Zod enum or an allowlist).
// ─────────────────────────────────────────────────────────────
export const PRODUCT_TYPES = [
  "contactor",
  "drive",
  "electric_cylinder",
  "gearhead",
  "linear_actuator",
  "motor",
  "robot_arm",
] as const;
export type ProductTypeLiteral = (typeof PRODUCT_TYPES)[number];

// ─────────────────────────────────────────────────────────────
// Generated discriminated union — same auto-discovery contract
// as PRODUCT_TYPES (one interface per concrete ProductBase
// subclass under specodex/models/). Discriminator is the
// ``product_type`` literal on each interface.
// ─────────────────────────────────────────────────────────────
export type Product = Contactor | Drive | ElectricCylinder | Gearhead | LinearActuator | Motor | RobotArm;
