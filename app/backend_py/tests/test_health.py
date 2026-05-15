"""Health endpoint contract tests.

Pins the JSON shape the smoke suite (``tests/post_deploy/``) checks
on every deploy. If this drifts from the Express ``GET /health``
response, parallel-deployment correctness breaks.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("APP_MODE", "public")
    monkeypatch.setenv("NODE_ENV", "test")
    # Re-import the app each test so config.load() runs against the
    # current env.
    import importlib

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_health_returns_healthy(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    payload = resp.json()
    # Shape pinned against app/backend/src/index.ts /health response.
    assert payload["status"] == "healthy"
    assert payload["environment"] == "test"
    assert payload["mode"] == "public"
    assert "timestamp" in payload


def test_health_mode_reflects_app_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "admin")
    monkeypatch.setenv("NODE_ENV", "test")
    import importlib

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    client = TestClient(main_mod.app)

    payload = client.get("/health").json()
    assert payload["mode"] == "admin"
