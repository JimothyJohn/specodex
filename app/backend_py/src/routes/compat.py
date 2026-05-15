"""Compatibility check route — pairwise compat for the rotary chain.

POST /api/v1/compat/check
GET  /api/v1/compat/adjacent

Reuses ``specodex.integration.compat.check`` so the device-pairing
math has a single source of truth. The route layer's only job is
input validation, the read-pair-from-DB, and the response envelope.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import BaseModel

from app.backend_py.src.db.dynamodb import BackendDB
from specodex.integration.compat import check as compat_check


router = APIRouter(prefix="/api/v1/compat")


# Mirror the Express ``ADJACENT_TYPES`` table verbatim.
ADJACENT_TYPES: dict[str, list[str]] = {
    "drive": ["motor"],
    "motor": ["drive", "gearhead"],
    "gearhead": ["motor"],
}

_SUPPORTED_TYPES = frozenset({"drive", "motor", "gearhead"})


def _is_pair_supported(a_type: str, b_type: str) -> bool:
    return b_type in ADJACENT_TYPES.get(a_type, [])


class ProductRef(BaseModel):
    id: str
    type: str


class CheckBody(BaseModel):
    a: ProductRef
    b: ProductRef


def _report_to_dict(report: Any) -> Any:
    """Convert a dataclass tree (CompatibilityReport / CompatResult /
    CheckResult) into nested dicts for JSON serialisation. Round-trip
    through asdict only handles the leaves cleanly when every node is
    a dataclass — the compat module's nodes are.
    """

    if is_dataclass(report) and not isinstance(report, type):
        return asdict(report)
    if isinstance(report, list):
        return [_report_to_dict(r) for r in report]
    if isinstance(report, dict):
        return {k: _report_to_dict(v) for k, v in report.items()}
    return report


@router.post("/check")
def check_compat(body: CheckBody = Body(...)) -> dict[str, Any]:
    a, b = body.a, body.b

    if a.type not in _SUPPORTED_TYPES or b.type not in _SUPPORTED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported product type. Must be one of {sorted(_SUPPORTED_TYPES)}"
            ),
        )

    if not _is_pair_supported(a.type, b.type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported product pair: {a.type} + {b.type}. "
                f"Supported pairs: drive↔motor, motor↔gearhead."
            ),
        )

    db = BackendDB()
    product_a = db.read_by_id(a.id, a.type)
    product_b = db.read_by_id(b.id, b.type)

    if product_a is None and product_b is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Both products not found",
        )
    if product_a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product a ({a.id}) not found",
        )
    if product_b is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product b ({b.id}) not found",
        )

    # ``strict=False`` mirrors Express's ``softenReport(check(...))`` —
    # the UI never gates on ``fail`` until shared schemas (fieldbus,
    # encoder) are normalised.
    report = compat_check(product_a, product_b, strict=False)
    return {"success": True, "data": _report_to_dict(report)}


@router.get("/adjacent")
def get_adjacent(type: str = Query("")) -> dict[str, Any]:
    return {"success": True, "data": ADJACENT_TYPES.get(type, [])}
