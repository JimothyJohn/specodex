"""Frontend ↔ FastAPI contract for ``/api/v1/relations/*``.

Replays the exact URLs the /build page builds
(``app/frontend/src/api/relationsUrl.contract.json``, which
``relationsUrl.test.ts`` pins to ``buildRelationsUrl()``) against the
real FastAPI app. Express runs the same check by importing the builder
directly (``app/backend/tests/relations.frontend-contract.test.ts``).

If the frontend and the routers drift, this fails with a 404 instead of
production serving index.html under a 200.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from specodex.db.dynamo import DynamoDBClient
from specodex.models.linear_actuator import LinearActuator
from specodex.models.motor import Motor

CONTRACT = Path(__file__).resolve().parents[2] / (
    "frontend/src/api/relationsUrl.contract.json"
)
CASES: list[dict[str, Any]] = json.loads(CONTRACT.read_text())["cases"]


def _source_id(product_type: str) -> str:
    ids = {
        c["source"]["product_id"]
        for c in CASES
        if c["source"]["product_type"] == product_type
    }
    assert len(ids) == 1, f"contract file should use one {product_type} id"
    return ids.pop()


@pytest.fixture
def contract_client(
    dynamodb_table, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_MODE", "admin")
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")

    service = DynamoDBClient(table_name="products")
    service.create(
        LinearActuator(
            product_id=UUID(_source_id("linear_actuator")),
            product_name="Contract Actuator",
            manufacturer="Mfg",
            product_type="linear_actuator",
            part_number="LA-1",
            compatible_motor_mounts=["NEMA 23"],
        )
    )
    service.create(
        Motor(
            product_id=UUID(_source_id("motor")),
            product_name="Contract Motor",
            manufacturer="Mfg",
            product_type="motor",
            part_number="MTR-1",
            motor_mount_pattern="NEMA 23",
            rated_voltage="200-240;V",
            rated_current="3;A",
        )
    )

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


@pytest.mark.parametrize("case", CASES, ids=[c["relation"] for c in CASES])
def test_frontend_url_resolves(
    contract_client: TestClient, case: dict[str, Any]
) -> None:
    resp = contract_client.get(case["url"])

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["success"] is True
    assert body["count"] == len(body["data"])


def test_motors_for_actuator_returns_the_mount_match(
    contract_client: TestClient,
) -> None:
    (case,) = [c for c in CASES if c["relation"] == "motors-for-actuator"]
    body = contract_client.get(case["url"]).json()
    assert [r["product_id"] for r in body["data"]] == [_source_id("motor")]


@pytest.mark.parametrize(
    "url",
    [
        # The shape the frontend used to build; must stay a JSON 404.
        "/api/v1/relations/motors?output=x&output_type=linear_actuator",
        "/api/v1/does-not-exist",
    ],
)
def test_unknown_api_path_is_json_404(contract_client: TestClient, url: str) -> None:
    resp = contract_client.get(url)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
