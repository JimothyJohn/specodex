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
    """Motor shaft fits if its diameter is at most the gearhead's input bore.

    ``input_shaft_diameter`` on a gearhead is the MAXIMUM motor shaft the
    input coupling accepts — precision-gearbox catalogs publish it as a
    clamping-bushing bound ("motor shaft ≤ 24 mm"), and bushing kits
    cover smaller shafts. So the check is `motor ≤ bore`, not equality.

    The 0.1mm grace absorbs catalog rounding ("10mm" vs "9.95mm") on the
    boundary; final fit is checked against actual mechanical drawings,
    not this predicate.
    """
    if motor_shaft is None or gearhead_input is None:
        return False
    if motor_shaft.value is None or gearhead_input.value is None:
        return False
    if motor_shaft.unit is None or gearhead_input.unit is None:
        return False
    if motor_shaft.unit != gearhead_input.unit:
        return False
    return motor_shaft.value <= gearhead_input.value + 0.1


def _meets_floor(value: Optional[ValueUnit], floor: float, unit: str) -> bool:
    """True if `value` is present, in canonical `unit`, and >= `floor`.

    Compares an actuator spec field against a numeric requirement floor
    already expressed in canonical units (the Build derivation engine
    emits mm / N / mm·s⁻¹). Typed family fields (`stroke`,
    `max_push_force`) normalise aliases to canonical on construction, so
    a unit other than `unit` means either a different physical quantity
    or an un-normalised generic field — excluded either way, per this
    module's precision-first rule.
    """
    if value is None or value.value is None or value.unit is None:
        return False
    if value.unit != unit:
        return False
    return value.value >= floor


def input_torque_capacity(g: Gearhead) -> Optional[ValueUnit]:
    """Continuous input-torque capacity of a gearhead, referred from output.

    Catalogs rate output torque (T2N); the motor cares about the input
    side. T_in = T2N / (ratio × efficiency). Efficiency defaults to 1.0
    when absent or out of (0, 1] — that UNDERSTATES capacity (real
    losses mean the input can actually push a bit more before the
    output hits T2N), so missing data errs toward excluding, per this
    module's precision-first rule.

    Returns None when `gear_ratio` or a positive `max_continuous_torque`
    is missing — callers exclude the row.
    """
    tq = g.max_continuous_torque
    if tq is None or tq.value is None or tq.unit is None:
        return None
    if g.gear_ratio is None or g.gear_ratio <= 0:
        return None
    eff = g.efficiency if g.efficiency is not None and 0 < g.efficiency <= 1 else 1.0
    return ValueUnit(value=tq.value / (g.gear_ratio * eff), unit=tq.unit)


def _gearhead_speed_ok(motor: Motor, g: Gearhead) -> bool:
    """Motor's rated speed must sit within the gearhead's input-speed rating.

    Prefers `max_input_speed` (n1B — the hard ceiling); falls back to
    `nominal_input_speed` (n1N — stricter, continuous rating) when the
    ceiling wasn't published. Missing both → excluded.
    """
    rating = (
        g.max_input_speed if g.max_input_speed is not None else g.nominal_input_speed
    )
    return _value_gte(rating, motor.rated_speed)


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


def compatible_actuators(
    actuator_db: Iterable[LinearActuator],
    *,
    min_stroke_mm: Optional[float] = None,
    min_peak_force_n: Optional[float] = None,
    min_peak_velocity_mm_s: Optional[float] = None,
) -> List[LinearActuator]:
    """Linear actuators meeting the requirement floors Build derives.

    The requirements-first entry point for Build's slot-fill sequence
    (`todo/BUILD.md` Part 4): given the numeric constraints Build's
    derivation engine produces from a motion application — minimum
    stroke, peak thrust, peak velocity — return the actuators whose
    ratings clear every supplied floor.

    Each floor is optional and independent. A `None` floor applies no
    constraint on that dimension (Build's "blank = no constraint
    applied" rule). A floor that *is* set excludes any actuator missing
    the corresponding field or carrying it in a non-canonical unit —
    precision over recall, consistent with the rest of this module:
    "compatible" must never include "we can't tell".

    Field mapping:
    - `min_stroke_mm`          → `stroke` (Length, canonical mm)
    - `min_peak_force_n`       → `max_push_force` (Force, canonical N —
      the carriage thrust rating)
    - `min_peak_velocity_mm_s` → `max_linear_speed` (generic mm/s)

    Orientation and duty cycle from the Build requirements are
    deliberately NOT actuator filters: orientation is a derating hint
    the downstream motor sizing consumes, and duty cycle is a
    motor-thermal concept with no actuator-rating analogue. They belong
    to the slots that can act on them, not here.
    """
    out: List[LinearActuator] = []
    for a in actuator_db:
        if min_stroke_mm is not None and not _meets_floor(
            a.stroke, min_stroke_mm, "mm"
        ):
            continue
        if min_peak_force_n is not None and not _meets_floor(
            a.max_push_force, min_peak_force_n, "N"
        ):
            continue
        if min_peak_velocity_mm_s is not None and not _meets_floor(
            a.max_linear_speed, min_peak_velocity_mm_s, "mm/s"
        ):
            continue
        out.append(a)
    return out


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
    """Gearheads whose input side accepts this motor as an ideal match.

    A gearhead is compatible if all four hold:
    1. Mount does not CONTRADICT: when both sides publish mount info,
       the gearhead's `input_motor_mount` list must contain the motor's
       `motor_mount_pattern`. When either side lacks it, the mount
       check is skipped — precision-gearbox vendors sell adapter
       plates instead of publishing NEMA-style patterns (only ~5% of
       ingested gearheads carry a list, 2026-07-25), so treating a
       missing list as a mismatch would exclude nearly the whole
       catalog on a field the vendor never states. This is a
       deliberate, documented exception to the module's
       exclude-on-missing rule; the physical checks below stay hard.
    2. The motor's `shaft_diameter` fits within its
       `input_shaft_diameter` (max-bore semantics, 0.1mm rounding
       grace — see `_shaft_compatible`).
    3. The motor's `rated_speed` sits within the gearhead's input-speed
       rating (`max_input_speed`, falling back to
       `nominal_input_speed` — see `_gearhead_speed_ok`).
    4. The motor's `rated_torque` does not exceed the gearhead's
       continuous input-torque capacity, referred from output torque
       through ratio and efficiency (see `input_torque_capacity`).

    Returns empty list if the motor lacks any of `shaft_diameter`,
    `rated_speed`, or `rated_torque` — mirroring `compatible_drives`'
    treatment of required motor fields.
    """
    if (
        motor.shaft_diameter is None
        or motor.rated_speed is None
        or motor.rated_torque is None
    ):
        return []

    out: List[Gearhead] = []
    for g in gearhead_db:
        if (
            motor.motor_mount_pattern is not None
            and g.input_motor_mount
            and motor.motor_mount_pattern not in g.input_motor_mount
        ):
            continue
        if not _shaft_compatible(motor.shaft_diameter, g.input_shaft_diameter):
            continue
        if not _gearhead_speed_ok(motor, g):
            continue
        if not _value_gte(input_torque_capacity(g), motor.rated_torque):
            continue
        out.append(g)
    return out


def _fmt(v: Optional[ValueUnit]) -> str:
    if v is None or v.value is None:
        return "?"
    return f"{v.value:g} {v.unit}" if v.unit else f"{v.value:g}"


def _length_mismatch(
    a: Optional[ValueUnit], b: Optional[ValueUnit], tolerance: float = 0.1
) -> bool:
    """True only when both sides are known, same unit, and differ by more
    than `tolerance` (catalog-rounding grace, same as `_shaft_compatible`).
    Missing data or a unit mismatch is NOT a claimed conflict — this
    module's exclude-on-missing rule applies to what gets reported, too.
    """
    if a is None or b is None:
        return False
    if a.value is None or b.value is None:
        return False
    if a.unit is None or b.unit is None or a.unit != b.unit:
        return False
    return abs(a.value - b.value) > tolerance


def mounting_conflicts(motor: Motor, gearhead: Gearhead) -> List[str]:
    """Human-readable mounting-geometry mismatches between a motor and a
    gearhead's input flange.

    Unlike `compatible_gearheads` (which filters silently), this reports
    WHY two specific parts don't fit — intended for a build/BOM UI
    surfacing explicit conflict reasons (see `todo/BUILD.md` Part 4's
    planned `_warnings` response field; not wired to the relations API
    yet — that's Build's job). Each string names the dimension and both
    sides' values, matching the style already used client-side in
    `app/frontend/src/utils/compat.ts`'s `detail` strings.

    Only compares a dimension when BOTH sides have it — missing data is
    never reported as a conflict, per this module's exclude-on-missing
    philosophy. An empty return means "no *known* conflict", which may
    mean compatible OR insufficient data; callers needing to
    distinguish those should check the relevant fields directly.

    Deliberately NOT checked (schema doesn't support it yet):
    - `shaft_length` vs. anything on Gearhead — `Gearhead` has no field
      for input-coupling engagement depth. `input_mounting_flange.
      pilot_depth` is a different physical dimension (flange register
      depth, for centering) and comparing shaft length against it would
      be a category error, not an approximation.
    - `shaft_modification` — `Gearhead` has no "required shaft
      modification" field to compare against.
    """
    conflicts: List[str] = []

    if motor.shaft_diameter is not None and gearhead.input_shaft_diameter is not None:
        if not _shaft_compatible(motor.shaft_diameter, gearhead.input_shaft_diameter):
            conflicts.append(
                f"shaft diameter: motor {_fmt(motor.shaft_diameter)} exceeds "
                f"gearhead input bore {_fmt(gearhead.input_shaft_diameter)}"
            )

    if (
        motor.motor_mount_pattern is not None
        and gearhead.input_motor_mount
        and motor.motor_mount_pattern not in gearhead.input_motor_mount
    ):
        conflicts.append(
            f"mount pattern: motor {motor.motor_mount_pattern!r} not in gearhead "
            f"input_motor_mount {sorted(gearhead.input_motor_mount)}"
        )

    mf = motor.mounting_flange
    gf = gearhead.input_mounting_flange
    if mf is not None and gf is not None:
        if (
            mf.mounting_standard is not None
            and gf.mounting_standard is not None
            and mf.mounting_standard != gf.mounting_standard
        ):
            conflicts.append(
                f"mounting standard: motor {mf.mounting_standard} vs gearhead "
                f"input {gf.mounting_standard}"
            )
        if _length_mismatch(mf.bolt_circle_diameter, gf.bolt_circle_diameter):
            conflicts.append(
                f"bolt circle diameter: motor {_fmt(mf.bolt_circle_diameter)} vs "
                f"gearhead input {_fmt(gf.bolt_circle_diameter)}"
            )
        if _length_mismatch(mf.bolt_hole_diameter, gf.bolt_hole_diameter):
            conflicts.append(
                f"bolt hole diameter: motor {_fmt(mf.bolt_hole_diameter)} vs "
                f"gearhead input {_fmt(gf.bolt_hole_diameter)}"
            )
        if (
            mf.bolt_hole_count is not None
            and gf.bolt_hole_count is not None
            and mf.bolt_hole_count != gf.bolt_hole_count
        ):
            conflicts.append(
                f"bolt hole count: motor {mf.bolt_hole_count} vs gearhead input "
                f"{gf.bolt_hole_count}"
            )
        # Register/spigot fit — treated as an equality check like bolt
        # circle diameter. Vendors differ on which side is male/female,
        # so this doesn't assume a shaft-into-bushing "motor <= bore"
        # relationship the way `_shaft_compatible` does.
        if _length_mismatch(mf.pilot_diameter, gf.pilot_diameter):
            conflicts.append(
                f"pilot diameter: motor {_fmt(mf.pilot_diameter)} vs gearhead "
                f"input {_fmt(gf.pilot_diameter)}"
            )

    return conflicts
