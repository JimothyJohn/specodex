"""API-key management for per-query billing (FastAPI port).

Mirror of ``app/backend/src/routes/apikeys.ts``. Identity is the JWT
``user.sub``, never the body. The plaintext key is returned once.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.backend_py.src.middleware.auth import AuthedUser, require_auth
from app.backend_py.src.services import stripe_client


router = APIRouter(prefix="/api/apikeys")


@router.post("")
def mint_api_key(
    body: dict[str, Any] = Body(default_factory=dict),
    user: AuthedUser = Depends(require_auth),
) -> dict[str, Any]:
    if not os.environ.get("STRIPE_LAMBDA_URL"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured",
        )
    # Empty body required; identity is the token, not the body (mirrors
    # checkout). The negative test is a body carrying user_id → 400.
    if body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be empty; identity is taken from the auth token",
        )
    try:
        api_key = stripe_client.create_api_key(user.sub)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    # Returned once; only its hash is persisted upstream.
    return {"success": True, "data": {"api_key": api_key}}
