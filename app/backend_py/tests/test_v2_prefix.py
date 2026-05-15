"""Tests for the /api/v2 path-prefix strip middleware.

The middleware lets the FastAPI app serve the same un-prefixed
routes (`/api/products`) whether it's hit directly (local dev,
smoke tests) or behind the API Gateway `/api/v2/{proxy+}` route.
"""

from __future__ import annotations

import importlib
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_MODE", "public")
    monkeypatch.setenv("NODE_ENV", "test")
    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


class TestV2PrefixStrip:
    def test_health_reachable_without_prefix(self, client: TestClient) -> None:
        # Direct hit — no /api/v2 prefix. The middleware is a
        # pass-through; /health routes normally.
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_health_reachable_via_v2_prefix(self, client: TestClient) -> None:
        # API Gateway forwards /api/v2/health → the middleware
        # rewrites to /api/health... but /health has no /api prefix.
        # Pin the actual rewrite contract: /api/v2/<rest> → /api/<rest>.
        # So a v2-prefixed health check uses /api/v2 + the route's
        # own path. /health isn't under /api, so test with a real
        # /api route below; here just confirm the bare prefix 404s
        # cleanly rather than 500-ing.
        resp = client.get("/api/v2/health")
        # /api/v2/health → /api/health, which isn't a registered
        # route (health is at /health). 404, not 500 — the
        # middleware didn't choke.
        assert resp.status_code == 404

    def test_products_route_via_v2_prefix(self, client: TestClient) -> None:
        # /api/v2/products/categories → /api/products/categories,
        # which IS a registered route. The categories endpoint needs
        # no DB seeding — it lists all registered types with counts,
        # and an empty/unreachable table just yields zero counts.
        # We only assert the middleware routed it (not 404), so a
        # 200 or a 500-from-no-DB are both "the route matched".
        resp = client.get("/api/v2/products/categories")
        assert resp.status_code != 404

    def test_bare_v2_prefix_does_not_500(self, client: TestClient) -> None:
        resp = client.get("/api/v2")
        # /api/v2 → /api, not a route. 404 is correct; 500 would mean
        # the middleware mishandled the exact-prefix case.
        assert resp.status_code == 404

    def test_non_api_path_untouched(self, client: TestClient) -> None:
        # A path that merely starts with /api but isn't /api/v2 must
        # pass through unrewritten.
        resp = client.get("/api/products/categories")
        assert resp.status_code != 404
