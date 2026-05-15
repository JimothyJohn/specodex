"""Integration tests for /api/v1/compat/* and /api/v1/relations/*.

The Express service tests already cover the predicate-level math in
``specodex.relations`` and ``specodex.integration.compat``; this
file pins the FastAPI wrapper contract — auth-less reads, the
4xx surfaces, the response envelope shape.
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
def compat_client(
    dynamodb_table, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_MODE", "admin")
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")

    service = DynamoDBClient(table_name="products")

    # Build a "real" pair: motor + drive that should at least line up
    # on voltage. The integration tests don't pin the full report
    # contents — that's covered in tests/unit/test_integration.py at
    # the specodex level.
    motor = Motor(
        product_name="Test Motor",
        manufacturer="Mfg",
        product_type="motor",
        part_number="MTR-1",
        rated_voltage="200-240;V",
        rated_current="3;A",
    )
    drive = Drive(
        product_name="Test Drive",
        manufacturer="Mfg",
        product_type="drive",
        part_number="DRV-1",
        input_voltage="200-240;V",
        rated_current="5;A",
    )
    service.create(motor)
    service.create(drive)

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app), str(motor.product_id), str(drive.product_id)


class TestAdjacent:
    def test_motor_neighbours(self, compat_client) -> None:
        client, _, _ = compat_client
        resp = client.get("/api/v1/compat/adjacent?type=motor")
        assert resp.status_code == 200
        assert set(resp.json()["data"]) == {"drive", "gearhead"}

    def test_drive_neighbours(self, compat_client) -> None:
        client, _, _ = compat_client
        resp = client.get("/api/v1/compat/adjacent?type=drive")
        assert resp.json()["data"] == ["motor"]

    def test_unknown_type_empty_data(self, compat_client) -> None:
        client, _, _ = compat_client
        resp = client.get("/api/v1/compat/adjacent?type=nothing")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestCheck:
    def test_supported_pair_returns_report(self, compat_client) -> None:
        client, motor_id, drive_id = compat_client
        resp = client.post(
            "/api/v1/compat/check",
            json={
                "a": {"id": motor_id, "type": "motor"},
                "b": {"id": drive_id, "type": "drive"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        report = body["data"]
        assert report["from_type"] == "motor"
        assert report["to_type"] == "drive"
        assert report["status"] in {"ok", "partial"}

    def test_unsupported_pair_returns_400(self, compat_client) -> None:
        client, motor_id, _ = compat_client
        resp = client.post(
            "/api/v1/compat/check",
            json={
                "a": {"id": motor_id, "type": "motor"},
                "b": {"id": motor_id, "type": "motor"},  # motor↔motor not in pairs
            },
        )
        assert resp.status_code == 400

    def test_unsupported_type_returns_400(self, compat_client) -> None:
        client, motor_id, _ = compat_client
        resp = client.post(
            "/api/v1/compat/check",
            json={
                "a": {"id": motor_id, "type": "motor"},
                "b": {"id": "x", "type": "robot_arm"},
            },
        )
        assert resp.status_code == 400

    def test_missing_product_returns_404(self, compat_client) -> None:
        client, motor_id, _ = compat_client
        resp = client.post(
            "/api/v1/compat/check",
            json={
                "a": {"id": motor_id, "type": "motor"},
                "b": {"id": "no-such-drive", "type": "drive"},
            },
        )
        assert resp.status_code == 404


class TestRelationsRoute:
    def test_drives_for_motor_returns_envelope(self, compat_client) -> None:
        client, motor_id, _ = compat_client
        resp = client.get(f"/api/v1/relations/drives-for-motor?id={motor_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
        assert body["count"] == len(body["data"])

    def test_drives_for_unknown_motor_returns_404(self, compat_client) -> None:
        client, _, _ = compat_client
        resp = client.get("/api/v1/relations/drives-for-motor?id=nope")
        assert resp.status_code == 404

    def test_gearheads_for_motor_returns_envelope(self, compat_client) -> None:
        client, motor_id, _ = compat_client
        resp = client.get(f"/api/v1/relations/gearheads-for-motor?id={motor_id}")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
