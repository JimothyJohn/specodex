"""User-owned Projects — named collections of product refs.

Port of ``app/backend/src/routes/projects.ts``. All endpoints are
``require_auth``-gated. Identity is read from ``user.sub``; the URL
never carries the owner. A user can only see / mutate their own
projects.

    GET    /api/projects
    POST   /api/projects
    GET    /api/projects/{id}
    PATCH  /api/projects/{id}
    DELETE /api/projects/{id}
    POST   /api/projects/{id}/products
    DELETE /api/projects/{id}/products/{type}/{pid}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, StringConstraints
from typing_extensions import Annotated

from app.backend_py.src.db.projects import ProjectsService
from app.backend_py.src.middleware.auth import AuthedUser, require_auth


router = APIRouter(prefix="/api/projects", dependencies=[Depends(require_auth)])


def _db() -> ProjectsService:
    return ProjectsService()


def _public(project: dict[str, Any]) -> dict[str, Any]:
    """Strip the internal partition keys before returning to clients."""

    return {k: v for k, v in project.items() if k not in ("PK", "SK")}


# ---------------------------------------------------------------------------
# Validation shapes
# ---------------------------------------------------------------------------


class _CreateBody(BaseModel):
    name: Annotated[
        str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)
    ]


class _RenameBody(BaseModel):
    name: Annotated[
        str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)
    ]


class _ProductRefBody(BaseModel):
    product_type: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    product_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
def list_projects(user: AuthedUser = Depends(require_auth)) -> dict[str, Any]:
    db = _db()
    projects = db.list(user.sub)
    return {
        "success": True,
        "data": [_public(p) for p in projects],
        "count": len(projects),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    body: _CreateBody = Body(...),
    user: AuthedUser = Depends(require_auth),
) -> dict[str, Any]:
    now = _iso_now()
    project = {
        "id": str(uuid4()),
        "name": body.name,
        "owner_sub": user.sub,
        "product_refs": [],
        "created_at": now,
        "updated_at": now,
    }
    db = _db()
    db.create(user.sub, project)
    return {"success": True, "data": _public({**project})}


@router.get("/{project_id}")
def get_project(
    project_id: str, user: AuthedUser = Depends(require_auth)
) -> dict[str, Any]:
    db = _db()
    project = db.get(user.sub, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return {"success": True, "data": _public(project)}


@router.patch("/{project_id}")
def rename_project(
    project_id: str,
    body: _RenameBody = Body(...),
    user: AuthedUser = Depends(require_auth),
) -> dict[str, Any]:
    db = _db()
    updated = db.rename(user.sub, project_id, body.name)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return {"success": True, "data": _public(updated)}


@router.delete("/{project_id}")
def delete_project(
    project_id: str, user: AuthedUser = Depends(require_auth)
) -> dict[str, Any]:
    db = _db()
    ok = db.delete(user.sub, project_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return {"success": True, "data": {"deleted": True}}


@router.post("/{project_id}/products")
def add_product_ref(
    project_id: str,
    body: _ProductRefBody = Body(...),
    user: AuthedUser = Depends(require_auth),
) -> dict[str, Any]:
    db = _db()
    updated = db.add_product(
        user.sub,
        project_id,
        {"product_type": body.product_type, "product_id": body.product_id},
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return {"success": True, "data": _public(updated)}


@router.delete("/{project_id}/products/{product_type}/{product_id}")
def remove_product_ref(
    project_id: str,
    product_type: str,
    product_id: str,
    user: AuthedUser = Depends(require_auth),
) -> dict[str, Any]:
    db = _db()
    updated = db.remove_product(
        user.sub,
        project_id,
        {"product_type": product_type, "product_id": product_id},
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return {"success": True, "data": _public(updated)}
