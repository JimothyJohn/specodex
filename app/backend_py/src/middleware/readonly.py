"""Readonly middleware — enforces read-only access in public mode.

Port of ``app/backend/src/middleware/readonly.ts``. FastAPI-shaped
ASGI middleware that rejects non-GET/HEAD/OPTIONS requests with 403
in public mode, except for explicitly allow-listed paths (the PDF
upload queue + auth + projects per-user mutations).

The Express version logged the blocked request with newline-stripped
path interpolation as a log-injection defence. We follow the same
practice here by passing the raw path through ``%s`` formatting
rather than f-strings (CodeQL has flagged this exact shape before).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)


_ALLOWED_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Paths exempt from readonly — these only queue work, they don't
# mutate existing data.
_WRITE_ALLOWED_PATHS = frozenset({"/api/upload", "/api/upload/"})

# Path prefixes exempt from readonly. Auth endpoints (register,
# login, password reset) need POST in public mode but don't mutate
# product data — the user table is Cognito's. Projects are per-user
# data; the route enforces ownership via require_auth.
_WRITE_ALLOWED_PREFIXES = ("/api/auth/", "/api/projects")


async def readonly_guard(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """ASGI middleware enforcing read-only mode in APP_MODE=public.

    Wired in ``main.py`` via ``app.middleware("http")(readonly_guard)``
    only when the loaded settings declare ``app_mode == "public"``.
    Admin-mode deployments skip the registration entirely; the public
    deployment is the only one that enforces this boundary.
    """

    method = request.method.upper()
    path = request.url.path

    if method in _ALLOWED_METHODS:
        return await call_next(request)

    if path in _WRITE_ALLOWED_PATHS:
        return await call_next(request)

    if any(path.startswith(prefix) for prefix in _WRITE_ALLOWED_PREFIXES):
        return await call_next(request)

    # Newline-strip the path before logging — user-controlled input
    # landing in a log line is a CodeQL log-injection finding waiting
    # to happen. Use %-formatting so the sanitiser is in plain sight.
    safe_path = path.replace("\r", "").replace("\n", "")
    logger.warning(
        "[readonly] Blocked %s %s — public mode is read-only",
        method,
        safe_path,
    )

    return JSONResponse(
        status_code=403,
        content={
            "success": False,
            "error": "This endpoint is read-only in public mode",
            "hint": "Use the local admin toolset for write operations",
        },
    )
