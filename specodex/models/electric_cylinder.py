from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from specodex.models.common import (
    Current,
    Force,
    IpRating,
    Length,
    MotorMountPattern,
    Power,
    TemperatureRange,
    ValueUnit,
    VoltageRange,
)
from specodex.models.product import ProductBase


class ElectricCylinder(ProductBase):
    """Linear actuator with integrated motor — produces force (N), not torque (Nm).

    Covers products like Faulhaber L-series linear actuators that combine
    a motor, gearhead, and lead screw into a single unit producing linear
    motion. Key differentiator from motors: output is force/stroke, not
    torque/speed.
    """

    product_type: Literal["electric_cylinder"] = "electric_cylinder"
    type: Optional[
        Literal[
            "linear actuator",
            "linear servo",
            "micro linear actuator",
            "tubular linear motor",
        ]
    ] = None
    series: Optional[str] = None

    # --- Linear output specs ---
    stroke: Length = Field(None, description="Maximum linear travel (e.g., in mm)")
    max_push_force: Force = Field(
        None, description="Maximum push/extend force (e.g., in N)"
    )
    max_pull_force: Force = Field(
        None, description="Maximum pull/retract force (e.g., in N)"
    )
    continuous_force: Force = Field(
        None, description="Continuous rated force (e.g., in N)"
    )
    # mm/s is compound (length/time) — keep generic.
    max_linear_speed: Optional[ValueUnit] = Field(
        None, description="Maximum linear speed unloaded (e.g., in mm/s)"
    )
    positioning_repeatability: Length = Field(
        None, description="Repeatability of positioning (e.g., in mm)"
    )

    # --- Integrated motor specs ---
    rated_voltage: VoltageRange = Field(
        None, description="Rated input voltage (e.g., in V)"
    )
    rated_current: Current = Field(None, description="Rated current draw (e.g., in A)")
    peak_current: Current = Field(None, description="Peak current draw (e.g., in A)")
    rated_power: Power = Field(None, description="Rated motor power (e.g., in W)")
    motor_type: Optional[str] = Field(
        None,
        description="Type of integrated motor (e.g., 'brushless dc', 'brushed dc')",
    )
    # Motor mount pattern (typically just one — electric cylinders are
    # usually a single integrated package). Bridges to
    # Motor.motor_mount_pattern for cross-product queries.
    motor_mount_pattern: Optional[MotorMountPattern] = Field(
        None,
        description="Motor frame designator the cylinder mounts to (e.g. 'NEMA 23').",
    )

    # --- Mechanical ---
    # mm/rev is compound — keep generic.
    lead_screw_pitch: Optional[ValueUnit] = Field(
        None, description="Lead screw pitch (e.g., in mm/rev)"
    )
    backlash: Length = Field(None, description="Mechanical backlash (e.g., in mm)")
    max_radial_load: Force = Field(
        None, description="Maximum radial load on output shaft (e.g., in N)"
    )
    max_axial_load: Force = Field(
        None, description="Maximum static axial load (e.g., in N)"
    )

    # --- Feedback & control ---
    encoder_feedback_support: Optional[str] = Field(
        None, description="Encoder or position feedback type"
    )
    fieldbus: Optional[str] = Field(
        None, description="Communication interface (e.g., 'CANopen', 'RS-232')"
    )

    # --- Environmental ---
    ip_rating: IpRating = Field(None, description="Ingress Protection rating")
    operating_temp: TemperatureRange = Field(
        None, description="Operating temperature range"
    )
    # Time family not introduced — hours/cycles stay generic.
    service_life: Optional[ValueUnit] = Field(
        None, description="Expected service life (e.g., in hours or cycles)"
    )
    noise_level: Optional[ValueUnit] = Field(
        None, description="Noise level (e.g., in dBA)"
    )
