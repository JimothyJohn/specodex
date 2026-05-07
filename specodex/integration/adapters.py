"""Adapters: product model → dict of named ports.

Each adapter is a pure function that reads attributes from an existing
product model and returns the ports it exposes. No fields are added to
the source models — integration is a computed view.

Port names are deliberately symmetric across products so a compat
engine can pair them without hardcoded rules:

    motor.power_input   ↔  drive.motor_output
    motor.shaft_output  ↔  gearhead.shaft_input
    motor.feedback      ↔  drive.feedback
    motor.power_input   ↔  contactor.load_output
"""

from __future__ import annotations

from typing import Callable, Dict

from specodex.integration.ports import (
    CoilPort,
    ElectricalPowerPort,
    FeedbackPort,
    FieldbusPort,
    MechanicalShaftPort,
    Port,
)
from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.contactor import Contactor
from specodex.models.drive import Drive
from specodex.models.electric_cylinder import ElectricCylinder
from specodex.models.gearhead import Gearhead
from specodex.models.motor import Motor
from specodex.models.product import ProductBase
from specodex.models.robot_arm import RobotArm


def _motor_ports(m: Motor) -> Dict[str, Port]:
    return {
        "power_input": ElectricalPowerPort(
            direction="input",
            voltage=m.rated_voltage,
            current=m.rated_current,
            power=m.rated_power,
            ac_dc=_guess_ac_dc_from_motor_type(m.type),
        ),
        "shaft_output": MechanicalShaftPort(
            direction="output",
            shaft_diameter=m.shaft_diameter,
            frame_size=m.frame_size,
            max_speed=m.max_speed,
            rated_torque=m.rated_torque,
            peak_torque=m.peak_torque,
        ),
        "feedback": FeedbackPort(
            direction="output",
            provides=m.encoder_feedback_support,
        ),
    }


def _drive_ports(d: Drive) -> Dict[str, Port]:
    first_freq = d.input_voltage_frequency[0] if d.input_voltage_frequency else None
    phases = d.input_voltage_phases[0] if d.input_voltage_phases else None
    protocols = [p.value if hasattr(p, "value") else str(p) for p in (d.fieldbus or [])]
    return {
        "mains_input": ElectricalPowerPort(
            direction="input",
            voltage=d.input_voltage,
            frequency=first_freq,
            phases=phases,
            ac_dc="ac",
        ),
        "motor_output": ElectricalPowerPort(
            direction="output",
            # The drive reproduces its input voltage at the motor side
            # modulated by PWM; for compatibility we treat input_voltage
            # as the envelope the motor must sit within.
            voltage=d.input_voltage,
            current=d.rated_current,
            power=d.rated_power,
            ac_dc="ac",
        ),
        "feedback": FeedbackPort(
            direction="input",
            supports=d.encoder_feedback_support,
        ),
        "fieldbus": FieldbusPort(
            direction="output",
            protocols=protocols or None,
        ),
    }


def _gearhead_ports(g: Gearhead) -> Dict[str, Port]:
    return {
        "shaft_input": MechanicalShaftPort(
            direction="input",
            shaft_diameter=g.input_shaft_diameter,
            frame_size=g.frame_size,
            max_speed=g.max_input_speed,
        ),
        "shaft_output": MechanicalShaftPort(
            direction="output",
            shaft_diameter=g.output_shaft_diameter,
            rated_torque=g.max_continuous_torque,
            peak_torque=g.max_peak_torque,
        ),
    }


def _contactor_ports(c: Contactor) -> Dict[str, Port]:
    return {
        "coil_input": CoilPort(
            direction="input",
            voltage_range=c.coil_voltage_range_ac or c.coil_voltage_range_dc,
            voltage_options=c.coil_voltage_options,
            ac_dc="ac"
            if c.coil_voltage_range_ac
            else ("dc" if c.coil_voltage_range_dc else None),
        ),
        "load_output": ElectricalPowerPort(
            direction="output",
            # Contactor switches anything up to its rated maximum — model
            # as a [0, max] range so a motor's operating range fits inside.
            voltage=_scalar_to_zero_max_range(c.rated_operational_voltage_max),
            current=c.ie_ac3_400v,
            power=c.motor_power_ac3_400v_kw,
            ac_dc="ac",
        ),
    }


def _electric_cylinder_ports(e: ElectricCylinder) -> Dict[str, Port]:
    protocols = [e.fieldbus] if e.fieldbus else None
    return {
        "power_input": ElectricalPowerPort(
            direction="input",
            voltage=e.rated_voltage,
            current=e.rated_current,
            power=e.rated_power,
        ),
        "feedback": FeedbackPort(
            direction="output",
            provides=e.encoder_feedback_support,
        ),
        "fieldbus": FieldbusPort(
            direction="output",
            protocols=protocols,
        ),
    }


def _robot_arm_ports(r: RobotArm) -> Dict[str, Port]:
    ctrl = r.controller
    protocols = (
        list(ctrl.communication_protocols)
        if ctrl and ctrl.communication_protocols
        else None
    )
    return {
        "mains_input": ElectricalPowerPort(
            direction="input",
            voltage=ctrl.power_source if ctrl else None,
            ac_dc="ac",
        ),
        "fieldbus": FieldbusPort(
            direction="output",
            protocols=protocols,
        ),
    }


def _scalar_to_zero_max_range(v: ValueUnit | None) -> MinMaxUnit | None:
    """Convert a ValueUnit scalar to a MinMaxUnit ``0-N`` range.

    Used where a product's spec is a *maximum capability* (e.g. a
    contactor's rated_operational_voltage_max) but the compat layer
    treats voltage as a range that must contain the demand.
    """
    if v is None:
        return None
    if isinstance(v, MinMaxUnit):
        return v
    return MinMaxUnit(min=0.0, max=v.value, unit=v.unit)


def _guess_ac_dc_from_motor_type(t: str | None) -> str | None:
    if t is None:
        return None
    if t.startswith("ac") or t == "permanent magnet":
        return "ac"
    if t.startswith("brushed") or t.startswith("brushless") or t == "hybrid":
        return "dc"
    return None


_DISPATCH: Dict[type, Callable[[ProductBase], Dict[str, Port]]] = {
    Motor: _motor_ports,
    Drive: _drive_ports,
    Gearhead: _gearhead_ports,
    Contactor: _contactor_ports,
    ElectricCylinder: _electric_cylinder_ports,
    RobotArm: _robot_arm_ports,
}


def ports_for(product: ProductBase) -> Dict[str, Port]:
    """Return the named integration ports for a product instance.

    Raises KeyError if the product type has no adapter.
    """
    adapter = _DISPATCH.get(type(product))
    if adapter is None:
        raise KeyError(
            f"No port adapter registered for {type(product).__name__} "
            f"(product_type={getattr(product, 'product_type', '?')})"
        )
    return adapter(product)
