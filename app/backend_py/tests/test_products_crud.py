"""CRUD + aggregation tests for /api/products.

Builds on the moto fixture in conftest.py plus the JWKS/RSA helpers
from test_auth_middleware.py to cover the admin-gated mutations.
The read-only aggregations (manufacturers, names, summary, single
read) need no auth — they're available in public mode.
"""

from __future__ import annotations

import importlib
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from specodex.db.dynamo import DynamoDBClient
from specodex.models.drive import Drive
from specodex.models.motor import Motor

# Token helper (plain function — fixtures live in conftest.py).
from app.backend_py.tests.conftest import make_token as _make_token


@pytest.fixture
def seeded_client(
    dynamodb_table, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv(
        "APP_MODE", "admin"
    )  # mutations require admin-mode + admin group
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")

    service = DynamoDBClient(table_name="products")
    motor_a = Motor(
        product_name="Test Motor A",
        manufacturer="MfgA",
        product_type="motor",
        part_number="MTR-001",
    )
    motor_b = Motor(
        product_name="Test Motor B",
        manufacturer="MfgB",
        product_type="motor",
        part_number="MTR-002",
    )
    drive = Drive(
        product_name="Test Drive",
        manufacturer="MfgA",
        product_type="drive",
        part_number="DRV-001",
    )
    assert service.create(motor_a)
    assert service.create(motor_b)
    assert service.create(drive)

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


# ---------------------------------------------------------------------------
# Read-only aggregations
# ---------------------------------------------------------------------------


class TestReadOnlyAggregations:
    def test_manufacturers_returns_unique_sorted(
        self, seeded_client: TestClient
    ) -> None:
        resp = seeded_client.get("/api/products/manufacturers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == ["MfgA", "MfgB"]

    def test_names_returns_unique_sorted(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/products/names")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        names = body["data"]
        assert "Test Motor A" in names
        assert "Test Motor B" in names
        assert "Test Drive" in names

    def test_summary_returns_per_type_plus_total(
        self, seeded_client: TestClient
    ) -> None:
        resp = seeded_client.get("/api/products/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["motor"] == 2
        assert data["drive"] == 1
        # Total includes every type, even zero-count ones don't bump it.
        assert data["total"] == sum(v for k, v in data.items() if k != "total")
        assert data["total"] >= 3


class TestSingleRead:
    def test_read_known_product_returns_200(self, seeded_client: TestClient) -> None:
        # Find a known product_id by listing first.
        resp = seeded_client.get("/api/products?type=motor&limit=1")
        first_id = resp.json()["data"][0]["product_id"]

        resp = seeded_client.get(f"/api/products/{first_id}?type=motor")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["product_id"] == first_id
        assert body["data"]["product_type"] == "motor"

    def test_missing_type_param_returns_400(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/products/some-id")
        assert resp.status_code == 400

    def test_unknown_id_returns_404(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/products/nonexistent-id?type=motor")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Admin-gated mutations
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_token(
    seeded_client: TestClient,
    configured_env,
    patched_jwks,
    rsa_keys,
) -> str:
    # seeded_client set APP_MODE=admin and reloaded main; configured_env
    # added Cognito env. Generate a token in the admin group.
    return _make_token(rsa_keys, sub="root", groups=["admin"])


@pytest.fixture
def user_token(configured_env, patched_jwks, rsa_keys) -> str:
    return _make_token(rsa_keys, sub="alice", groups=["users"])


class TestCreate:
    def test_create_single_product_returns_201(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        new_motor: dict[str, Any] = {
            "product_name": "Brand New Motor",
            "manufacturer": "MfgC",
            "product_type": "motor",
            "part_number": "MTR-NEW",
        }
        resp = seeded_client.post(
            "/api/products",
            json=new_motor,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["items_created"] == 1
        assert body["data"]["items_failed"] == 0

    def test_create_requires_admin(
        self,
        seeded_client: TestClient,
        user_token: str,
    ) -> None:
        resp = seeded_client.post(
            "/api/products",
            json={
                "product_name": "x",
                "manufacturer": "y",
                "product_type": "motor",
            },
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 403

    def test_create_with_no_token_returns_401(
        self,
        seeded_client: TestClient,
        configured_env,
        patched_jwks,
    ) -> None:
        # configured_env ensures Cognito vars are present so we hit the
        # "missing bearer" 401 path rather than the "auth not
        # configured" 503 path.
        resp = seeded_client.post(
            "/api/products",
            json={
                "product_name": "x",
                "manufacturer": "y",
                "product_type": "motor",
            },
        )
        assert resp.status_code == 401

    def test_missing_product_type_returns_400(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        resp = seeded_client.post(
            "/api/products",
            json={"product_name": "x", "manufacturer": "y"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400

    def test_unknown_product_type_returns_400(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        resp = seeded_client.post(
            "/api/products",
            json={
                "product_name": "x",
                "manufacturer": "y",
                "product_type": "definitely_not_a_type",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400

    def test_create_batch_returns_count(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        batch = [
            {
                "product_name": f"Batch Motor {i}",
                "manufacturer": "BatchMfg",
                "product_type": "motor",
                "part_number": f"BATCH-{i}",
            }
            for i in range(3)
        ]
        resp = seeded_client.post(
            "/api/products",
            json=batch,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["items_received"] == 3
        assert body["data"]["items_created"] == 3

    def test_primitive_body_returns_400(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        resp = seeded_client.post(
            "/api/products",
            json="not an object",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400


class TestUpdate:
    def test_update_persists(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        first_id = seeded_client.get("/api/products?type=motor&limit=1").json()["data"][
            0
        ]["product_id"]

        resp = seeded_client.put(
            f"/api/products/{first_id}?type=motor",
            json={"product_name": "Renamed Motor"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        # Re-read to verify the change took.
        again = seeded_client.get(f"/api/products/{first_id}?type=motor")
        assert again.json()["data"]["product_name"] == "Renamed Motor"

    def test_update_unknown_id_returns_404(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        resp = seeded_client.put(
            "/api/products/no-such-id?type=motor",
            json={"product_name": "x"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    def test_update_requires_admin(
        self,
        seeded_client: TestClient,
        user_token: str,
    ) -> None:
        resp = seeded_client.put(
            "/api/products/any-id?type=motor",
            json={"product_name": "x"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 403


class TestDelete:
    def test_delete_by_id_returns_200(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        first_id = seeded_client.get("/api/products?type=motor&limit=1").json()["data"][
            0
        ]["product_id"]

        resp = seeded_client.delete(
            f"/api/products/{first_id}?type=motor",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        # Re-read should now 404.
        again = seeded_client.get(f"/api/products/{first_id}?type=motor")
        assert again.status_code == 404

    def test_delete_unknown_id_returns_404(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        resp = seeded_client.delete(
            "/api/products/no-such-id?type=motor",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    def test_delete_by_part_number_deletes_matching(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        resp = seeded_client.delete(
            "/api/products/part-number/MTR-001",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["deleted"] >= 1

    def test_delete_by_manufacturer_returns_count(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        resp = seeded_client.delete(
            "/api/products/manufacturer/MfgA",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        # MfgA has both a motor and a drive seeded.
        assert resp.json()["data"]["deleted"] >= 2

    def test_delete_by_unknown_part_number_returns_404(
        self,
        seeded_client: TestClient,
        admin_token: str,
    ) -> None:
        resp = seeded_client.delete(
            "/api/products/part-number/DOES-NOT-EXIST",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404
