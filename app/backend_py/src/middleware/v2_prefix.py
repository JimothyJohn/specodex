"""``/api/v2`` path-prefix strip middleware.

The Python FastAPI Lambda is mounted in API Gateway at
``/api/v2/{proxy+}`` (see ``app/infrastructure/lib/api-stack.ts``),
so every request arrives with a ``/api/v2`` prefix on the path:
``/api/v2/products``, ``/api/v2/v1/search``, etc.

The route handlers, however, are registered at the *un-prefixed*
paths (``/api/products``, ``/api/v1/search``) — they're a 1:1
port of the Express routes and the frontend's API client knows
those paths.

This middleware bridges the gap: it rewrites the incoming ASGI
``path`` from ``/api/v2/<rest>`` to ``/api/<rest>`` before routing.
The frontend's API client does the inverse rewrite
(``/api/<rest>`` → ``/api/v2/<rest>``) when ``VITE_API_VERSION=v2``,
so the two halves compose into a no-op for the application code.

When a request arrives *without* the ``/api/v2`` prefix (local dev
hitting the app directly, or a smoke test), the middleware is a
pass-through — nothing to strip.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Request, Response


_V2_PREFIX = "/api/v2"


async def strip_v2_prefix(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Rewrite ``/api/v2/<rest>`` → ``/api/<rest>`` on the request scope.

    Mutates ``request.scope['path']`` (and ``raw_path``) in place. ASGI
    apps are allowed to do this — Starlette's router reads ``path``
    from the scope at dispatch time, after middleware has run.
    """

    path: str = request.scope.get("path", "")
    if path == _V2_PREFIX or path.startswith(_V2_PREFIX + "/"):
        rewritten = "/api" + path[len(_V2_PREFIX) :]
        request.scope["path"] = rewritten
        # raw_path is bytes; keep it consistent so anything reading it
        # (access logs, some integrations) sees the rewritten value.
        raw = request.scope.get("raw_path")
        if isinstance(raw, (bytes, bytearray)):
            query = b""
            if b"?" in raw:
                query = raw[raw.index(b"?") :]
            request.scope["raw_path"] = rewritten.encode("latin-1") + query

    return await call_next(request)
