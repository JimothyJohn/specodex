"""Pairwise compatibility between products via their ports.

Compares ports of the same ``kind`` and opposite ``direction``. Each
field check returns one of:

    ok      — both sides populated and the values agree
    partial — one side missing the field (can't prove a mismatch)
    fail    — both populated and the values disagree

A port pair's overall result is ``fail`` if any check failed, otherwise
``partial`` if any was partial, otherwise ``ok``. The report surfaces
per-field detail so a UI can highlight exactly which spec didn't match.

Unit-aware numeric parsing reuses ``specodex.units`` so a motor
rated "2;kW" and a drive rated "2000;W" compare equal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from specodex.integration.adapters import ports_for
from specodex.integration.ports import (
    ElectricalPowerPort,
    FeedbackPort,
    FieldbusPort,
    MechanicalShaftPort,
)
from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.encoder import EncoderFeedback, feedback_subsumes
from specodex.models.product import ProductBase


CheckStatus = Literal["ok", "partial", "fail"]


@dataclass
class CheckResult:
    """One field-level comparison between two ports."""

    field: str
    status: CheckStatus
    detail: str = ""


@dataclass
class CompatResult:
    """Result for one port pair (e.g. drive.motor_output ↔ motor.power_input)."""

    from_port: str
    to_port: str
    status: CheckStatus
    checks: List[CheckResult] = field(default_factory=list)


@dataclass
class CompatibilityReport:
    """Full report for a product pair."""

    from_type: str
    to_type: str
    status: CheckStatus
    results: List[CompatResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Value parsing — port fields are already canonical-unit normalised at
# Pydantic validation time, so we only need to split into floats + unit.
# ---------------------------------------------------------------------------


def _scalar(v: Any) -> Optional[Tuple[float, str]]:
    """Pull (value, unit) from a ValueUnit or single-bound MinMaxUnit."""
    if isinstance(v, ValueUnit):
        return v.value, v.unit
    if isinstance(v, MinMaxUnit):
        scalar = v.min if v.min is not None else v.max
        if scalar is None:
            return None
        return scalar, v.unit
    return None


def _range(v: Any) -> Optional[Tuple[float, float, str]]:
    """Pull (min, max, unit) from a MinMaxUnit, or (v, v, unit) from a ValueUnit."""
    if isinstance(v, MinMaxUnit):
        lo = v.min if v.min is not None else v.max
        hi = v.max if v.max is not None else v.min
        if lo is None or hi is None:
            return None
        return lo, hi, v.unit
    if isinstance(v, ValueUnit):
        return v.value, v.value, v.unit
    return None


# ---------------------------------------------------------------------------
# Field-level checks
# ---------------------------------------------------------------------------


def _check_voltage_fits(
    supply: Any, demand: Any, field_name: str = "voltage"
) -> CheckResult:
    """Supply side offers a range (or single value); demand must fit inside.

    Used for drive output → motor rated, contactor load → motor rated.
    """
    s = _range(supply)
    d = _range(demand)
    if s is None or d is None:
        return CheckResult(field_name, "partial", "one side missing voltage")
    if s[2] != d[2]:
        return CheckResult(field_name, "fail", f"unit mismatch: {s[2]} vs {d[2]}")
    if d[0] < s[0] or d[1] > s[1]:
        return CheckResult(
            field_name,
            "fail",
            f"demand {d[0]}-{d[1]} outside supply {s[0]}-{s[1]} {s[2]}",
        )
    return CheckResult(field_name, "ok", f"{d[0]}-{d[1]} within {s[0]}-{s[1]} {s[2]}")


def _check_supply_ge_demand(supply: Any, demand: Any, field_name: str) -> CheckResult:
    """Supply must be ≥ demand (e.g. drive rated_current ≥ motor rated_current)."""
    s = _scalar(supply)
    d = _scalar(demand)
    if s is None or d is None:
        return CheckResult(field_name, "partial", f"one side missing {field_name}")
    if s[1] != d[1]:
        return CheckResult(field_name, "fail", f"unit mismatch: {s[1]} vs {d[1]}")
    if s[0] < d[0]:
        return CheckResult(field_name, "fail", f"supply {s[0]} < demand {d[0]} {s[1]}")
    return CheckResult(field_name, "ok", f"supply {s[0]} ≥ demand {d[0]} {s[1]}")


def _check_demand_le_max(demand: Any, maximum: Any, field_name: str) -> CheckResult:
    return _check_supply_ge_demand(maximum, demand, field_name)


def _check_equal_str(
    a: Optional[str], b: Optional[str], field_name: str
) -> CheckResult:
    if a is None or b is None:
        return CheckResult(field_name, "partial", f"one side missing {field_name}")
    if a.strip().lower() != b.strip().lower():
        return CheckResult(field_name, "fail", f"{a!r} != {b!r}")
    return CheckResult(field_name, "ok", a)


def _check_membership(
    value: Optional[str], options: Optional[List[str]], field_name: str
) -> CheckResult:
    if value is None or not options:
        return CheckResult(field_name, "partial", "one side missing")
    v_norm = value.strip().lower()
    if any(v_norm == o.strip().lower() for o in options):
        return CheckResult(field_name, "ok", f"{value} in supported list")
    return CheckResult(field_name, "fail", f"{value} not in {options}")


def _check_intersect(
    a: Optional[List[str]], b: Optional[List[str]], field_name: str
) -> CheckResult:
    if not a or not b:
        return CheckResult(field_name, "partial", "one side missing")
    a_set = {x.strip().lower() for x in a}
    b_set = {x.strip().lower() for x in b}
    common = a_set & b_set
    if common:
        return CheckResult(field_name, "ok", f"shared: {sorted(common)}")
    return CheckResult(field_name, "fail", f"no overlap between {a} and {b}")


def _check_shaft_fit(motor_shaft: Any, gearhead_bore: Any) -> CheckResult:
    """Motor shaft OD must equal gearhead bore within 0.1 mm.

    Equality rather than "shaft ≤ bore" because motor shafts couple via
    keyed/clamped bores — a mismatch of any size is the wrong part.
    """
    m = _scalar(motor_shaft)
    g = _scalar(gearhead_bore)
    if m is None or g is None:
        return CheckResult("shaft_diameter", "partial", "one side missing")
    if m[1] != g[1]:
        return CheckResult("shaft_diameter", "fail", f"unit mismatch: {m[1]} vs {g[1]}")
    if abs(m[0] - g[0]) > 0.1:
        return CheckResult(
            "shaft_diameter",
            "fail",
            f"motor {m[0]} {m[1]} ≠ gearhead bore {g[0]} {g[1]}",
        )
    return CheckResult("shaft_diameter", "ok", f"{m[0]} {m[1]} matches bore")


# ---------------------------------------------------------------------------
# Per-kind comparators
# ---------------------------------------------------------------------------


def _compare_electrical_power(
    supply: ElectricalPowerPort, demand: ElectricalPowerPort
) -> List[CheckResult]:
    checks: List[CheckResult] = [
        _check_voltage_fits(supply.voltage, demand.voltage),
        _check_supply_ge_demand(supply.current, demand.current, "current"),
        _check_supply_ge_demand(supply.power, demand.power, "power"),
    ]
    if supply.ac_dc and demand.ac_dc:
        checks.append(
            CheckResult(
                "ac_dc",
                "ok" if supply.ac_dc == demand.ac_dc else "fail",
                f"{supply.ac_dc} vs {demand.ac_dc}",
            )
        )
    return checks


def _compare_mechanical_shaft(
    source: MechanicalShaftPort, sink: MechanicalShaftPort
) -> List[CheckResult]:
    """Source = motor output, sink = gearhead input."""
    return [
        _check_equal_str(source.frame_size, sink.frame_size, "frame_size"),
        _check_shaft_fit(source.shaft_diameter, sink.shaft_diameter),
        _check_demand_le_max(source.max_speed, sink.max_speed, "speed"),
    ]


def _compare_feedback(source: FeedbackPort, sink: FeedbackPort) -> List[CheckResult]:
    """Motor (provides one EncoderFeedback) ↔ drive (supports list of protocols).

    Resolution order:
        1. Identify which side has ``provides`` populated — that's the
           motor side. Drive carries the protocol list in ``supports``.
        2. If the motor side's encoder has a ``protocol``, check it
           against the drive's supported list (with the SUBSUMES
           widening: EnDat 2.2 in supports accepts EnDat 2.1 motor).
        3. Without a protocol on the motor side, we can't safely conclude
           compatibility — return ``partial`` rather than fabricate.
    """
    motor_side = source if source.provides is not None else sink
    drive_side = sink if motor_side is source else source

    provided: EncoderFeedback | None = motor_side.provides
    supported = drive_side.supports

    if provided is None or not supported:
        return [CheckResult("encoder_type", "partial", "one side missing")]

    if provided.protocol is None:
        # We have a structured device but no wire protocol — can't
        # verify the drive accepts it. Surface as partial with the
        # raw text if present so the UI can hint what's missing.
        hint = provided.raw or provided.device
        return [
            CheckResult(
                "encoder_type",
                "partial",
                f"motor encoder has no protocol set (raw={hint!r})",
            )
        ]

    for accepted in supported:
        if feedback_subsumes(accepted, provided):
            return [
                CheckResult(
                    "encoder_type",
                    "ok",
                    f"motor protocol {provided.protocol} ∈ drive support",
                )
            ]
    return [
        CheckResult(
            "encoder_type",
            "fail",
            f"motor protocol {provided.protocol} not in drive support {sorted(supported)}",
        )
    ]


def _compare_fieldbus(source: FieldbusPort, sink: FieldbusPort) -> List[CheckResult]:
    return [_check_intersect(source.protocols, sink.protocols, "fieldbus")]


_COMPARATORS: dict[str, Callable[..., List[CheckResult]]] = {
    "electrical_power": _compare_electrical_power,
    "mechanical_shaft": _compare_mechanical_shaft,
    "feedback": _compare_feedback,
    "fieldbus": _compare_fieldbus,
}


# ---------------------------------------------------------------------------
# Roll-up + top-level entry point
# ---------------------------------------------------------------------------


def _roll_up(checks: List[CheckResult]) -> CheckStatus:
    if any(c.status == "fail" for c in checks):
        return "fail"
    if any(c.status == "partial" for c in checks):
        return "partial"
    return "ok"


def _soften(report: CompatibilityReport) -> CompatibilityReport:
    """Downgrade every `fail` to `partial` while keeping per-field detail.

    Used by the API layer until cross-product schemas (fieldbus
    protocols, encoder names) are normalised. Detail strings still
    record what mismatched so the UI can surface them as warnings.
    """
    softened_results: List[CompatResult] = []
    for r in report.results:
        new_checks = [
            CheckResult(
                field=c.field,
                status="partial" if c.status == "fail" else c.status,
                detail=c.detail,
            )
            for c in r.checks
        ]
        softened_results.append(
            CompatResult(
                from_port=r.from_port,
                to_port=r.to_port,
                status=_roll_up(new_checks),
                checks=new_checks,
            )
        )
    overall = (
        _roll_up([CheckResult("pair", r.status) for r in softened_results])
        if softened_results
        else "partial"
    )
    return CompatibilityReport(
        from_type=report.from_type,
        to_type=report.to_type,
        status=overall,
        results=softened_results,
    )


def check(
    a: ProductBase, b: ProductBase, *, strict: bool = True
) -> CompatibilityReport:
    """Check compatibility between two products end-to-end.

    Pairs every output port on A with a matching input port on B (and
    vice versa) and reports per-pair status.

    When ``strict=False`` (fits-partial mode) any ``fail`` is downgraded
    to ``partial``. The per-field detail is preserved so the UI can still
    show *which* spec didn't line up, just without gating selection on it.
    """
    a_ports = ports_for(a)
    b_ports = ports_for(b)
    a_name = type(a).__name__
    b_name = type(b).__name__

    pair_results: List[CompatResult] = []
    for a_port_name, a_port in a_ports.items():
        for b_port_name, b_port in b_ports.items():
            if a_port.kind != b_port.kind:
                continue
            if {a_port.direction, b_port.direction} != {"input", "output"}:
                continue

            a_is_source = a_port.direction == "output"
            source, sink = (a_port, b_port) if a_is_source else (b_port, a_port)
            src_label = (
                f"{a_name}.{a_port_name}" if a_is_source else f"{b_name}.{b_port_name}"
            )
            sink_label = (
                f"{b_name}.{b_port_name}" if a_is_source else f"{a_name}.{a_port_name}"
            )

            comparator = _COMPARATORS.get(source.kind)
            if comparator is None:
                continue
            checks = comparator(source, sink)

            pair_results.append(
                CompatResult(
                    from_port=src_label,
                    to_port=sink_label,
                    status=_roll_up(checks),
                    checks=checks,
                )
            )

    if not pair_results:
        overall: CheckStatus = "partial"
    else:
        overall = _roll_up([CheckResult("pair", r.status) for r in pair_results])

    report = CompatibilityReport(
        from_type=a.product_type,
        to_type=b.product_type,
        status=overall,
        results=pair_results,
    )
    return report if strict else _soften(report)
