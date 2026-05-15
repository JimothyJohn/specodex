"""Auth route tests.

The Cognito proxy is mocked with ``unittest.mock.patch`` against the
``_cognito_client`` factory rather than via moto (moto's Cognito
support is functional but its happy-path responses don't carry the
``AuthenticationResult`` envelope the way we'd want; mocking the
client is simpler).
"""

from __future__ import annotations

import importlib
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient


@pytest.fixture
def configured_auth_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_MODE", "public")
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_TEST")
    monkeypatch.setenv("COGNITO_USER_POOL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


@pytest.fixture
def unconfigured_auth_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_CLIENT_ID", raising=False)
    import app.backend_py.src.main as main_mod

    importlib.reload(main_mod)
    yield TestClient(main_mod.app)


def _client_error(name: str) -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": name, "Message": "test"}},
        operation_name="MockOp",
    )


def _patch_cognito(stub: MagicMock):
    return patch("app.backend_py.src.routes.auth._cognito_client", return_value=stub)


# ---------------------------------------------------------------------------
# Configuration gate
# ---------------------------------------------------------------------------


class TestConfigurationGate:
    def test_register_503_without_client_id(
        self, unconfigured_auth_client: TestClient
    ) -> None:
        resp = unconfigured_auth_client.post(
            "/api/auth/register",
            json={"email": "a@b.co", "password": "Abcdefghij12"},
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_short_password_returns_422(
        self, configured_auth_client: TestClient
    ) -> None:
        resp = configured_auth_client.post(
            "/api/auth/register",
            json={"email": "a@b.co", "password": "Short1A"},
        )
        assert resp.status_code == 422

    def test_password_missing_uppercase_returns_422(
        self, configured_auth_client: TestClient
    ) -> None:
        resp = configured_auth_client.post(
            "/api/auth/register",
            json={"email": "a@b.co", "password": "alllowercase12"},
        )
        assert resp.status_code == 422

    def test_invalid_email_returns_422(
        self, configured_auth_client: TestClient
    ) -> None:
        resp = configured_auth_client.post(
            "/api/auth/register",
            json={"email": "not-an-email", "password": "Abcdefghij12"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Happy paths via mocked Cognito
# ---------------------------------------------------------------------------


class TestRegister:
    def test_success(self, configured_auth_client: TestClient) -> None:
        stub = MagicMock()
        stub.sign_up.return_value = {}
        with _patch_cognito(stub):
            resp = configured_auth_client.post(
                "/api/auth/register",
                json={"email": "a@b.co", "password": "Abcdefghij12"},
            )
        assert resp.status_code == 200
        stub.sign_up.assert_called_once()

    def test_username_exists_maps_to_409(
        self, configured_auth_client: TestClient
    ) -> None:
        stub = MagicMock()
        stub.sign_up.side_effect = _client_error("UsernameExistsException")
        with _patch_cognito(stub):
            resp = configured_auth_client.post(
                "/api/auth/register",
                json={"email": "a@b.co", "password": "Abcdefghij12"},
            )
        assert resp.status_code == 409


class TestLogin:
    def _stub_login_success(self) -> MagicMock:
        stub = MagicMock()
        stub.initiate_auth.return_value = {
            "AuthenticationResult": {
                "IdToken": "id-token-value",
                "AccessToken": "access-token-value",
                "RefreshToken": "refresh-token-value",
                "ExpiresIn": 3600,
            }
        }
        return stub

    def test_success_returns_tokens(self, configured_auth_client: TestClient) -> None:
        with _patch_cognito(self._stub_login_success()):
            resp = configured_auth_client.post(
                "/api/auth/login",
                json={"email": "a@b.co", "password": "Abcdefghij12"},
            )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["id_token"] == "id-token-value"
        assert body["refresh_token"] == "refresh-token-value"

    def test_not_authorized_maps_to_401(
        self, configured_auth_client: TestClient
    ) -> None:
        stub = MagicMock()
        stub.initiate_auth.side_effect = _client_error("NotAuthorizedException")
        with _patch_cognito(stub):
            resp = configured_auth_client.post(
                "/api/auth/login",
                json={"email": "a@b.co", "password": "Abcdefghij12"},
            )
        assert resp.status_code == 401

    def test_unconfirmed_user_maps_to_403(
        self, configured_auth_client: TestClient
    ) -> None:
        stub = MagicMock()
        stub.initiate_auth.side_effect = _client_error("UserNotConfirmedException")
        with _patch_cognito(stub):
            resp = configured_auth_client.post(
                "/api/auth/login",
                json={"email": "a@b.co", "password": "Abcdefghij12"},
            )
        assert resp.status_code == 403

    def test_missing_auth_result_returns_400(
        self, configured_auth_client: TestClient
    ) -> None:
        """MFA / NEW_PASSWORD_REQUIRED challenge — Express returns 400."""

        stub = MagicMock()
        stub.initiate_auth.return_value = {"ChallengeName": "MFA"}
        with _patch_cognito(stub):
            resp = configured_auth_client.post(
                "/api/auth/login",
                json={"email": "a@b.co", "password": "Abcdefghij12"},
            )
        assert resp.status_code == 400


class TestLogout:
    def test_already_revoked_returns_200(
        self, configured_auth_client: TestClient
    ) -> None:
        """Best-effort logout: NotAuthorizedException = already revoked,
        still return 200 so client-side logout proceeds.
        """

        stub = MagicMock()
        stub.revoke_token.side_effect = _client_error("NotAuthorizedException")
        with _patch_cognito(stub):
            resp = configured_auth_client.post(
                "/api/auth/logout", json={"refresh_token": "tok"}
            )
        assert resp.status_code == 200

    def test_success_returns_200(self, configured_auth_client: TestClient) -> None:
        stub = MagicMock()
        stub.revoke_token.return_value = {}
        with _patch_cognito(stub):
            resp = configured_auth_client.post(
                "/api/auth/logout", json={"refresh_token": "tok"}
            )
        assert resp.status_code == 200


class TestForgotReset:
    def test_forgot_success(self, configured_auth_client: TestClient) -> None:
        stub = MagicMock()
        stub.forgot_password.return_value = {}
        with _patch_cognito(stub):
            resp = configured_auth_client.post(
                "/api/auth/forgot", json={"email": "a@b.co"}
            )
        assert resp.status_code == 200

    def test_reset_success(self, configured_auth_client: TestClient) -> None:
        stub = MagicMock()
        stub.confirm_forgot_password.return_value = {}
        with _patch_cognito(stub):
            resp = configured_auth_client.post(
                "/api/auth/reset",
                json={
                    "email": "a@b.co",
                    "code": "123456",
                    "password": "Abcdefghij12",
                },
            )
        assert resp.status_code == 200
