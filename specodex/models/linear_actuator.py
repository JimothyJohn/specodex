from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import Field

from specodex.models.common import (
    Current,
    Force,
    Inertia,
    IpRating,
    Length,
    Mass,
    Power,
    TemperatureRange,
    Torque,
    ValueUnit,
    VoltageRange,
)
from specodex.models.encoder import EncoderFeedback
from specodex.models.product import ProductBase


class LinearActuator(ProductBase):
    """Rodless linear-motion module — carriage moving along a guided rail.

    Distinct from ``ElectricCylinder`` (which pushes/pulls from a rod tip):
    a linear actuator's payload rides on the body of the unit and travels
    along a guided rail or stage. Drive can be ball screw, lead screw,
    belt, or linear motor; many are sold motorless for pairing with the
    customer's servo.
    """

    product_type: Literal["linear_actuator"] = "linear_actuator"
    type: Optional[
        Literal[
            "linear_slide",
            "linear_stage",
            "rodless_screw",
            "rodless_belt",
            "lm_guide_actuator",
        ]
    ] = Field(None, description="Form factor of the linear-motion module.")
    series: Optional[str] = None

    # --- Linear motion specs ---
    stroke: Length = Field(None, description="Maximum linear travel (e.g., in mm)")
    max_work_load: Mass = Field(
        None,
        description="Maximum payload mass the carriage can move (e.g., in kg)",
    )
    max_push_force: Force = Field(
        None, description="Maximum thrust force on the carriage (e.g., in N)"
    )
    holding_force: Force = Field(
        None, description="Force exerted by an optional holding brake (e.g., in N)"
    )
    dynamic_load_rating: Force = Field(
        None,
        description="Dynamic load rating for bearing life calculations (e.g., in N)",
    )
    static_load_rating: Force = Field(
        None,
        description="Static load rating for bearing capacity (e.g., in N)",
    )
    # mm/s and mm/s^2 are compound — keep generic.
    max_linear_speed: Optional[ValueUnit] = Field(
        None, description="Maximum linear speed (e.g., in mm/s)"
    )
    max_acceleration: Optional[ValueUnit] = Field(
        None, description="Maximum linear acceleration (e.g., in mm/s²)"
    )
    positioning_repeatability: Length = Field(
        None, description="Repeatability of positioning (e.g., in mm)"
    )
    backlash: Optional[ValueUnit] = Field(
        None, description="Mechanical backlash (e.g., in mm or arcmin)"
    )

    # --- Drive / mechanical ---
    actuation_mechanism: Optional[
        Literal["ball_screw", "lead_screw", "belt", "linear_motor"]
    ] = Field(None, description="Primary drive mechanism for linear motion.")
    # mm/rev is compound — keep generic.
    lead_screw_pitch: Optional[ValueUnit] = Field(
        None, description="Lead screw pitch (e.g., in mm/rev)"
    )
    screw_diameter: Length = Field(
        None, description="Nominal diameter of the lead screw (e.g., in mm)"
    )
    static_allowable_moment_pitching: Torque = Field(
        None, description="Static allowable pitching moment (e.g., in Nm)"
    )
    static_allowable_moment_yawing: Torque = Field(
        None, description="Static allowable yawing moment (e.g., in Nm)"
    )
    static_allowable_moment_rolling: Torque = Field(
        None, description="Static allowable rolling moment (e.g., in Nm)"
    )
    rotor_inertia: Inertia = Field(
        None,
        description="Mass moment of inertia of the moving parts (e.g., in kg·cm²)",
    )

    # --- Integrated motor (optional) ---
    motor_type: Optional[Literal["step_motor", "servo_motor", "motorless"]] = Field(
        None,
        description=(
            "Type of integrated motor. 'motorless' for units sold without a "
            "motor (customer pairs their own servo)."
        ),
    )
    # Linear actuators commonly accept several feedback options (the
    # Lintech 200 takes both incremental and absolute feedback variants),
    # so this stays a list of structured EncoderFeedback values rather
    # than a single one.
    encoder_feedback_support: Optional[List[EncoderFeedback]] = Field(
        None, description="Types of encoder feedback supported."
    )
    rated_voltage: VoltageRange = Field(
        None, description="Rated input voltage (e.g., in V)"
    )
    rated_current: Current = Field(
        None, description="Rated electrical current (e.g., in A)"
    )
    peak_current: Current = Field(
        None, description="Peak electrical current (e.g., in A)"
    )
    rated_power: Power = Field(None, description="Rated electrical power (e.g., in W)")

    # --- Environmental & compliance ---
    ip_rating: IpRating = Field(None, description="Ingress Protection rating")
    operating_temp: TemperatureRange = Field(
        None, description="Operating temperature range"
    )
    # %RH is its own family — keep generic.
    operating_humidity_range: Optional[ValueUnit] = Field(
        None, description="Operating humidity range (e.g., in %RH)"
    )
    cleanroom_class: Optional[str] = Field(
        None, description="Cleanroom classification (e.g., 'ISO Class 5')"
    )
