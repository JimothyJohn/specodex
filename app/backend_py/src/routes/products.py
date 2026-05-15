"""Products endpoints — full CRUD + aggregations.

Mirrors ``app/backend/src/routes/products.ts``. All write methods
gate behind ``require_auth + admin group`` since Express's readonly
guard already blocked them in public mode and the admin-only mutation
pattern (used on /api/admin/* in Express) is the safer default for
the parallel-deploy window. If a real-user write path lands later,
relax the gate on a per-route basis.

Response shape matches the Express backend's
``{success, data, count?, error?}`` envelope so the frontend can
hit either stack via a single API client.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.backend_py.src.db.dynamodb import BackendDB
from app.backend_py.src.middleware.auth import AuthedUser, require_group


router = APIRouter(prefix="/api/products")


def _db() -> BackendDB:
    # Function-call indirection rather than a module-level global so
    # tests can monkeypatch ``BackendDB`` after env vars are set up.
    return BackendDB()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("/categories")
def list_categories() -> dict[str, Any]:
    db = _db()
    return {"success": True, "data": db.get_categories()}


@router.get("/manufacturers")
def list_manufacturers() -> dict[str, Any]:
    db = _db()
    return {"success": True, "data": db.get_unique_manufacturers()}


@router.get("/names")
def list_names() -> dict[str, Any]:
    db = _db()
    return {"success": True, "data": db.get_unique_names()}


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    db = _db()
    return {"success": True, "data": db.count()}


@router.get("")
def list_products(
    type: str = Query("all"),
    limit: Optional[int] = Query(None, ge=1, le=10_000),
) -> dict[str, Any]:
    db = _db()
    rows = db.list_by_type(type, limit=limit)
    data = [row.model_dump(mode="json") for row in rows]
    return {"success": True, "data": data, "count": len(data)}


@router.get("/{product_id}")
def read_product(
    product_id: str,
    type: Optional[str] = Query(None),
) -> dict[str, Any]:
    if not type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="type query parameter is required",
        )
    db = _db()
    product = db.read_by_id(product_id, type)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )
    return {"success": True, "data": product.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Mutations — gated behind admin group
# ---------------------------------------------------------------------------


def _require_admin() -> AuthedUser:
    """Module-level helper so the dep is constructed once."""

    return require_group("admin")  # type: ignore[return-value]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_products(
    payload: Any = Body(...),
    _: AuthedUser = Depends(require_group("admin")),
) -> dict[str, Any]:
    """Accept a product dict or a list of product dicts.

    Express handled datasheets here too via an in-band ``url`` field
    sniff; we don't — datasheets get their own route in the
    upcoming datasheets.py port. Reject anything that doesn't have
    a ``product_type`` and a ``manufacturer``.
    """

    if isinstance(payload, dict):
        items: list[dict[str, Any]] = [payload]
    elif isinstance(payload, list) and all(isinstance(p, dict) for p in payload):
        items = payload
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body must be a product object or an array of product objects",
        )

    from specodex.config import SCHEMA_CHOICES  # local import — avoid hot path

    parsed = []
    for item in items:
        product_type = item.get("product_type")
        manufacturer = item.get("manufacturer")
        if not product_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Each product must have a product_type field",
            )
        if not manufacturer:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Each product must have a manufacturer field",
            )
        model_class = SCHEMA_CHOICES.get(product_type)
        if model_class is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown product_type: {product_type}",
            )
        # Auto-generate product_id if the caller didn't supply one.
        item.setdefault("product_id", str(uuid4()))
        try:
            parsed.append(model_class(**item))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid product payload: {exc}",
            )

    db = _db()
    if len(parsed) == 1:
        success_count = 1 if db.create(parsed[0]) else 0
    else:
        success_count = db.batch_create(parsed)

    failure_count = len(parsed) - success_count
    return {
        "success": success_count > 0,
        "data": {
            "items_received": len(parsed),
            "items_created": success_count,
            "items_failed": failure_count,
        },
    }


@router.put("/{product_id}")
def update_product(
    product_id: str,
    updates: dict[str, Any] = Body(...),
    type: Optional[str] = Query(None),
    _: AuthedUser = Depends(require_group("admin")),
) -> dict[str, Any]:
    if not type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="type query parameter is required",
        )
    db = _db()
    if not db.update_by_id(product_id, type, updates):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found or failed to update",
        )
    return {"success": True, "message": "Product updated successfully"}


@router.delete("/part-number/{part_number}")
def delete_by_part_number(
    part_number: str,
    _: AuthedUser = Depends(require_group("admin")),
) -> dict[str, Any]:
    db = _db()
    result = db.delete_by_part_number(part_number)
    if result["deleted"] == 0 and result["failed"] == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No products found with this part number",
        )
    return {
        "success": True,
        "data": result,
        "message": (
            f"Deleted {result['deleted']} products (Failed: {result['failed']})"
        ),
    }


@router.delete("/manufacturer/{manufacturer}")
def delete_by_manufacturer(
    manufacturer: str,
    _: AuthedUser = Depends(require_group("admin")),
) -> dict[str, Any]:
    db = _db()
    result = db.delete_by_manufacturer(manufacturer)
    if result["deleted"] == 0 and result["failed"] == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No products found with this manufacturer",
        )
    return {
        "success": True,
        "data": result,
        "message": (
            f"Deleted {result['deleted']} products (Failed: {result['failed']})"
        ),
    }


@router.delete("/name/{product_name}")
def delete_by_product_name(
    product_name: str,
    _: AuthedUser = Depends(require_group("admin")),
) -> dict[str, Any]:
    db = _db()
    result = db.delete_by_product_name(product_name)
    if result["deleted"] == 0 and result["failed"] == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No products found with this name",
        )
    return {
        "success": True,
        "data": result,
        "message": (
            f"Deleted {result['deleted']} products (Failed: {result['failed']})"
        ),
    }


@router.delete("/{product_id}")
def delete_product(
    product_id: str,
    type: Optional[str] = Query(None),
    _: AuthedUser = Depends(require_group("admin")),
) -> dict[str, Any]:
    if not type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="type query parameter is required",
        )
    db = _db()
    if not db.delete_by_id(product_id, type):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found or failed to delete",
        )
    return {"success": True, "message": "Product deleted successfully"}
