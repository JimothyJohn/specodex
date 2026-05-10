"""Device-relations layer for cross-product compatibility queries.

Powers `/api/v1/relations/*` endpoints (planned, separate PR) and admin
CLI flows that ask "which motors fit this actuator", "which drives can
run this motor", "which gearheads accept this motor frame".

The compatibility predicates here read Pydantic model fields directly,
so schema drift surfaces at type-check time. See `todo/SCHEMA.md` Part 3
for the design rationale and Phase 1 for the bridge fields
(`motor_mount_pattern`, `compatible_motor_mounts`,
`input_motor_mount`) that make these queries typed instead of
string-comparison hacks.

Predicate philosophy: **exclude on missing data, not include**. If a
candidate record lacks a field the predicate needs, it is omitted from
the result rather than passed through. The compatibility query is meant
to be precise — "compatible motors" should never include "we don't
know if this is compatible" rows. Recall loss from incomplete records
is the right failure mode here; precision loss would put the wrong
hardware in a Build BOM.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Union

from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.drive import Drive
from specodex.models.electric_cylinder import ElectricCylinder
from specodex.models.gearhead import Gearhead
from specodex.models.linear_actuator import LinearActuator
from specodex.models.motor import Motor


ActuatorLike = Union[LinearActuator, ElectricCylinder]


# ---------------------------------------------------------------------------
# Internal predicates. All return False on any None / missing-unit input
# per the "exclude on missing data" rule above.
# ---------------------------------------------------------------------------


def _value_in_range(value: Optional[ValueUnit], rng: Optional[MinMaxUnit]) -> bool:
    """True if `value` falls within `rng` [min, max] (unit-aware).

    Both endpoints of `rng` are optional: an open-ended range
    (min=None, max=120) means "anything ≤ 120 in unit"; (min=24, max=None)
    means "≥ 24 in unit". Units must match — the Pydantic
    BeforeValidators on VoltageRange / Current normalise to canonical
    units on construction, so a unit mismatch here is a genuine
    incompatibility, not a representation bug.
    """
    if value is None or rng is None:
        return False
    if value.value is None or value.unit is None or rng.unit is None:
        return False
    if value.unit != rng.unit:
        return False
    if rng.min is not None and value.value < rng.min:
        return False
    if rng.max is not None and value.value > rng.max:
        return False
    return True


def _range_within(inner: Optional[MinMaxUnit], outer: Optional[MinMaxUnit]) -> bool:
    """True if `inner` is fully contained in `outer` (unit-aware).

    Used for voltage matching: a motor rated for 200-240V must run on a
    drive whose input range covers that whole span. A drive with input
    480-480V doesn't cover a 200-240V motor even though the motor's
    high end is below the drive's low end.
    """
    if inner is None or outer is None:
        return False
    if inner.unit is None or outer.unit is None or inner.unit != outer.unit:
        return False
    # If `inner` has no min, treat as -inf for the lower-bound check; same
    # for max. This keeps single-point voltages (min==max) and one-sided
    # ranges from short-circuiting incorrectly.
    if inner.min is not None:
        if outer.min is not None and inner.min < outer.min:
            return False
    if inner.max is not None:
        if outer.max is not None and inner.max > outer.max:
            return False
    # Both inner endpoints None → there is no voltage info to check; the
    # outer dictates nothing. Conservative: reject as missing-data.
    if inner.min is None and inner.max is None:
        return False
    return True


def _value_gte(a: Optional[ValueUnit], b: Optional[ValueUnit]) -> bool:
    """True if `a >= b` (unit-aware). Any None / unit mismatch returns False."""
    if a is None or b is None:
        return False
    if a.value is None or b.value is None:
        return False
    if a.unit is None or b.unit is None or a.unit != b.unit:
        return False
    return a.value >= b.value


def _shaft_compatible(
    motor_shaft: Optional[ValueUnit], gearhead_input: Optional[ValueUnit]
) -> bool:
    """Shafts are compatible if diameters match within 0.1mm tolerance.

    Real bushing tolerances are tighter (typically H7/h6 fits on the
    order of 0.01mm), but vendor catalogs round inconsistently
    ("10mm" vs "10.0mm" vs "9.95mm"). 0.1mm is the pragmatic threshold
    for a catalog-level pre-filter — final fit is checked against
    actual mechanical drawings, not this predicate.
    """
    if motor_shaft is None or gearhead_input is None:
        return False
    if motor_shaft.value is None or gearhead_input.value is None:
        return False
    if motor_shaft.unit is None or gearhead_input.unit is None:
        return False
    if motor_shaft.unit != gearhead_input.unit:
        return False
    return abs(motor_shaft.value - gearhead_input.value) <= 0.1


def _encoder_protocol_intersect(motor: Motor, drive: Drive) -> bool:
    """True if motor's encoder protocol is in drive's supported list.

    Schema is mid-harmonisation (todo/SCHEMA.md Phase 1.1, also touched
    by the in-flight DOUBLE_TAP work): on master, motor side is
    ``Optional[str]`` and drive side is ``Optional[List[str]]``. The
    DOUBLE_TAP branch promotes motor to a structured ``EncoderFeedback``
    with a ``.protocol`` attribute. Tolerate both shapes here so this
    predicate doesn't have to change when DOUBLE_TAP merges.

    Drive side is always a list (or None) — easy to test membership
    against. Motor side is the string OR the protocol attribute on the
    structured model.
    """
    motor_enc = motor.encoder_feedback_support
    drive_protocols = drive.encoder_feedback_support
    if motor_enc is None or drive_protocols is None:
        return False
    if isinstance(motor_enc, str):
        motor_proto: Optional[str] = motor_enc
    else:
        motor_proto = getattr(motor_enc, "protocol", None)
    if motor_proto is None:
        return False
    return motor_proto in drive_protocols


# ---------------------------------------------------------------------------
# Public compatibility queries.
# ---------------------------------------------------------------------------


def compatible_motors(
    actuator: ActuatorLike,
    motor_db: Iterable[Motor],
    *,
    min_torque: Optional[ValueUnit] = None,
    min_speed: Optional[ValueUnit] = None,
) -> List[Motor]:
    """Motors that mount on `actuator` and meet optional torque/speed floor.

    Mount matching:
    - LinearActuator: motor's frame must be in `compatible_motor_mounts`
      (a list — actuators often accept multiple frames via adapter plates).
    - ElectricCylinder: motor's frame must equal `motor_mount_pattern`
      (single-valued — cylinders are typically one integrated package).

    Returns empty list if the actuator has no mount info on file. The
    compatibility query is precise; "show every motor" is the wrong
    fallback when the actuator's mount pattern hasn't been ingested.
    """
    if isinstance(actuator, LinearActuator):
        mounts = set(actuator.compatible_motor_mounts or [])
    else:  # ElectricCylinder
        mounts = (
            {actuator.motor_mount_pattern} if actuator.motor_mount_pattern else set()
        )
    if not mounts:
        return []

    out: List[Motor] = []
    for m in motor_db:
        if m.motor_mount_pattern is None or m.motor_mount_pattern not in mounts:
            continue
        if min_torque is not None and not _value_gte(m.rated_torque, min_torque):
            continue
        if min_speed is not None and not _value_gte(m.rated_speed, min_speed):
            continue
        out.append(m)
    return out


def compatible_drives(motor: Motor, drive_db: Iterable[Drive]) -> List[Drive]:
    """Drives whose voltage / current envelope covers this motor.

    A drive is compatible if all three hold:
    1. Drive's `input_voltage` range fully contains motor's `rated_voltage`
       range. Single-point motor voltages (min == max) are handled the
       same way — the inner range collapses to a point that must be in
       the outer.
    2. Drive's `rated_current` is at least the motor's `rated_current`
       (the drive can deliver continuous current the motor demands).
    3. Motor's encoder protocol appears in drive's supported list (the
       wire format matches). Devices behind the wire are the motor's
       problem, not the drive's.

    Returns empty list if motor lacks any of the three required fields.
    """
    if motor.rated_voltage is None or motor.rated_current is None:
        return []

    out: List[Drive] = []
    for d in drive_db:
        if not _range_within(motor.rated_voltage, d.input_voltage):
            continue
        if not _value_gte(d.rated_current, motor.rated_current):
            continue
        if not _encoder_protocol_intersect(motor, d):
            continue
        out.append(d)
    return out


def compatible_gearheads(
    motor: Motor, gearhead_db: Iterable[Gearhead]
) -> List[Gearhead]:
    """Gearheads whose input mount + shaft accept this motor.

    A gearhead is compatible if both hold:
    1. Its `input_motor_mount` list contains the motor's
       `motor_mount_pattern`.
    2. Its `input_shaft_diameter` matches motor's `shaft_diameter`
       within 0.1mm tolerance (catalog-rounding pragmatic threshold —
       see `_shaft_compatible`).

    Returns empty list if motor lacks `motor_mount_pattern`. The
    compatibility query is precise; "show every gearhead" is the wrong
    fallback.
    """
    if motor.motor_mount_pattern is None:
        return []

    out: List[Gearhead] = []
    for g in gearhead_db:
        if not g.input_motor_mount:
            continue
        if motor.motor_mount_pattern not in g.input_motor_mount:
            continue
        if not _shaft_compatible(motor.shaft_diameter, g.input_shaft_diameter):
            continue
        out.append(g)
    return out
