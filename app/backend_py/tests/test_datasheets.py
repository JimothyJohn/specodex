"""Datasheets CRUD tests.

Same fixture pattern as test_products_crud.py — seed via the
pipeline DAL, exercise the FastAPI routes via TestClient, gate
mutations behind admin tokens.
"""

from __future__ import annotations

import importlib
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from specodex.db.dynamo import DynamoDBClient
from specodex.models.datasheet import Datasheet
from app.backend_py.tests.conftest import make_token as _make_token


@pytest.fixture
def datasheets_client(
    dynamodb_table, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_MODE", "admin")
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")

    service = DynamoDBClient(table_name="products")
    ds = Datasheet(
        url="https://example.com/motor.pdf",
        product_type="motor",
        product_name="Test Motor",
        manufacturer="MfgA",
    )
    assert service.create(ds)

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


class TestList:
    def test_list_returns_mapped_shape(self, datasheets_client: TestClient) -> None:
        resp = datasheets_client.get("/api/datasheets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["count"] >= 1
        # The list mapper rewrites product_type → 'datasheet' and
        # surfaces the underlying type as component_type.
        first = body["data"][0]
        assert first["product_type"] == "datasheet"
        assert first["component_type"] == "motor"
        assert first["product_id"] == first["datasheet_id"]
        # No last_scraped on a freshly-created datasheet.
        assert first["is_scraped"] is False


class TestCreate:
    @pytest.fixture
    def admin_token(
        self,
        datasheets_client: TestClient,
        configured_env,
        patched_jwks,
        rsa_keys,
    ) -> str:
        return _make_token(rsa_keys, sub="root", groups=["admin"])

    def test_missing_required_fields_returns_400(
        self,
        datasheets_client: TestClient,
        admin_token: str,
    ) -> None:
        resp = datasheets_client.post(
            "/api/datasheets",
            json={"url": "https://example.com/x.pdf"},  # missing product_*
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400

    def test_duplicate_url_returns_409(
        self,
        datasheets_client: TestClient,
        admin_token: str,
    ) -> None:
        # The seeded datasheet's URL is example.com/motor.pdf.
        resp = datasheets_client.post(
            "/api/datasheets",
            json={
                "url": "https://example.com/motor.pdf",
                "product_type": "motor",
                "product_name": "Dup",
                "manufacturer": "MfgB",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 409

    def test_create_returns_201(
        self,
        datasheets_client: TestClient,
        admin_token: str,
    ) -> None:
        payload: dict[str, Any] = {
            "url": "https://example.com/drive.pdf",
            "product_type": "drive",
            "product_name": "Test Drive",
            "manufacturer": "MfgB",
        }
        resp = datasheets_client.post(
            "/api/datasheets",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["url"] == payload["url"]
        assert "datasheet_id" in body["data"]
