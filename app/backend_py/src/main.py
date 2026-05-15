"""FastAPI application entrypoint.

Wires together the routers and the AWS Lambda Web Adapter (Mangum).
The handler shape mirrors ``app/backend/src/index.ts`` so the
CDK ``api-stack.ts`` Phase-1.3 wiring can swap the Lambda's
handler value without code-level surprises elsewhere.

Run locally:

    cd app/backend_py
    uv run uvicorn app.backend_py.src.main:app --reload --port 3001
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.backend_py.src.config import load as load_settings
from app.backend_py.src.middleware.readonly import readonly_guard
from app.backend_py.src.routes import compat as compat_routes
from app.backend_py.src.routes import datasheets as datasheets_routes
from app.backend_py.src.routes import health as health_routes
from app.backend_py.src.routes import products as products_routes
from app.backend_py.src.routes import relations as relations_routes
from app.backend_py.src.routes import search as search_routes


def create_app() -> FastAPI:
    """FastAPI factory.

    Settings are read at app-creation time, not import time, so unit
    tests can ``monkeypatch.setenv`` before calling ``create_app``.
    """

    settings = load_settings()
    app = FastAPI(
        title="Specodex API (Python)",
        version="0.1.0",
        # Phase 1 deploys this under ``/api/v2/*`` per the plan in
        # ``todo/PYTHON_BACKEND.md`` §1.3. Routers below already carry
        # their own ``/api/...`` prefix to match the Express paths
        # 1:1; the CDK-level path translation is the operator's job.
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    # Readonly guard only runs in public mode. Admin deployments
    # skip the registration entirely so write methods are unmolested.
    if settings.app_mode == "public":
        app.middleware("http")(readonly_guard)

    app.include_router(health_routes.router)
    app.include_router(products_routes.router)
    app.include_router(datasheets_routes.router)
    app.include_router(search_routes.router)
    app.include_router(compat_routes.router)
    app.include_router(relations_routes.router)

    return app


app = create_app()

# AWS Lambda entrypoint. ``api-stack.ts`` will point its Python
# Lambda's handler at ``app.backend_py.src.main.handler``.
handler = Mangum(app, lifespan="off")
