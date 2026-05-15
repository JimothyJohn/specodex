"""Tests for the public-mode readonly guard.

Mirrors the cases in ``app/backend/tests/middleware/readonly.test.ts``:
non-GET methods rejected with 403, except for the upload queue
(``/api/upload``), auth routes (``/api/auth/*``), and projects
(``/api/projects*``). Admin-mode deployment skips the middleware
registration entirely.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def public_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("APP_MODE", "public")
    monkeypatch.setenv("NODE_ENV", "test")
    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


@pytest.fixture
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("APP_MODE", "admin")
    monkeypatch.setenv("NODE_ENV", "test")
    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


class TestPublicModeRejects:
    def test_post_to_unknown_path_returns_403(self, public_client: TestClient) -> None:
        resp = public_client.post("/api/something", json={})
        assert resp.status_code == 403
        body = resp.json()
        assert body["success"] is False
        assert "read-only" in body["error"].lower()

    def test_put_returns_403(self, public_client: TestClient) -> None:
        resp = public_client.put("/api/anything")
        assert resp.status_code == 403

    def test_delete_returns_403(self, public_client: TestClient) -> None:
        resp = public_client.delete("/api/anything")
        assert resp.status_code == 403

    def test_patch_returns_403(self, public_client: TestClient) -> None:
        resp = public_client.patch("/api/anything", json={})
        assert resp.status_code == 403


class TestPublicModeAllows:
    def test_get_passes_through(self, public_client: TestClient) -> None:
        # /health exists and is a GET — should not be touched by
        # readonly_guard.
        resp = public_client.get("/health")
        assert resp.status_code == 200

    def test_options_passes_through(self, public_client: TestClient) -> None:
        # OPTIONS preflight is fine in any mode.
        resp = public_client.options("/api/products")
        # CORS responds with 200; readonly didn't block.
        assert resp.status_code in (200, 405)

    def test_post_to_upload_path_passes(self, public_client: TestClient) -> None:
        # The upload queue route isn't mounted yet (Phase 1.1 only has
        # /health + /api/products), but readonly should NOT 403 the
        # request — it should fall through to FastAPI's 404 for the
        # unmounted route. That's the contract we're pinning.
        resp = public_client.post("/api/upload", json={})
        assert resp.status_code != 403

    def test_post_to_auth_prefix_passes(self, public_client: TestClient) -> None:
        resp = public_client.post("/api/auth/login", json={})
        assert resp.status_code != 403

    def test_post_to_projects_prefix_passes(self, public_client: TestClient) -> None:
        resp = public_client.post("/api/projects", json={})
        assert resp.status_code != 403


class TestAdminModeAllowsAll:
    def test_admin_mode_lets_post_through(self, admin_client: TestClient) -> None:
        # In admin mode the readonly middleware isn't even registered;
        # writes hit the routers directly. /api/something doesn't
        # exist so we expect 404, NOT 403 from readonly.
        resp = admin_client.post("/api/something", json={})
        assert resp.status_code == 404


class TestLogInjection:
    """Pin the CLAUDE.md log-injection defence — newline characters in
    a user-controlled path must not propagate into the log record.
    The middleware uses %-formatting + a strip pass to defang.
    """

    def test_crlf_in_path_does_not_break_logger(
        self, public_client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        caplog.set_level(
            logging.WARNING, logger="app.backend_py.src.middleware.readonly"
        )

        # Real HTTP clients can't easily send raw CR/LF in a path
        # because URL encoding intercepts them, but a malicious
        # caller via the ASGI scope could. The string-strip in the
        # middleware is defence-in-depth — we exercise it directly
        # via the encoded form here as a smoke test that the helper
        # is wired.
        resp = public_client.post("/api/something%0Ainjected", json={})
        assert resp.status_code == 403
        # If anything landed in caplog, it must not contain a literal
        # newline followed by an attacker-supplied "injected".
        for record in caplog.records:
            assert "\ninjected" not in record.getMessage()
