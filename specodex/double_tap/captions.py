"""Per-product-type field captions — text labels the LLM should look for.

When the verifier flags a missing field, the primed second-pass prompt
includes the field's catalog captions: "for `rotor_inertia`, look for
'Rotor inertia', 'Inertia', 'GD²/4', 'J' followed by units of kg·m² or
kg·cm²". This is the "feed in the text of the missing fields themselves
to see if you can find them" knob from the DOUBLE_TAP plan.

Captions are best-effort and incomplete on purpose — adding them when
a real fixture shows the LLM missed an extractable field is cheaper
than enumerating every vendor's phrasing up-front. New entries are
welcome via PR.
"""

from __future__ import annotations

from typing import Mapping


# Maps ``field_name`` → list of catalog phrases the LLM should hunt for.
# Keys must match the Pydantic field name on the product model.
_MOTOR_CAPTIONS: dict[str, list[str]] = {
    "rated_voltage": [
        "Rated voltage",
        "Voltage rating",
        "Nominal voltage",
        "U_N",
        "V_dc",
    ],
    "rated_current": ["Rated current", "Nominal current", "I_N", "I_rated"],
    "rated_torque": ["Rated torque", "Continuous torque", "T_N", "M_N", "Nm"],
    "peak_torque": ["Peak torque", "Maximum torque", "T_max", "M_max", "Stall torque"],
    "rated_speed": ["Rated speed", "Nominal speed", "n_N", "rpm"],
    "max_speed": ["Maximum speed", "No-load speed", "n_max", "n_0"],
    "rated_power": ["Rated power", "Nominal power", "P_N", "kW", "W"],
    "rotor_inertia": [
        "Rotor inertia",
        "Inertia",
        "GD²/4",
        "J",
        "kg·m²",
        "kg·cm²",
        "Moment of inertia",
    ],
    "torque_constant": ["Torque constant", "K_T", "Nm/A"],
    "voltage_constant": ["Voltage constant", "K_E", "V/krpm", "Back-EMF constant"],
    "resistance": ["Phase resistance", "Winding resistance", "R", "Ω"],
    "inductance": ["Phase inductance", "Winding inductance", "L", "mH"],
    "shaft_diameter": ["Shaft diameter", "Output shaft", "Shaft Ø", "mm"],
    "frame_size": ["Frame size", "Flange size", "NEMA", "IEC"],
    "ip_rating": [
        "Protection class",
        "IP rating",
        "Ingress protection",
        "IP65",
        "IP67",
    ],
    "encoder_feedback_support": [
        "Encoder",
        "Feedback",
        "Position sensor",
        "Resolver",
        "Hall sensor",
        "EnDat",
        "BiSS",
        "Hiperface",
        "Tamagawa",
    ],
    "poles": ["Pole pairs", "Number of poles", "Poles", "p"],
}

_DRIVE_CAPTIONS: dict[str, list[str]] = {
    "input_voltage": [
        "Input voltage",
        "Mains voltage",
        "Supply voltage",
        "U_in",
        "VAC",
    ],
    "rated_current": ["Rated output current", "Continuous current", "I_N", "I_cont"],
    "peak_current": ["Peak current", "Maximum current", "I_max"],
    "rated_power": ["Rated output power", "Continuous power", "P_N", "kW"],
    "switching_frequency": ["Switching frequency", "PWM frequency", "f_PWM", "kHz"],
    "input_voltage_phases": ["Phases", "Number of phases", "1-phase", "3-phase"],
    "input_voltage_frequency": ["Mains frequency", "Input frequency", "50 Hz", "60 Hz"],
    "fieldbus": [
        "Communication",
        "Fieldbus",
        "EtherCAT",
        "PROFINET",
        "EtherNet/IP",
        "CANopen",
    ],
    "encoder_feedback_support": [
        "Supported encoders",
        "Encoder interface",
        "Feedback interface",
        "EnDat",
        "BiSS",
        "Hiperface",
        "Resolver",
        "TTL",
        "1Vpp",
    ],
    "ip_rating": ["Protection class", "IP rating", "IP20", "IP54"],
    "ethernet_ports": ["Ethernet ports", "RJ45", "Number of Ethernet"],
    "digital_inputs": ["Digital inputs", "DI", "Number of inputs"],
    "digital_outputs": ["Digital outputs", "DO", "Number of outputs"],
}

_GEARHEAD_CAPTIONS: dict[str, list[str]] = {
    "gear_ratio": ["Gear ratio", "Reduction ratio", "i", "Ratio"],
    "max_continuous_torque": ["Continuous torque", "Rated torque", "T_2N", "M_2N"],
    "max_peak_torque": ["Peak torque", "Maximum torque", "T_2max", "M_2B"],
    "input_shaft_diameter": ["Input shaft", "Motor shaft", "Bore", "mm"],
    "output_shaft_diameter": ["Output shaft", "Output Ø", "mm"],
    "max_input_speed": ["Max input speed", "n_1max", "rpm"],
    "backlash": ["Backlash", "j", "arcmin"],
    "ip_rating": ["Protection class", "IP rating"],
    "frame_size": ["Frame size", "Flange size"],
}

_ELECTRIC_CYLINDER_CAPTIONS: dict[str, list[str]] = {
    "stroke": ["Stroke length", "Travel", "mm"],
    "max_push_force": ["Push force", "Thrust", "N"],
    "max_pull_force": ["Pull force", "Retract force"],
    "continuous_force": ["Continuous force", "Rated force", "F_N"],
    "max_linear_speed": ["Linear speed", "Maximum speed", "mm/s"],
    "rated_voltage": ["Rated voltage", "Supply voltage", "VDC"],
    "rated_current": ["Rated current", "Nominal current"],
    "rated_power": ["Rated power", "P_N"],
    "lead_screw_pitch": ["Lead", "Pitch", "mm/rev"],
    "encoder_feedback_support": [
        "Encoder",
        "Feedback",
        "Position sensor",
        "Hall",
    ],
}

_LINEAR_ACTUATOR_CAPTIONS: dict[str, list[str]] = {
    "stroke": ["Stroke", "Travel length", "mm"],
    "max_work_load": ["Maximum payload", "Work load", "kg"],
    "max_push_force": ["Thrust force", "Push force", "N"],
    "max_linear_speed": ["Maximum speed", "v_max", "mm/s"],
    "max_acceleration": ["Maximum acceleration", "a_max", "mm/s²"],
    "lead_screw_pitch": ["Lead", "Pitch", "mm/rev"],
    "compatible_motor_mounts": ["Compatible motor", "Motor flange", "NEMA"],
    "encoder_feedback_support": ["Encoder", "Feedback"],
}

_CONTACTOR_CAPTIONS: dict[str, list[str]] = {
    "ie_ac3_400v": ["AC-3 / 400V", "Operational current", "I_e"],
    "motor_power_ac3_400v_kw": ["Motor power", "AC-3 / 400V", "kW"],
    "rated_operational_voltage_max": ["Operational voltage", "U_e", "V"],
    "coil_voltage_options": ["Coil voltage", "Control voltage"],
}

_CAPTIONS_BY_TYPE: Mapping[str, dict[str, list[str]]] = {
    "motor": _MOTOR_CAPTIONS,
    "drive": _DRIVE_CAPTIONS,
    "gearhead": _GEARHEAD_CAPTIONS,
    "electric_cylinder": _ELECTRIC_CYLINDER_CAPTIONS,
    "linear_actuator": _LINEAR_ACTUATOR_CAPTIONS,
    "contactor": _CONTACTOR_CAPTIONS,
}


def captions_for(product_type: str, field_name: str) -> list[str]:
    """Return the catalog phrases the LLM should hunt for, or an empty list."""
    return _CAPTIONS_BY_TYPE.get(product_type, {}).get(field_name, [])


# Per-product-type "common fields" — fields that almost always appear in
# the catalog. Verifier flags a probe when one of these is None on a
# first-pass extraction. Keep the list short — false-positive probes
# burn tokens for nothing.
COMMON_FIELDS: dict[str, frozenset[str]] = {
    "motor": frozenset(
        {
            "rated_voltage",
            "rated_current",
            "rated_torque",
            "rated_speed",
            "rated_power",
            "rotor_inertia",
        }
    ),
    "drive": frozenset(
        {
            "input_voltage",
            "rated_current",
            "rated_power",
        }
    ),
    "gearhead": frozenset(
        {
            "gear_ratio",
            "max_continuous_torque",
            "max_input_speed",
        }
    ),
    "electric_cylinder": frozenset(
        {
            "stroke",
            "max_push_force",
            "rated_voltage",
        }
    ),
    "linear_actuator": frozenset(
        {
            "stroke",
            "max_linear_speed",
        }
    ),
    "contactor": frozenset(
        {
            "ie_ac3_400v",
            "rated_operational_voltage_max",
        }
    ),
}


def common_fields_for(product_type: str) -> frozenset[str]:
    """Return the small set of "almost always populated" fields for a type."""
    return COMMON_FIELDS.get(product_type, frozenset())
