"""Relations route — device compatibility queries.

Mirrors the Express ``relations.ts`` shape but reuses
``specodex.relations`` directly. The Python module is the single
source of truth for the predicates (SCHEMA Phase 3a); this file
is the FastAPI wrapper.

Endpoints:
- GET /api/v1/relations/actuators?min_stroke_mm=...&...
- GET /api/v1/relations/motors-for-actuator?id=<uuid>&type=...
- GET /api/v1/relations/drives-for-motor?id=<uuid>
- GET /api/v1/relations/gearheads-for-motor?id=<uuid>
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.backend_py.src.db.dynamodb import BackendDB
from specodex.config import SCHEMA_CHOICES
from specodex.models.linear_actuator import LinearActuator
from specodex.models.motor import Motor
from specodex.relations import (
    compatible_actuators,
    compatible_drives,
    compatible_gearheads,
    compatible_motors,
    stroke_distribution_positions,
)


router = APIRouter(prefix="/api/v1/relations")


def _serialise(rows: list[Any]) -> list[dict[str, Any]]:
    """Serialise a list of Pydantic instances to JSON-shaped dicts."""

    return [r.model_dump(mode="json") for r in rows]


@router.get("/actuators")
def actuators(
    min_stroke_mm: Optional[float] = Query(None, ge=0),
    min_peak_force_n: Optional[float] = Query(None, ge=0),
    min_peak_velocity_mm_s: Optional[float] = Query(None, ge=0),
    min_duty_cycle: Optional[float] = Query(None, ge=0, le=1),
    orientation: Optional[Literal["horizontal", "vertical"]] = Query(None),
) -> dict[str, Any]:
    """Requirements-first actuator narrowing (``todo/BUILD.md`` Part 4).

    Every floor is optional — an unset floor applies no constraint, per
    Build's "blank = no constraint applied" rule. ``min_duty_cycle`` and
    ``orientation`` are accepted for the BUILD.md ``ActuatorQuery``
    contract but deliberately do not filter: duty cycle is a
    motor-thermal concept and orientation a downstream derating hint
    (see ``specodex.relations.compatible_actuators``).

    Out-of-range floors are rejected by FastAPI with 422 rather than
    Express's zod 400; the v2 surface pins 422 throughout (same
    precedent as ``/api/v1/search``'s ``limit``).
    """
    del min_duty_cycle, orientation  # accepted, non-filtering — see docstring

    db = BackendDB()
    rows = db.list_by_type("linear_actuator")
    all_actuators = [r for r in rows if isinstance(r, LinearActuator)]

    matches = compatible_actuators(
        all_actuators,
        min_stroke_mm=min_stroke_mm,
        min_peak_force_n=min_peak_force_n,
        min_peak_velocity_mm_s=min_peak_velocity_mm_s,
    )

    positions = stroke_distribution_positions(matches)
    data = _serialise(matches)
    for row, position in zip(data, positions):
        if position is not None:
            row["_distribution_position"] = position

    return {
        "success": True,
        "data": data,
        "count": len(data),
        "total": len(all_actuators),
    }


@router.get("/motors-for-actuator")
def motors_for_actuator(
    id: str = Query(..., min_length=1),
    type: Literal["linear_actuator", "electric_cylinder"] = Query(...),
) -> dict[str, Any]:
    db = BackendDB()
    actuator = db.read_by_id(id, type)
    if actuator is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Actuator not found",
        )

    motors = db.list_by_type("motor")
    matches = compatible_motors(actuator, motors)
    return {"success": True, "data": _serialise(matches), "count": len(matches)}


@router.get("/drives-for-motor")
def drives_for_motor(id: str = Query(..., min_length=1)) -> dict[str, Any]:
    db = BackendDB()
    motor = db.read_by_id(id, "motor")
    if motor is None or not isinstance(motor, Motor):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Motor not found",
        )

    drives_iter = db.list_by_type("drive")
    matches = compatible_drives(motor, drives_iter)
    return {"success": True, "data": _serialise(matches), "count": len(matches)}


@router.get("/gearheads-for-motor")
def gearheads_for_motor(id: str = Query(..., min_length=1)) -> dict[str, Any]:
    db = BackendDB()
    motor = db.read_by_id(id, "motor")
    if motor is None or not isinstance(motor, Motor):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Motor not found",
        )

    # Filter the gearhead listing to only Gearhead instances — the
    # predicate is typed against Gearhead.
    gearhead_class = SCHEMA_CHOICES.get("gearhead")
    if gearhead_class is None:
        return {"success": True, "data": [], "count": 0}
    gearheads = db.list_by_type("gearhead")
    matches = compatible_gearheads(motor, gearheads)
    return {"success": True, "data": _serialise(matches), "count": len(matches)}
