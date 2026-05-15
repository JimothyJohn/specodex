"""Health-check endpoint. Mirrors ``GET /health`` on the Express
backend — same JSON shape so smoke tests (``tests/post_deploy/``)
pass against either stack."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.backend_py.src.config import load as load_settings


router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    settings = load_settings()
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": settings.node_env,
        "mode": settings.app_mode,
    }
