"""Per-query paygate middleware tests (FastAPI v2).

Mirrors app/backend/tests/apiKeyPaygate.test.ts: free without a key,
401/402 gating, metering only on success, fail-open on a billing
outage. Metering runs in a daemon thread, so assertions poll a short
deadline for the call.
"""

from __future__ import annotations

import importlib
import time
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from specodex.db.dynamo import DynamoDBClient
from specodex.models.motor import Motor

from app.backend_py.tests.conftest import make_token as _make_token


@pytest.fixture
def paygate_client(
    dynamodb_table,
    rsa_keys,
    configured_env,
    patched_jwks,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_MODE", "admin")
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")
    # Billing must look configured for the apikeys mint route's 503 gate;
    # the paygate itself keys on patched verify_api_key, not this URL.
    monkeypatch.setenv("STRIPE_LAMBDA_URL", "https://billing.test")

    DynamoDBClient(table_name="products").create(
        Motor(
            product_name="Paygate Motor",
            manufacturer="ABB",
            product_type="motor",
            part_number="MTR-PG",
            rated_power="500;W",
        )
    )

    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


def _wait_for(mock: MagicMock, deadline: float = 1.0) -> None:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if mock.call_count > 0:
            return
        time.sleep(0.01)


class TestSearchPaygate:
    def test_no_key_served_free_not_metered(self, paygate_client: TestClient) -> None:
        with (
            patch(
                "app.backend_py.src.middleware.paygate.stripe_client.verify_api_key"
            ) as verify,
            patch(
                "app.backend_py.src.middleware.paygate.stripe_client.report_query_usage"
            ) as report,
        ):
            resp = paygate_client.get("/api/v1/search?type=motor")
        assert resp.status_code == 200
        verify.assert_not_called()
        time.sleep(0.05)
        report.assert_not_called()

    def test_unknown_key_401_query_never_runs(self, paygate_client: TestClient) -> None:
        with (
            patch(
                "app.backend_py.src.middleware.paygate.stripe_client.verify_api_key",
                return_value={"valid": False},
            ),
            patch(
                "app.backend_py.src.middleware.paygate.stripe_client.report_query_usage"
            ) as report,
        ):
            resp = paygate_client.get(
                "/api/v1/search?type=motor", headers={"X-API-Key": "bad"}
            )
        assert resp.status_code == 401
        time.sleep(0.05)
        report.assert_not_called()

    def test_valid_key_no_active_sub_402(self, paygate_client: TestClient) -> None:
        with patch(
            "app.backend_py.src.middleware.paygate.stripe_client.verify_api_key",
            return_value={
                "valid": True,
                "user_id": "u-1",
                "subscription_status": "past_due",
            },
        ):
            resp = paygate_client.get(
                "/api/v1/search?type=motor", headers={"X-API-Key": "k"}
            )
        assert resp.status_code == 402

    def test_valid_active_key_meters_one_query(
        self, paygate_client: TestClient
    ) -> None:
        report = MagicMock(return_value=True)
        with (
            patch(
                "app.backend_py.src.middleware.paygate.stripe_client.verify_api_key",
                return_value={
                    "valid": True,
                    "user_id": "u-42",
                    "subscription_status": "active",
                },
            ),
            patch(
                "app.backend_py.src.middleware.paygate.stripe_client.report_query_usage",
                report,
            ),
        ):
            resp = paygate_client.get(
                "/api/v1/search?type=motor", headers={"X-API-Key": "k"}
            )
            assert resp.status_code == 200
            _wait_for(report)
        report.assert_called_once_with("u-42", 1)

    def test_failed_query_not_metered(self, paygate_client: TestClient) -> None:
        report = MagicMock(return_value=True)
        with (
            patch(
                "app.backend_py.src.middleware.paygate.stripe_client.verify_api_key",
                return_value={
                    "valid": True,
                    "user_id": "u-42",
                    "subscription_status": "active",
                },
            ),
            patch(
                "app.backend_py.src.middleware.paygate.stripe_client.report_query_usage",
                report,
            ),
        ):
            # Invalid product type → 400 from the search handler.
            resp = paygate_client.get(
                "/api/v1/search?type=not-a-type", headers={"X-API-Key": "k"}
            )
            assert resp.status_code == 400
            time.sleep(0.1)
        report.assert_not_called()

    def test_billing_outage_fails_open(self, paygate_client: TestClient) -> None:
        report = MagicMock()
        with (
            patch(
                "app.backend_py.src.middleware.paygate.stripe_client.verify_api_key",
                side_effect=RuntimeError("unreachable"),
            ),
            patch(
                "app.backend_py.src.middleware.paygate.stripe_client.report_query_usage",
                report,
            ),
        ):
            resp = paygate_client.get(
                "/api/v1/search?type=motor", headers={"X-API-Key": "k"}
            )
            assert resp.status_code == 200
            time.sleep(0.05)
        report.assert_not_called()


class TestApiKeyMintRoute:
    def test_401_without_token(self, paygate_client: TestClient) -> None:
        resp = paygate_client.post("/api/apikeys", json={})
        assert resp.status_code in (401, 403)

    def test_mint_uses_token_identity(
        self, paygate_client: TestClient, rsa_keys
    ) -> None:
        token = _make_token(rsa_keys, sub="user-123")
        with patch(
            "app.backend_py.src.routes.apikeys.stripe_client.create_api_key",
            return_value="sk_query_minted",
        ) as create:
            resp = paygate_client.post(
                "/api/apikeys", json={}, headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["api_key"] == "sk_query_minted"
        create.assert_called_once_with("user-123")

    def test_body_with_user_id_rejected(
        self, paygate_client: TestClient, rsa_keys
    ) -> None:
        token = _make_token(rsa_keys, sub="user-123")
        with patch(
            "app.backend_py.src.routes.apikeys.stripe_client.create_api_key"
        ) as create:
            resp = paygate_client.post(
                "/api/apikeys",
                json={"user_id": "someone-else"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 400
        create.assert_not_called()
