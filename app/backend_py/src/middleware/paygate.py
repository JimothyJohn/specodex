"""Per-query paygate for programmatic API consumers (FastAPI port).

Direct mirror of ``app/backend/src/middleware/apiKeyPaygate.ts``. Wired
as an ``app.middleware("http")`` (like ``readonly_guard``) so it sees
the path *after* ``strip_v2_prefix`` has rewritten ``/api/v2/...`` →
``/api/...`` and can read ``response.status_code`` to bill only on
success.

Contract, keyed entirely on the ``X-API-Key`` header for requests to
the billable read paths (search, relations):

  - No header        → public/UI traffic, served free, unchanged.
  - Header, unknown  → 401 (a present-but-invalid key is a client error).
  - Header, no sub   → 402 Payment Required.
  - Header, active   → served, then +1 query reported AFTER a <400
                       response, off the request path (daemon thread).

Availability bias: a billing-service outage fails OPEN to the free
path rather than 500-ing the read API.
"""

from __future__ import annotations

import logging
import threading
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.backend_py.src.services import stripe_client

logger = logging.getLogger(__name__)

_API_KEY_HEADER = "x-api-key"
# Paths whose successful responses are billable when keyed.
_BILLABLE_PREFIXES = ("/api/v1/search", "/api/v1/relations")


def _is_billable(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _BILLABLE_PREFIXES)


def _meter_async(user_id: str) -> None:
    # Off the request path: report_query_usage is a sync httpx call with
    # a 5s timeout; never make the client wait on it.
    threading.Thread(
        target=stripe_client.report_query_usage,
        args=(user_id, 1),
        daemon=True,
    ).start()


async def paygate(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    path = request.url.path
    if not _is_billable(path):
        return await call_next(request)

    api_key = request.headers.get(_API_KEY_HEADER)
    if not api_key:
        # Free public/UI path — unchanged.
        return await call_next(request)

    try:
        verification = stripe_client.verify_api_key(api_key)
    except Exception as exc:
        # Billing outage → fail open to free rather than 500 the read API.
        logger.error("API key verification unavailable; serving free: %s", exc)
        return await call_next(request)

    if not verification.get("valid") or not verification.get("user_id"):
        return JSONResponse(
            status_code=401, content={"success": False, "error": "Invalid API key"}
        )

    if verification.get("subscription_status") != "active":
        return JSONResponse(
            status_code=402,
            content={
                "success": False,
                "error": (
                    "Active subscription required for API access. "
                    "Subscribe at /api/subscription/checkout."
                ),
            },
        )

    user_id = verification["user_id"]
    response = await call_next(request)
    # Meter only successful queries; a 4xx/5xx isn't a billable query.
    if response.status_code < 400:
        _meter_async(user_id)
    return response
