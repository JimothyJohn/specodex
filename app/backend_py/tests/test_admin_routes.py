"""Admin route tests.

Blacklist tests use a temp blacklist file. Diff/promote/demote/purge
get the lighter treatment — pinning the validation paths (400s) and
the response envelope. The semantic correctness of those operations
is already covered by tests/integration/test_admin_operations.py at
the specodex level.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.backend_py.tests.conftest import make_token as _make_token


@pytest.fixture
def admin_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_MODE", "admin")
    monkeypatch.setenv("NODE_ENV", "test")
    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


@pytest.fixture
def admin_token(
    admin_client: TestClient,
    configured_env,
    patched_jwks,
    rsa_keys,
) -> str:
    return _make_token(rsa_keys, sub="root", groups=["admin"])


@pytest.fixture
def user_token(configured_env, patched_jwks, rsa_keys) -> str:
    return _make_token(rsa_keys, sub="alice", groups=["users"])


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def temp_blacklist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the Blacklist module at a temp JSON file."""

    bl_path = tmp_path / "blacklist.json"
    bl_path.write_text(json.dumps({"banned_manufacturers": []}))

    from specodex.admin import blacklist as bl_module

    monkeypatch.setattr(bl_module, "DEFAULT_BLACKLIST_PATH", bl_path)
    return bl_path


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


class TestAuthGate:
    def test_blacklist_requires_admin_group(
        self, admin_client: TestClient, user_token: str
    ) -> None:
        resp = admin_client.get("/api/admin/blacklist", headers=_h(user_token))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------


class TestBlacklist:
    def test_empty_initially(
        self,
        admin_client: TestClient,
        admin_token: str,
        temp_blacklist: Path,
    ) -> None:
        resp = admin_client.get("/api/admin/blacklist", headers=_h(admin_token))
        assert resp.status_code == 200
        assert resp.json()["data"]["banned_manufacturers"] == []

    def test_add_and_remove(
        self,
        admin_client: TestClient,
        admin_token: str,
        temp_blacklist: Path,
    ) -> None:
        # Add
        resp = admin_client.post(
            "/api/admin/blacklist",
            json={"manufacturer": "ScamCo"},
            headers=_h(admin_token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["added"] is True
        assert "ScamCo" in data["banned_manufacturers"]

        # Idempotent re-add returns added=False
        resp = admin_client.post(
            "/api/admin/blacklist",
            json={"manufacturer": "ScamCo"},
            headers=_h(admin_token),
        )
        assert resp.json()["data"]["added"] is False

        # Remove
        resp = admin_client.delete(
            "/api/admin/blacklist/ScamCo", headers=_h(admin_token)
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["removed"] is True
        assert resp.json()["data"]["banned_manufacturers"] == []

    def test_missing_manufacturer_returns_400(
        self,
        admin_client: TestClient,
        admin_token: str,
        temp_blacklist: Path,
    ) -> None:
        resp = admin_client.post(
            "/api/admin/blacklist", json={}, headers=_h(admin_token)
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Validation paths
# ---------------------------------------------------------------------------


class TestDiffValidation:
    def test_invalid_stage_returns_400(
        self, admin_client: TestClient, admin_token: str
    ) -> None:
        resp = admin_client.post(
            "/api/admin/diff",
            json={"source": "bogus", "target": "dev", "type": "motor"},
            headers=_h(admin_token),
        )
        assert resp.status_code == 400

    def test_same_source_and_target_returns_400(
        self, admin_client: TestClient, admin_token: str
    ) -> None:
        resp = admin_client.post(
            "/api/admin/diff",
            json={"source": "dev", "target": "dev", "type": "motor"},
            headers=_h(admin_token),
        )
        assert resp.status_code == 400

    def test_invalid_type_returns_400(
        self, admin_client: TestClient, admin_token: str
    ) -> None:
        resp = admin_client.post(
            "/api/admin/diff",
            json={"source": "dev", "target": "prod", "type": "notathing"},
            headers=_h(admin_token),
        )
        assert resp.status_code == 400


class TestPurgeValidation:
    def test_requires_type_or_manufacturer(
        self, admin_client: TestClient, admin_token: str
    ) -> None:
        resp = admin_client.post(
            "/api/admin/purge",
            json={"stage": "dev"},
            headers=_h(admin_token),
        )
        assert resp.status_code == 400

    def test_apply_without_correct_confirm_returns_400(
        self, admin_client: TestClient, admin_token: str
    ) -> None:
        """The expected confirm string is ``yes delete <stage> <type?>
        <manufacturer?>``. Anything else with apply=true → 400."""

        # Patch the underlying purge() so we don't accidentally call
        # DynamoDB if the confirm check is bypassed.
        with patch("app.backend_py.src.routes.admin.purge") as fake_purge:
            resp = admin_client.post(
                "/api/admin/purge",
                json={
                    "stage": "dev",
                    "type": "motor",
                    "apply": True,
                    "confirm": "delete dev motor",  # missing 'yes'
                },
                headers=_h(admin_token),
            )
            assert resp.status_code == 400
            fake_purge.assert_not_called()
