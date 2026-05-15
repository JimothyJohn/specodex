"""Subscription route tests.

Stripe Lambda HTTP calls are mocked via patching the httpx calls in
``stripe_client``. The route layer is thin; we mostly pin the
``config`` response shape and the ``checkout`` body rejection.
"""

from __future__ import annotations

import importlib
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.backend_py.tests.conftest import make_token as _make_token


@pytest.fixture
def subscription_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_MODE", "admin")
    monkeypatch.setenv("NODE_ENV", "test")
    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


class TestConfigEndpoint:
    def test_billing_disabled_returns_false(
        self,
        subscription_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("STRIPE_LAMBDA_URL", raising=False)
        resp = subscription_client.get("/api/subscription/config")
        assert resp.status_code == 200
        assert resp.json()["data"] == {"billing_enabled": False}

    def test_billing_enabled_when_lambda_url_set(
        self,
        subscription_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("STRIPE_LAMBDA_URL", "https://billing.test/")
        resp = subscription_client.get("/api/subscription/config")
        assert resp.json()["data"] == {"billing_enabled": True}


class TestStatusEndpoint:
    def test_disabled_returns_sentinel(
        self,
        subscription_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        configured_env,
        patched_jwks,
        rsa_keys,
    ) -> None:
        monkeypatch.delenv("STRIPE_LAMBDA_URL", raising=False)
        token = _make_token(rsa_keys, sub="alice")
        resp = subscription_client.get(
            "/api/subscription/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == {
            "subscription_status": "none",
            "billing_enabled": False,
        }

    def test_enabled_proxies_through_stripe_client(
        self,
        subscription_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        configured_env,
        patched_jwks,
        rsa_keys,
    ) -> None:
        monkeypatch.setenv("STRIPE_LAMBDA_URL", "https://billing.test/")
        token = _make_token(rsa_keys, sub="alice")

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "user_id": "alice",
            "subscription_status": "active",
            "stripe_customer_id": "cus_abc",
        }
        with patch("httpx.get", return_value=fake_response):
            resp = subscription_client.get(
                "/api/subscription/status",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["subscription_status"] == "active"


class TestCheckoutEndpoint:
    def test_empty_body_succeeds(
        self,
        subscription_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        configured_env,
        patched_jwks,
        rsa_keys,
    ) -> None:
        monkeypatch.setenv("STRIPE_LAMBDA_URL", "https://billing.test/")
        token = _make_token(rsa_keys, sub="alice")

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "checkout_url": "https://stripe.test/c/sess_123"
        }
        with patch("httpx.post", return_value=fake_response):
            resp = subscription_client.post(
                "/api/subscription/checkout",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert "checkout_url" in resp.json()["data"]

    def test_body_with_user_id_rejected(
        self,
        subscription_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        configured_env,
        patched_jwks,
        rsa_keys,
    ) -> None:
        """Mirrors Express's z.object({}).strict() — identity is the
        token, never the body. Posting any field should 400 even if
        billing is disabled."""

        monkeypatch.setenv("STRIPE_LAMBDA_URL", "https://billing.test/")
        token = _make_token(rsa_keys, sub="alice")
        resp = subscription_client.post(
            "/api/subscription/checkout",
            json={"user_id": "attacker"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
