"""Stripe Lambda client — proxy from FastAPI to the billing Lambda.

Direct port of ``app/backend/src/services/stripe.ts``. The Stripe
Lambda's wire format keys on ``user_id``; we source that from the
verified JWT (``user.sub``) at the route layer, never from the
client.

When ``STRIPE_LAMBDA_URL`` isn't configured the methods return
sentinel "billing disabled" responses (None or True) — matches
the Express service's "fail open" philosophy for non-mutating
checks. The Lambda being down should not block users.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx


logger = logging.getLogger(__name__)


def _base_url() -> Optional[str]:
    return os.environ.get("STRIPE_LAMBDA_URL") or None


def _enabled() -> bool:
    return bool(_base_url())


def get_subscription_status(user_id: str) -> Optional[dict[str, Any]]:
    """Returns the Stripe Lambda's status payload, or ``None`` if billing
    isn't configured on this deployment."""

    base = _base_url()
    if base is None:
        return None
    resp = httpx.get(f"{base.rstrip('/')}/status/{user_id}", timeout=5.0)
    if resp.status_code >= 400:
        raise RuntimeError(f"Stripe status check failed: {resp.status_code}")
    return resp.json()


def is_subscription_active(user_id: str) -> bool:
    """Fail-open active check.

    True when:
    - billing isn't configured (no enforcement), OR
    - the Stripe Lambda is unreachable / errors, OR
    - the user's status is 'active' or 'trialing'.

    Mirrors Express's ``isSubscriptionActive`` — the billing service
    being down should not block users.
    """

    if not _enabled():
        return True
    try:
        status = get_subscription_status(user_id)
        if status is None:
            return True
        sub_status = status.get("subscription_status", "")
        return sub_status in ("active", "trialing")
    except Exception as exc:
        logger.error("Failed to check subscription status: %s", exc)
        return True


def create_checkout_session(user_id: str) -> dict[str, Any]:
    """Create a checkout session. Raises if billing isn't configured —
    explicit failure mode for write-shaped requests.
    """

    base = _base_url()
    if base is None:
        raise RuntimeError("Stripe billing is not configured")

    resp = httpx.post(
        f"{base.rstrip('/')}/checkout",
        json={"user_id": user_id},
        timeout=5.0,
    )
    if resp.status_code >= 400:
        try:
            err = resp.json()
            message = err.get("error", f"Checkout failed: {resp.status_code}")
        except Exception:
            message = f"Checkout failed: {resp.status_code}"
        raise RuntimeError(message)
    return resp.json()


def report_usage(user_id: str, tokens: int) -> Optional[dict[str, Any]]:
    """Fire-and-forget usage report. Returns None on any failure so
    the caller never has to handle billing-side errors.
    """

    if not _enabled():
        return None
    try:
        resp = httpx.post(
            f"{_base_url().rstrip('/')}/usage",
            json={"user_id": user_id, "tokens": tokens},
            timeout=5.0,
        )
        if resp.status_code >= 400:
            logger.error("Usage reporting failed: %s", resp.status_code)
            return None
        return resp.json()
    except Exception as exc:
        logger.error("Failed to report usage: %s", exc)
        return None
