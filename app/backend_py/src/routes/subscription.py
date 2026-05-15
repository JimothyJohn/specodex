"""Subscription routes — proxy to the Stripe payments Lambda.

Port of ``app/backend/src/routes/subscription.ts``. Identity is
read from the JWT (``user.sub``) — never from the URL or body.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.backend_py.src.middleware.auth import AuthedUser, require_auth
from app.backend_py.src.services import stripe_client


router = APIRouter(prefix="/api/subscription")


@router.get("/status")
def status_endpoint(
    user: AuthedUser = Depends(require_auth),
) -> dict[str, Any]:
    if not os.environ.get("STRIPE_LAMBDA_URL"):
        return {
            "success": True,
            "data": {"subscription_status": "none", "billing_enabled": False},
        }
    try:
        result = stripe_client.get_subscription_status(user.sub)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    return {"success": True, "data": result}


@router.post("/checkout")
def checkout(
    body: dict[str, Any] = Body(default_factory=dict),
    user: AuthedUser = Depends(require_auth),
) -> dict[str, Any]:
    # Express enforced an empty body via Zod ``.strict()``. Mirror it —
    # the negative test is "old-style body with user_id in it" → 400.
    if body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Request body must be empty; identity is taken from the auth token"
            ),
        )
    try:
        result = stripe_client.create_checkout_session(user.sub)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    return {"success": True, "data": result}


@router.get("/config")
def billing_config() -> dict[str, Any]:
    """Public — used by the frontend before any user is authed."""

    return {
        "success": True,
        "data": {"billing_enabled": bool(os.environ.get("STRIPE_LAMBDA_URL"))},
    }
