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
from specodex.models.linear_actuator import LinearActuator
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


@pytest.fixture
def actuators_client(
    dynamodb_table, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Client over a table seeded with four linear actuators.

    Strokes are chosen so the distribution ranker has something to rank:
    two share the 200 mm bucket, one sits alone at 500 mm, and one
    carries no stroke at all (the unbadgeable row).
    """
    monkeypatch.setenv("APP_MODE", "admin")
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")

    service = DynamoDBClient(table_name="products")

    def _actuator(pn: str, **fields) -> LinearActuator:
        return LinearActuator(
            product_name=f"Actuator {pn}",
            manufacturer="Mfg",
            product_type="linear_actuator",
            part_number=pn,
            **fields,
        )

    for actuator in [
        _actuator(
            "LA-200a",
            stroke={"value": 200, "unit": "mm"},
            max_push_force={"value": 500, "unit": "N"},
            max_linear_speed={"value": 800, "unit": "mm/s"},
        ),
        _actuator(
            "LA-200b",
            stroke={"value": 200, "unit": "mm"},
            max_push_force={"value": 1500, "unit": "N"},
            max_linear_speed={"value": 200, "unit": "mm/s"},
        ),
        _actuator(
            "LA-500",
            stroke={"value": 500, "unit": "mm"},
            max_push_force={"value": 3000, "unit": "N"},
        ),
        _actuator("LA-nostroke", max_push_force={"value": 100, "unit": "N"}),
    ]:
        service.create(actuator)

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


class TestActuatorsRoute:
    """`GET /api/v1/relations/actuators` — Build's first slot-fill query
    (todo/BUILD.md Part 4). Mirrors the Express route's contract."""

    def test_no_floors_returns_every_actuator(self, actuators_client) -> None:
        resp = actuators_client.get("/api/v1/relations/actuators")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        # Build's "blank = no constraint applied" rule: an empty query
        # narrows nothing.
        assert body["count"] == 4
        assert body["total"] == 4
        assert body["count"] == len(body["data"])

    def test_stroke_floor_narrows(self, actuators_client) -> None:
        resp = actuators_client.get("/api/v1/relations/actuators?min_stroke_mm=300")
        assert resp.status_code == 200
        body = resp.json()
        assert [r["part_number"] for r in body["data"]] == ["LA-500"]
        # `total` stays the pre-filter size so the UI can show "1 of 4".
        assert body["total"] == 4

    def test_floors_compose(self, actuators_client) -> None:
        resp = actuators_client.get(
            "/api/v1/relations/actuators"
            "?min_stroke_mm=150&min_peak_force_n=1000&min_peak_velocity_mm_s=100"
        )
        assert resp.status_code == 200
        # LA-200a clears stroke + speed but not force; LA-500 clears
        # stroke + force but publishes no speed (excluded on missing
        # data); only LA-200b clears all three.
        assert [r["part_number"] for r in resp.json()["data"]] == ["LA-200b"]

    def test_missing_field_is_excluded_when_its_floor_is_set(
        self, actuators_client
    ) -> None:
        resp = actuators_client.get(
            "/api/v1/relations/actuators?min_peak_velocity_mm_s=1"
        )
        parts = {r["part_number"] for r in resp.json()["data"]}
        assert parts == {"LA-200a", "LA-200b"}

    def test_distribution_position_is_attached(self, actuators_client) -> None:
        resp = actuators_client.get("/api/v1/relations/actuators")
        by_pn = {r["part_number"]: r for r in resp.json()["data"]}

        # Two rows in the 200 mm bucket → rank 1; one at 500 mm → rank 2.
        assert by_pn["LA-200a"]["_distribution_position"] == {
            "spec": "stroke",
            "rank": 1,
            "cluster_count": 2,
        }
        assert (
            by_pn["LA-200b"]["_distribution_position"]
            == (by_pn["LA-200a"]["_distribution_position"])
        )
        assert by_pn["LA-500"]["_distribution_position"] == {
            "spec": "stroke",
            "rank": 2,
            "cluster_count": 1,
        }
        # No stroke, no badge — the UI has nothing to say about this row.
        assert "_distribution_position" not in by_pn["LA-nostroke"]

    def test_distribution_ranks_the_filtered_set_not_the_catalogue(
        self, actuators_client
    ) -> None:
        # Once the 200 mm pair is filtered out, LA-500 is the most
        # common remaining stroke — the badge is relative to what the
        # user is actually looking at.
        resp = actuators_client.get("/api/v1/relations/actuators?min_stroke_mm=300")
        assert resp.json()["data"][0]["_distribution_position"] == {
            "spec": "stroke",
            "rank": 1,
            "cluster_count": 1,
        }

    def test_duty_cycle_and_orientation_are_accepted_but_do_not_filter(
        self, actuators_client
    ) -> None:
        # Accepted for the BUILD.md ActuatorQuery contract; duty cycle is
        # a motor-thermal concept and orientation a derating hint, so
        # neither narrows the actuator list.
        resp = actuators_client.get(
            "/api/v1/relations/actuators?min_duty_cycle=0.5&orientation=vertical"
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 4

    @pytest.mark.parametrize(
        "query",
        [
            "min_stroke_mm=-1",
            "min_peak_force_n=-0.5",
            "min_peak_velocity_mm_s=-100",
            "min_duty_cycle=1.5",
            "min_duty_cycle=-0.1",
            "orientation=sideways",
            "min_stroke_mm=abc",
        ],
    )
    def test_invalid_query_returns_422(self, actuators_client, query: str) -> None:
        # FastAPI's own validation layer answers 422 where Express's zod
        # answered 400; the v2 surface pins 422 throughout (same
        # precedent as /api/v1/search's `limit`).
        resp = actuators_client.get(f"/api/v1/relations/actuators?{query}")
        assert resp.status_code == 422
