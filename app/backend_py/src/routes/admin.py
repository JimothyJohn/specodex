"""Admin routes — blacklist management + dev/prod data movement.

All endpoints require the ``admin`` Cognito group. Destructive
operations default to dry-run; the client passes ``apply=true`` to
actually write. Purge additionally requires a confirm string
matching ``expected_purge_confirm``.

The route layer is a thin wrapper around ``specodex.admin`` —
``Blacklist``, ``operations.diff``, ``operations.promote``,
``operations.demote``, ``operations.purge``. Keeping the logic in
the pipeline package means the Python CLI and the Express backend
already see the same semantics; this port preserves that.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.backend_py.src.middleware.auth import require_group
from specodex.admin.blacklist import Blacklist
from specodex.admin.operations import demote, diff, promote, purge
from specodex.db.dynamo import DynamoDBClient


router = APIRouter(
    prefix="/api/admin",
    dependencies=[Depends(require_group("admin"))],
)


# ---------------------------------------------------------------------------
# Helpers — mirror Express's services/adminOperations.ts shape
# ---------------------------------------------------------------------------


PROMOTABLE_PRODUCT_TYPES: tuple[str, ...] = (
    "motor",
    "drive",
    "gearhead",
    "robot_arm",
)
STAGES: tuple[str, ...] = ("dev", "staging", "prod")


def _make_client(stage: str) -> DynamoDBClient:
    return DynamoDBClient(table_name=f"products-{stage}")


def _expected_purge_confirm(
    stage: str,
    product_type: Optional[str],
    manufacturer: Optional[str],
) -> str:
    parts = ["yes delete", stage]
    if product_type:
        parts.append(product_type)
    if manufacturer:
        parts.append(manufacturer)
    return " ".join(parts)


def _is_stage(value: Any) -> bool:
    return isinstance(value, str) and value in STAGES


def _is_promotable_type(value: Any) -> bool:
    return isinstance(value, str) and value in PROMOTABLE_PRODUCT_TYPES


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _dataclass_to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, list):
        return [_dataclass_to_dict(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------


@router.get("/blacklist")
def get_blacklist() -> dict[str, Any]:
    bl = Blacklist()
    return {"success": True, "data": {"banned_manufacturers": bl.names()}}


@router.post("/blacklist")
def add_to_blacklist(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    manufacturer = str(body.get("manufacturer", "")).strip()
    if not manufacturer:
        raise _bad_request("manufacturer is required")
    bl = Blacklist()
    added = bl.add(manufacturer)
    if added:
        bl.save()
    return {
        "success": True,
        "data": {
            "manufacturer": manufacturer,
            "added": added,
            "banned_manufacturers": bl.names(),
        },
    }


@router.delete("/blacklist/{manufacturer}")
def remove_from_blacklist(manufacturer: str) -> dict[str, Any]:
    if not manufacturer:
        raise _bad_request("manufacturer path parameter is required")
    bl = Blacklist()
    removed = bl.remove(manufacturer)
    if removed:
        bl.save()
    return {
        "success": True,
        "data": {
            "manufacturer": manufacturer,
            "removed": removed,
            "banned_manufacturers": bl.names(),
        },
    }


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


@router.post("/diff")
def diff_endpoint(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    source = body.get("source")
    target = body.get("target")
    product_type = body.get("type")
    manufacturer = body.get("manufacturer") or None

    if not _is_stage(source) or not _is_stage(target):
        raise _bad_request("source and target must each be one of: dev, staging, prod")
    if source == target:
        raise _bad_request("source and target must differ")
    if not _is_promotable_type(product_type):
        raise _bad_request(
            f"type must be one of: {', '.join(PROMOTABLE_PRODUCT_TYPES)}"
        )

    result = diff(
        source=_make_client(source),
        target=_make_client(target),
        product_type=product_type,
        source_stage=source,
        target_stage=target,
        manufacturer=manufacturer if isinstance(manufacturer, str) else None,
    )
    return {"success": True, "data": _dataclass_to_dict(result)}


# ---------------------------------------------------------------------------
# Promote + Demote
# ---------------------------------------------------------------------------


def _promote_or_demote(body: dict[str, Any], kind: str) -> dict[str, Any]:
    source = body.get("source")
    target = body.get("target")
    product_type = body.get("type")
    manufacturer = body.get("manufacturer") or None
    apply_flag = body.get("apply") is True

    if not _is_stage(source) or not _is_stage(target):
        raise _bad_request("source and target must each be one of: dev, staging, prod")
    if source == target:
        raise _bad_request("source and target must differ")
    if not _is_promotable_type(product_type):
        raise _bad_request(
            f"type must be one of: {', '.join(PROMOTABLE_PRODUCT_TYPES)}"
        )

    src = _make_client(source)
    tgt = _make_client(target)
    mfg = manufacturer if isinstance(manufacturer, str) and manufacturer else None

    if kind == "promote":
        result = promote(
            source=src,
            target=tgt,
            product_type=product_type,
            blacklist=Blacklist(),
            manufacturer=mfg,
            apply=apply_flag,
        )
    else:
        result = demote(
            source=src,
            target=tgt,
            product_type=product_type,
            manufacturer=mfg,
            apply=apply_flag,
        )
    return {"success": True, "data": _dataclass_to_dict(result)}


@router.post("/promote")
def promote_endpoint(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _promote_or_demote(body, "promote")


@router.post("/demote")
def demote_endpoint(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _promote_or_demote(body, "demote")


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


@router.post("/purge")
def purge_endpoint(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    stage = body.get("stage")
    product_type_raw = body.get("type")
    manufacturer_raw = body.get("manufacturer")
    apply_flag = body.get("apply") is True
    confirm = body.get("confirm")

    if not _is_stage(stage):
        raise _bad_request("stage must be one of: dev, staging, prod")

    product_type = product_type_raw if _is_promotable_type(product_type_raw) else None
    manufacturer = (
        manufacturer_raw
        if isinstance(manufacturer_raw, str) and manufacturer_raw
        else None
    )
    if not product_type and not manufacturer:
        raise _bad_request("purge requires type and/or manufacturer")

    expected = _expected_purge_confirm(stage, product_type, manufacturer)
    if apply_flag and confirm != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Confirmation string does not match the purge scope",
                "expected": expected,
            },
        )

    result = purge(
        client=_make_client(stage),
        product_type=product_type,
        manufacturer=manufacturer,
        apply=apply_flag,
    )
    data = _dataclass_to_dict(result)
    data["expected_confirm"] = expected
    return {"success": True, "data": data}
