"""Datasheets endpoints — full CRUD.

Port of ``app/backend/src/routes/datasheets.ts``. Mutations gated
behind ``require_group('admin')`` for the same parallel-deploy
window reasons as the products route.

The list response maps the on-disk Datasheet shape into the
shape the frontend expects (``component_type`` for the
underlying product type, ``product_id`` aliased to
``datasheet_id`` so React keys collide cleanly, and an
``is_scraped`` boolean derived from ``last_scraped``).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.backend_py.src.db.dynamodb import BackendDB
from app.backend_py.src.middleware.auth import AuthedUser, require_group
from specodex.models.datasheet import Datasheet


router = APIRouter(prefix="/api/datasheets")


def _db() -> BackendDB:
    return BackendDB()


def _to_response_shape(datasheet: Datasheet) -> dict[str, Any]:
    """Mirror the Express listing transform.

    Frontend treats every row in the listing as ``product_type ==
    'datasheet'`` and reads ``component_type`` for the underlying
    product. ``last_scraped`` is the existence signal for ``is_scraped``.
    """

    raw = datasheet.model_dump(mode="json")
    underlying_type = raw.pop("product_type", "")
    raw["product_type"] = "datasheet"
    raw["component_type"] = underlying_type
    raw["product_id"] = raw.get("datasheet_id")
    raw["is_scraped"] = bool(raw.get("last_scraped"))
    return raw


@router.get("")
def list_datasheets() -> dict[str, Any]:
    db = _db()
    items = [_to_response_shape(ds) for ds in db.list_datasheets()]
    return {"success": True, "data": items, "count": len(items)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_datasheet(
    payload: dict[str, Any] = Body(...),
    _: AuthedUser = Depends(require_group("admin")),
) -> dict[str, Any]:
    required = ("url", "product_type", "product_name")
    missing = [f for f in required if not payload.get(f)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"Missing required fields: {', '.join(required)}"),
        )

    db = _db()
    if db.datasheet_exists(payload["url"]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Datasheet with this URL already exists",
        )

    payload.setdefault("datasheet_id", str(uuid4()))
    # Datasheet's manufacturer is required by the model; reject up
    # front so a 400 stands in for the Pydantic ValidationError.
    if not payload.get("manufacturer"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required field: manufacturer",
        )
    try:
        datasheet = Datasheet(**payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid datasheet payload: {exc}",
        )

    if not db.create_datasheet(datasheet):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create datasheet",
        )

    return {"success": True, "data": datasheet.model_dump(mode="json")}


@router.put("/{datasheet_id}")
def update_datasheet(
    datasheet_id: str,
    payload: dict[str, Any] = Body(...),
    _: AuthedUser = Depends(require_group("admin")),
) -> dict[str, Any]:
    product_type = payload.get("product_type")
    if not product_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="product_type is required for updates",
        )

    db = _db()
    if not db.update_datasheet(datasheet_id, product_type, payload):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasheet not found or failed to update",
        )
    return {"success": True, "message": "Datasheet updated successfully"}


@router.delete("/{datasheet_id}")
def delete_datasheet(
    datasheet_id: str,
    type: str = Query(...),
    _: AuthedUser = Depends(require_group("admin")),
) -> dict[str, Any]:
    db = _db()
    if not db.delete_datasheet(datasheet_id, type):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasheet not found or failed to delete",
        )
    return {"success": True, "message": "Datasheet deleted successfully"}
