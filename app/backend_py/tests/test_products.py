"""Contract tests for the /api/products endpoints.

Exercises the FastAPI app against a moto-mocked DynamoDB table
seeded with a small Motor + Drive sample. Pins the response
envelope (``{success, data, count?}``) and the per-type filtering
behaviour against the Express contract so the two stacks return
identical shapes during the parallel-deploy window.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from specodex.db.dynamo import DynamoDBClient
from specodex.models.drive import Drive
from specodex.models.motor import Motor


@pytest.fixture
def seeded_client(dynamodb_table, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Seed the moto table with one Motor and one Drive, then return a
    TestClient pointing at a freshly-built FastAPI app.

    Re-imports the app module so route-level closures pick up the
    current env vars and the patched DAL.
    """

    monkeypatch.setenv("APP_MODE", "public")
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")

    service = DynamoDBClient(table_name="products")
    motor = Motor(
        product_name="Test Motor",
        manufacturer="TestMfg",
        product_type="motor",
        part_number="MTR-001",
        rated_voltage="200-240;V",
        rated_torque="2.5;Nm",
    )
    drive = Drive(
        product_name="Test Drive",
        manufacturer="TestMfg",
        product_type="drive",
        part_number="DRV-001",
    )
    assert service.create(motor)
    assert service.create(drive)

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_categories_returns_all_registered_types(
    seeded_client: TestClient,
) -> None:
    resp = seeded_client.get("/api/products/categories")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True

    types = {entry["type"] for entry in payload["data"]}
    # Every type in SCHEMA_CHOICES shows up, even zero-count ones.
    # This pins the contract Express's getCategories established.
    assert {"motor", "drive"} <= types

    # Per-type entry shape pinned: type / count / display_name.
    motor_entry = next(e for e in payload["data"] if e["type"] == "motor")
    assert motor_entry["count"] == 1
    assert motor_entry["display_name"] == "Motors"

    drive_entry = next(e for e in payload["data"] if e["type"] == "drive")
    assert drive_entry["count"] == 1
    assert drive_entry["display_name"] == "Drives"


def test_list_products_default_returns_all(
    seeded_client: TestClient,
) -> None:
    resp = seeded_client.get("/api/products")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    # Default ``type=all`` returns rows from every type.
    types = {row["product_type"] for row in payload["data"]}
    assert {"motor", "drive"} <= types
    assert payload["count"] == len(payload["data"])


def test_list_products_filters_by_type(seeded_client: TestClient) -> None:
    resp = seeded_client.get("/api/products?type=motor")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["data"][0]["product_type"] == "motor"


def test_list_products_unknown_type_returns_empty(
    seeded_client: TestClient,
) -> None:
    # The Express handler returns an empty array for unknown types
    # rather than 400 — pin that exact behaviour.
    resp = seeded_client.get("/api/products?type=nonexistent_type")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"] == []
    assert payload["count"] == 0


def test_list_products_respects_limit(seeded_client: TestClient) -> None:
    resp = seeded_client.get("/api/products?type=all&limit=1")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 1
    # 2 seeded rows, limit=1 → result is truncated.
    assert payload["truncated"] is True


def test_list_products_default_not_truncated_on_small_table(
    seeded_client: TestClient,
) -> None:
    # Default cap is 2000; only 2 rows seeded. Should never trip
    # the truncated flag in this fixture.
    resp = seeded_client.get("/api/products")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["truncated"] is False


def test_list_products_truncated_flag_false_below_limit(
    seeded_client: TestClient,
) -> None:
    # Even with an explicit limit larger than the row count,
    # truncated stays False because the result didn't hit the cap.
    resp = seeded_client.get("/api/products?type=motor&limit=10")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 1
    assert payload["truncated"] is False


def test_list_products_default_limit_applied(
    seeded_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin the contract: when the caller omits ``limit``, the route
    # passes DEFAULT_LIST_LIMIT (2000) down to ``db.list_by_type``.
    # Mirrors the v1 backend's test "applies the default 2000-row cap".
    from app.backend_py.src.routes import products as products_route

    captured: dict[str, object] = {}
    real_db = products_route._db()
    original_list_by_type = real_db.list_by_type

    def spy_list_by_type(t: str, limit=None):
        captured["limit"] = limit
        return original_list_by_type(t, limit=limit)

    class _SpyDB:
        list_by_type = staticmethod(spy_list_by_type)

    monkeypatch.setattr(products_route, "_db", lambda: _SpyDB)

    resp = seeded_client.get("/api/products?type=motor")
    assert resp.status_code == 200
    assert captured["limit"] == products_route.DEFAULT_LIST_LIMIT == 2000
