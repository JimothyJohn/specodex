"""Relations route — device compatibility queries.

Mirrors the Express ``relations.ts`` shape but reuses
``specodex.relations`` directly. The Python module is the single
source of truth for the predicates (SCHEMA Phase 3a); this file
is the FastAPI wrapper.

Endpoints:
- GET /api/v1/relations/motors-for-actuator?id=<uuid>&type=...
- GET /api/v1/relations/drives-for-motor?id=<uuid>
- GET /api/v1/relations/gearheads-for-motor?id=<uuid>
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.backend_py.src.db.dynamodb import BackendDB
from specodex.config import SCHEMA_CHOICES
from specodex.models.motor import Motor
from specodex.relations import (
    compatible_drives,
    compatible_gearheads,
    compatible_motors,
)


router = APIRouter(prefix="/api/v1/relations")


def _serialise(rows: list[Any]) -> list[dict[str, Any]]:
    """Serialise a list of Pydantic instances to JSON-shaped dicts."""

    return [r.model_dump(mode="json") for r in rows]


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
