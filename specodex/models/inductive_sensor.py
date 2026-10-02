"""Inductive proximity sensor Pydantic model.

Schema grounded in IEC 60947-5-2 vocabulary as published by SICK (IMB /
IME / IMA), Balluff (BES), Baumer (IFRM), Keyence (EV / EZ), Datalogic
(IS), Omron (E2E NEXT), Pepperl+Fuchs (NBB / NBN), Turck (Bi / Ni),
Telemecanique (OsiSense XS), Contrinex, ifm, Autonics and wenglor. See
inductive_sensor.md in this directory for sources and field-by-field
reasoning.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import Field

from specodex.models.common import (
    Current,
    Frequency,
    IpRating,
    Length,
    LenientValueUnit,
    TemperatureRange,
    Torque,
    Voltage,
    VoltageRange,
)
from specodex.models.product import ProductBase


class InductiveSensor(ProductBase):
    """Inductive proximity sensor — a non-contact switch that detects
    metallic targets by the damping of an oscillating field at its
    sensing face (IEC 60947-5-2).

    Covers switching sensors (DC 2/3/4-wire, AC and AC/DC 2-wire, NAMUR)
    and distance-proportional analog variants, in threaded-barrel, smooth
    barrel, rectangular, ring and slot housings.
    """

    product_type: Literal["inductive_sensor"] = "inductive_sensor"
    type: Optional[
        Literal[
            "cylindrical_threaded",
            "cylindrical_smooth",
            "rectangular",
            "ring",
            "slot",
        ]
    ] = Field(
        None,
        description=(
            "Housing form factor. Threaded metric barrels (M5…M30) are "
            "'cylindrical_threaded'; plain Ø3 / Ø4 / Ø6.5 barrels are "
            "'cylindrical_smooth'; block / cubic / flat-pack bodies are "
            "'rectangular'."
        ),
    )
    series: Optional[str] = None

    # --- Housing ---
    housing_size: Optional[str] = Field(
        None,
        description=(
            "Housing size designation, normalised: thread as 'M12' / 'M18' "
            "(drop the pitch — 'M18 x 1' → 'M18'), smooth barrels as "
            "'Ø6.5', rectangular bodies as the face in mm, e.g. '40x40'."
        ),
    )
    housing_length: Length = Field(
        None, description="Overall housing length along the sensing axis (mm)."
    )
    housing_material: Optional[str] = Field(
        None,
        description=(
            "Housing (body) material as printed, e.g. 'Nickel-plated brass', "
            "'Stainless steel V4A (1.4404)', 'PBT'."
        ),
    )
    sensing_face_material: Optional[str] = Field(
        None,
        description=(
            "Material of the active sensing face, e.g. 'PBT', 'LCP', "
            "'Stainless steel'. Distinct from the housing material."
        ),
    )

    # --- Sensing ---
    sensing_distance: Length = Field(
        None,
        description=(
            "Nominal (rated) sensing distance Sn (mm). Printed as 'Sensing "
            "range Sn', 'Rated operating distance', 'Nominal switching "
            "distance', 'Detecting distance' or 'Operating distance'. "
            "The headline figure — never the assured / setting distance."
        ),
    )
    assured_sensing_distance: Length = Field(
        None,
        description=(
            "Upper bound of the assured / safe / setting distance Sa (mm) "
            "— the gap at which switching is guaranteed across tolerance "
            "and temperature. Printed as 'Safe sensing range Sa', "
            "'Assured operating distance' or 'Setting distance'; for a "
            "range like '0 … 6.48 mm' store 6.48. Only when printed — do "
            "not compute it from Sn."
        ),
    )
    mounting: Optional[Literal["flush", "non_flush", "quasi_flush"]] = Field(
        None,
        description=(
            "Installation in metal. 'flush' = flush / shielded / "
            "embeddable; 'non_flush' = non-flush / not flush / unshielded "
            "/ non-shielded / non-embeddable; 'quasi_flush' = quasi-flush "
            "/ quasi-shielded / semi-flush."
        ),
    )
    switching_frequency: Frequency = Field(
        None,
        description=(
            "Maximum switching frequency, always in Hz (1.5 kHz → 1500 Hz). "
            "Also printed as 'Response frequency'. For AC/DC parts with "
            "two values, the DC value."
        ),
    )
    hysteresis: LenientValueUnit = Field(
        None,
        description=(
            "Maximum switching hysteresis / differential travel as a "
            "percentage of the sensing distance (unit '%'). For a range "
            "like '3 … 20 %' store the upper bound."
        ),
    )
    repeatability: LenientValueUnit = Field(
        None,
        description=(
            "Repeat accuracy / reproducibility, as printed — usually a "
            "percentage of the real sensing distance (unit '%')."
        ),
    )

    # --- Output ---
    output_type: Optional[
        Literal[
            "pnp",
            "npn",
            "pnp_npn",
            "push_pull",
            "two_wire",
            "namur",
            "analog_voltage",
            "analog_current",
        ]
    ] = Field(
        None,
        description=(
            "Output stage. 'pnp_npn' = selectable / auto-detecting PNP or "
            "NPN; 'two_wire' = polarity-free or polarised 2-wire load-in-"
            "series switch (DC, AC or AC/DC); 'namur' = EN 60947-5-6 "
            "2-wire current output; analog_* = distance-proportional "
            "0–10 V / 4–20 mA output."
        ),
    )
    output_function: Optional[Literal["no", "nc", "complementary", "programmable"]] = (
        Field(
            None,
            description=(
                "Switching function. 'no' = normally open / make; 'nc' = "
                "normally closed / break; 'complementary' = NO + NC antivalent "
                "(4-wire); 'programmable' = NO/NC selectable by wiring or "
                "IO-Link."
            ),
        )
    )
    wiring: Optional[
        Literal["dc_2_wire", "dc_3_wire", "dc_4_wire", "ac_2_wire", "ac_dc_2_wire"]
    ] = Field(None, description="Electrical wiring system.")
    io_link: Optional[bool] = Field(
        None, description="True when the part has an IO-Link interface."
    )

    # --- Electrical ---
    supply_voltage: VoltageRange = Field(
        None,
        description=(
            "Supply / operating voltage range (V). Printed as 'Supply "
            "voltage', 'Operating voltage UB', 'Power supply voltage'. "
            "Use the full operating range when both a rated and an "
            "operating range are printed."
        ),
    )
    max_load_current: Current = Field(
        None,
        description=(
            "Maximum continuous output (load) current. Printed as "
            "'Continuous current Ia', 'Rated operating current Ie', 'Max. "
            "output current', 'Control output … mA max.'."
        ),
    )
    min_load_current: Current = Field(
        None,
        description=(
            "Minimum load current needed for reliable switching — 2-wire "
            "sensors only (the '3' in 'Control output 3 to 100 mA')."
        ),
    )
    no_load_current: Current = Field(
        None,
        description=(
            "Supply current drawn with the output unloaded. Printed as "
            "'No-load current' or 'Current consumption'. 3- and 4-wire "
            "sensors; for 2-wire sensors use leakage_current instead."
        ),
    )
    leakage_current: Current = Field(
        None,
        description=(
            "Off-state current through the load. Printed as 'Leakage "
            "current', 'Residual current' or 'Off-state current Ir'."
        ),
    )
    voltage_drop: Voltage = Field(
        None,
        description=(
            "Maximum on-state voltage drop across the output. Printed as "
            "'Voltage drop Ud' or 'Residual voltage'."
        ),
    )
    short_circuit_protection: Optional[bool] = Field(
        None, description="True when the output is short-circuit protected."
    )
    reverse_polarity_protection: Optional[bool] = Field(
        None, description="True when the supply is reverse-polarity protected."
    )

    # --- Connection ---
    connection: Optional[
        Literal["cable", "connector_m8", "connector_m12", "pigtail", "terminals"]
    ] = Field(
        None,
        description=(
            "Electrical connection. 'pigtail' = short cable ending in a "
            "connector; 'terminals' = terminal compartment."
        ),
    )
    connector_pins: Optional[int] = Field(
        None, description="Pin count of the connector (3 or 4), when it has one."
    )
    cable_length: Length = Field(
        None, description="Length of the attached cable or pigtail (m)."
    )

    # --- Environmental / mechanical ---
    operating_temp: TemperatureRange = Field(
        None, description="Ambient operating temperature range (°C)."
    )
    ip_rating: IpRating = Field(
        None,
        description=(
            "Highest plain-numeric IP rating claimed (e.g. 67, 68). Stated "
            "as 'Enclosure rating', 'Degree of protection', 'Protection "
            "degree'. Not the electrical protection class (II / III)."
        ),
    )
    protection_ratings: Optional[List[str]] = Field(
        None,
        description=(
            "Every ingress rating listed, as printed — e.g. ['IP67', "
            "'IP68', 'IP69K']. Carries the lettered ratings ip_rating "
            "cannot."
        ),
    )
    tightening_torque: Torque = Field(
        None, description="Maximum tightening torque of the mounting nuts (Nm)."
    )
    certifications: Optional[List[str]] = Field(
        None,
        description="Marks and approvals: CE, cULus, UKCA, CCC, ATEX, Ecolab.",
    )
