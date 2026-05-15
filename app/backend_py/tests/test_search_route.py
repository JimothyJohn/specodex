"""Integration tests for GET /api/v1/search.

Seeds the moto DynamoDB with motors + drives, exercises the route
across query, where, sort, and limit parameters. Pins the
``{success, data, count}`` envelope.
"""

from __future__ import annotations

import importlib
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from specodex.db.dynamo import DynamoDBClient
from specodex.models.drive import Drive
from specodex.models.motor import Motor


@pytest.fixture
def search_client(
    dynamodb_table, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_MODE", "admin")
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")

    service = DynamoDBClient(table_name="products")
    service.create(
        Motor(
            product_name="Big Motor",
            manufacturer="ABB",
            product_type="motor",
            part_number="MTR-BIG",
            rated_power="1000;W",
        )
    )
    service.create(
        Motor(
            product_name="Small Motor",
            manufacturer="ABB",
            product_type="motor",
            part_number="MTR-SMALL",
            rated_power="100;W",
        )
    )
    service.create(
        Motor(
            product_name="Siemens Motor",
            manufacturer="Siemens",
            product_type="motor",
            part_number="MTR-SIE",
            rated_power="500;W",
        )
    )
    service.create(
        Drive(
            product_name="ABB Drive",
            manufacturer="ABB",
            product_type="drive",
            part_number="DRV-001",
        )
    )

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


class TestSearchRoute:
    def test_no_params_returns_all(self, search_client: TestClient) -> None:
        resp = search_client.get("/api/v1/search")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["count"] >= 4

    def test_type_filter(self, search_client: TestClient) -> None:
        resp = search_client.get("/api/v1/search?type=drive")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["product_type"] == "drive"

    def test_invalid_type_returns_400(self, search_client: TestClient) -> None:
        resp = search_client.get("/api/v1/search?type=definitely_not_a_thing")
        assert resp.status_code == 400

    def test_manufacturer_filter(self, search_client: TestClient) -> None:
        resp = search_client.get("/api/v1/search?manufacturer=ABB")
        body = resp.json()
        # 2 ABB motors + 1 ABB drive.
        assert body["count"] == 3

    def test_where_clause_filters_by_power(self, search_client: TestClient) -> None:
        resp = search_client.get("/api/v1/search?type=motor&where=rated_power%3E%3D500")
        body = resp.json()
        # Big (1000W) + Siemens (500W).
        assert body["count"] == 2

    def test_query_matches_part_number(self, search_client: TestClient) -> None:
        resp = search_client.get("/api/v1/search?q=MTR-BIG")
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["part_number"] == "MTR-BIG"
        # Query results have a positive relevance score.
        assert body["data"][0]["relevance"] > 0

    def test_sort_ascending_by_power(self, search_client: TestClient) -> None:
        resp = search_client.get("/api/v1/search?type=motor&sort=rated_power")
        body = resp.json()
        parts = [row["part_number"] for row in body["data"]]
        # 100 W < 500 W < 1000 W.
        assert parts == ["MTR-SMALL", "MTR-SIE", "MTR-BIG"]

    def test_limit_clamps_low(self, search_client: TestClient) -> None:
        # FastAPI's ge=1 rejects limit=0 with 422, but for limit=1 we
        # should get exactly one row.
        resp = search_client.get("/api/v1/search?type=motor&limit=1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_limit_out_of_range_returns_422(self, search_client: TestClient) -> None:
        # FastAPI rejects out-of-range Query params at the validation
        # layer with 422 (Express used 400 + zod; the surfaces match
        # close enough that frontend callers don't need to special-case).
        resp = search_client.get("/api/v1/search?limit=999")
        assert resp.status_code == 422
