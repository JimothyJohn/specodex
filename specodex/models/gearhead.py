# gearhead.py
# AI-generated comment:
# This module defines the Pydantic model for a gearhead, a mechanical device
# used to increase torque and reduce speed from a motor. It builds upon the
# ProductBase model to include specific technical attributes relevant to
# gearheads, such as gear ratio, torque ratings, and backlash. This structured
# approach ensures data consistency for gearhead products.

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field, model_validator

from specodex.models.common import (
    Force,
    Inertia,
    IpRating,
    Length,
    Speed,
    TemperatureRange,
    Torque,
    ValueUnit,
)
from specodex.models.product import ProductBase


class Gearhead(ProductBase):
    """
    A Pydantic model representing a gearhead.

    This model extends the ProductBase to include attributes specific to
    gearheads, which are crucial for engineering and selection processes.
    This model is pre-populated with defaults for the Sesame PHL series.
    """

    product_type: Literal["gearhead"] = "gearhead"

    @model_validator(mode="before")
    @classmethod
    def coerce_string_fields(cls, data: Any) -> Any:
        """Convert dict-stored fields to strings when the model expects str."""
        if isinstance(data, dict):
            for field_name in (
                "frame_size",
                "gear_type",
                "lubrication_type",
            ):
                val = data.get(field_name)
                if isinstance(val, dict):
                    # Convert {value: X, unit: Y} to "X Y" string
                    v = val.get("value", val.get("min", ""))
                    u = val.get("unit", "")
                    data[field_name] = f"{v} {u}".strip() if v else str(val)
        return data

    # --- Performance Specifications ---
    gear_ratio: Optional[float] = Field(
        None,
        description="The ratio of input speed to output speed (e.g., 10.0 for 10:1)",
    )
    gear_type: Optional[str] = Field(
        "helical planetary",
        description="Type of gear mechanism (e.g., 'Planetary', 'Spur', 'Helical')",
    )
    stages: Optional[int] = Field(
        None, description="Number of gear stages (e.g., 1 or 2)"
    )
    nominal_input_speed: Speed = Field(
        None, description="Nominal continuous input speed (e.g., in rpm)"
    )
    max_input_speed: Speed = Field(
        None, description="Maximum allowable input speed (e.g., in rpm)"
    )
    max_continuous_torque: Torque = Field(
        None, description="Nominal continuous output torque (T2N) (e.g., in Nm)"
    )
    max_peak_torque: Torque = Field(
        None, description="Emergency-stop / transient peak torque (T2NOT) (e.g., in Nm)"
    )
    # arcmin is neither a length nor any other family — keep generic.
    backlash: Optional[ValueUnit] = Field(
        None, description="Rotational lost motion (e.g., in arcminutes)"
    )
    efficiency: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Efficiency of the gearhead as a ratio (e.g., 0.97 for 97%)",
    )
    # Nm/arcmin is compound — keep generic.
    torsional_rigidity: Optional[ValueUnit] = Field(
        None, description="Torsional rigidity (e.g., in Nm/arcmin)"
    )
    rotor_inertia: Inertia = Field(
        None, description="Moment of inertia for the gearbox (e.g., in kg.cm²)"
    )
    # dB/dBA is its own beast — keep generic.
    noise_level: Optional[ValueUnit] = Field(
        None, description="Noise level at 1m distance (e.g., in dBA)"
    )

    # --- Mechanical Specifications ---
    frame_size: Optional[str] = Field(
        None, description="Gearbox frame size, corresponding to flange (e.g., 42, 60)"
    )
    input_shaft_diameter: Length = Field(
        None, description="Diameter of the input shaft (motor specific) (e.g., in mm)"
    )
    output_shaft_diameter: Length = Field(
        None, description="Diameter of the output shaft (e.g., in mm)"
    )
    max_radial_load: Force = Field(
        None, description="Maximum radial load (F2m) (e.g., in N)"
    )
    max_axial_load: Force = Field(
        None, description="Maximum axial load (F2ab) (e.g., in N)"
    )

    # --- Environmental & Service ---
    ip_rating: IpRating = Field(None, description="Ingress Protection (IP) rating")
    operating_temp: TemperatureRange = Field(
        None,
        description="Operating temperature range",
    )
    # Service life in hours — Time family not introduced; stay generic.
    service_life: Optional[ValueUnit] = Field(
        None, description="Expected service life (e.g., in hours)"
    )
    lubrication_type: Optional[str] = Field(
        "Synthetic Lubricant", description="Type of lubrication used"
    )
