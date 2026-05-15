"""Products endpoints — list + categories.

Mirrors a strict subset of ``app/backend/src/routes/products.ts``.
The full surface (single-product reads, batch POST, admin mutations,
delete) is deferred to a follow-up PR; this PR is the vertical
slice that proves the FastAPI + Mangum + ``specodex.db.dynamo``
chain works end-to-end. See ``app/backend_py/README.md`` for the
"what's deferred" list.

Response shape matches the Express backend's
``{success, data, count?, error?}`` envelope so the frontend can
hit either stack via a single API client.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query

from app.backend_py.src.db.dynamodb import BackendDB


router = APIRouter(prefix="/api/products")


def _db() -> BackendDB:
    # Function-call indirection rather than a module-level global so
    # tests can monkeypatch ``BackendDB`` after env vars are set up.
    return BackendDB()


@router.get("/categories")
def list_categories() -> dict[str, Any]:
    db = _db()
    return {"success": True, "data": db.get_categories()}


@router.get("")
def list_products(
    type: str = Query("all"),
    limit: Optional[int] = Query(None, ge=1, le=10_000),
) -> dict[str, Any]:
    db = _db()
    rows = db.list_by_type(type, limit=limit)
    # FastAPI will serialise Pydantic instances via model_dump on the
    # return path; force the conversion here to match the Express
    # envelope's plain-dict ``data`` payload.
    data = [row.model_dump(mode="json") for row in rows]
    return {"success": True, "data": data, "count": len(data)}
