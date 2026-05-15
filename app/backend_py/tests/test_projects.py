"""Projects CRUD tests.

All routes are require_auth-gated. Auth uses the same Cognito-mocked
fixtures as the products CRUD tests.
"""

from __future__ import annotations

import importlib
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.backend_py.tests.conftest import make_token as _make_token


@pytest.fixture
def projects_client(
    dynamodb_table, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_MODE", "admin")  # avoid readonly guard
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


@pytest.fixture
def user_a_token(
    projects_client: TestClient,
    configured_env,
    patched_jwks,
    rsa_keys,
) -> str:
    return _make_token(rsa_keys, sub="alice", email="alice@example.com")


@pytest.fixture
def user_b_token(
    projects_client: TestClient,
    configured_env,
    patched_jwks,
    rsa_keys,
) -> str:
    return _make_token(rsa_keys, sub="bob", email="bob@example.com")


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestAuthGate:
    def test_list_requires_auth(self, projects_client: TestClient) -> None:
        resp = projects_client.get("/api/projects")
        # No Cognito config in this case = 503. The contract is "not
        # 200" — anonymous traffic never sees project data.
        assert resp.status_code in (401, 503)

    def test_list_with_invalid_token_returns_401(
        self,
        projects_client: TestClient,
        configured_env,
        patched_jwks,
    ) -> None:
        resp = projects_client.get(
            "/api/projects", headers={"Authorization": "Bearer garbage"}
        )
        assert resp.status_code == 401


class TestCrudHappyPath:
    def test_full_lifecycle(
        self,
        projects_client: TestClient,
        user_a_token: str,
    ) -> None:
        # Empty list initially.
        resp = projects_client.get("/api/projects", headers=_h(user_a_token))
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

        # Create.
        resp = projects_client.post(
            "/api/projects",
            json={"name": "My Project"},
            headers=_h(user_a_token),
        )
        assert resp.status_code == 201
        project = resp.json()["data"]
        assert project["name"] == "My Project"
        assert project["owner_sub"] == "alice"
        assert project["product_refs"] == []
        project_id = project["id"]

        # Read back via list + by id.
        listed = projects_client.get("/api/projects", headers=_h(user_a_token)).json()
        assert listed["count"] == 1

        single = projects_client.get(
            f"/api/projects/{project_id}", headers=_h(user_a_token)
        ).json()
        assert single["data"]["id"] == project_id

        # Rename.
        resp = projects_client.patch(
            f"/api/projects/{project_id}",
            json={"name": "Renamed Project"},
            headers=_h(user_a_token),
        )
        assert resp.json()["data"]["name"] == "Renamed Project"

        # Add product ref.
        resp = projects_client.post(
            f"/api/projects/{project_id}/products",
            json={"product_type": "motor", "product_id": "MTR-X"},
            headers=_h(user_a_token),
        )
        assert resp.status_code == 200
        refs = resp.json()["data"]["product_refs"]
        assert refs == [{"product_type": "motor", "product_id": "MTR-X"}]

        # Idempotency: re-adding the same ref doesn't grow the list.
        resp = projects_client.post(
            f"/api/projects/{project_id}/products",
            json={"product_type": "motor", "product_id": "MTR-X"},
            headers=_h(user_a_token),
        )
        assert len(resp.json()["data"]["product_refs"]) == 1

        # Remove ref.
        resp = projects_client.delete(
            f"/api/projects/{project_id}/products/motor/MTR-X",
            headers=_h(user_a_token),
        )
        assert resp.json()["data"]["product_refs"] == []

        # Delete the project.
        resp = projects_client.delete(
            f"/api/projects/{project_id}", headers=_h(user_a_token)
        )
        assert resp.json()["data"] == {"deleted": True}

        # 404 after deletion.
        assert (
            projects_client.get(
                f"/api/projects/{project_id}", headers=_h(user_a_token)
            ).status_code
            == 404
        )


class TestOwnership:
    def test_user_a_cannot_read_user_b_project(
        self,
        projects_client: TestClient,
        user_a_token: str,
        user_b_token: str,
    ) -> None:
        # alice creates a project.
        create = projects_client.post(
            "/api/projects",
            json={"name": "alice's"},
            headers=_h(user_a_token),
        )
        pid = create.json()["data"]["id"]

        # bob's list is empty (different partition).
        bob_list = projects_client.get("/api/projects", headers=_h(user_b_token)).json()
        assert bob_list["count"] == 0

        # bob can't read alice's by id.
        resp = projects_client.get(f"/api/projects/{pid}", headers=_h(user_b_token))
        assert resp.status_code == 404

        # bob can't delete alice's.
        resp = projects_client.delete(f"/api/projects/{pid}", headers=_h(user_b_token))
        assert resp.status_code == 404


class TestValidation:
    def test_missing_name_returns_422(
        self,
        projects_client: TestClient,
        user_a_token: str,
    ) -> None:
        resp = projects_client.post(
            "/api/projects",
            json={},
            headers=_h(user_a_token),
        )
        assert resp.status_code == 422

    def test_empty_name_after_strip_returns_422(
        self,
        projects_client: TestClient,
        user_a_token: str,
    ) -> None:
        resp = projects_client.post(
            "/api/projects",
            json={"name": "   "},
            headers=_h(user_a_token),
        )
        assert resp.status_code == 422
