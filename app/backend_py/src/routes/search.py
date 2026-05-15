"""Search endpoint — text query + spec filtering + sort.

Port of ``app/backend/src/routes/search.ts``. The Zod validation
layer in Express becomes FastAPI's native query-parameter parsing;
adding a new product type still only requires a model file +
``./Quickstart gen-types`` (the validation is driven by the
auto-discovered SCHEMA_CHOICES registry).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.backend_py.src.db.dynamodb import BackendDB
from app.backend_py.src.services.search import SearchParams, search_products
from specodex.config import SCHEMA_CHOICES


router = APIRouter(prefix="/api/v1/search")


@router.get("")
def search(
    q: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    manufacturer: Optional[str] = Query(None),
    where: Optional[list[str]] = Query(None),
    sort: Optional[list[str]] = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    # Validate ``type`` against the auto-discovered enum. FastAPI's
    # native ``Literal`` would do this, but the literal needs the
    # SCHEMA_CHOICES at decoration time and that registers product
    # types via module-level import side effects — checking inside
    # the handler keeps the seam loose.
    if type is not None and type not in SCHEMA_CHOICES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid product type: {type!r}. "
                f"Must be one of: {sorted(SCHEMA_CHOICES.keys())}"
            ),
        )

    db = BackendDB()
    product_type = type or "all"
    products = db.list_by_type(product_type)

    result = search_products(
        SearchParams(
            products=list(products),
            query=q,
            manufacturer=manufacturer,
            where=where,
            sort=sort,
            limit=limit,
        )
    )

    return {
        "success": True,
        "data": result.products,
        "count": result.count,
    }
