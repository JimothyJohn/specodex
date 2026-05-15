"""Unit tests for the Cognito JWT auth dependencies.

Generates an RSA keypair in-process, exposes the public JWK via a
patched ``_fetch_jwks`` (cache cleared between cases), signs test
tokens with the matching private key, and exercises every branch
of ``require_auth`` / ``optional_auth`` / ``require_group``.

Mirrors the contracts pinned in
``app/backend/tests/auth.middleware.test.ts``:

- Missing / malformed Authorization header → 401.
- Valid token, no Cognito config → 503.
- Valid token, configured pool → 200 with user attached.
- Expired token → 401.
- Wrong audience / issuer → 401.
- ``token_use != 'id'`` (an access token) → 401.
- ``require_group('admin')`` returns 403 for non-admin, 200 for admin.
- ``optional_auth`` returns None for no token, populated user for valid.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Iterator

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.backend_py.src.middleware import auth as auth_mod
from app.backend_py.src.middleware.auth import (
    AuthedUser,
    optional_auth,
    require_auth,
    require_group,
)


# ---------------------------------------------------------------------------
# RSA key fixture + JWKS shim
# ---------------------------------------------------------------------------


KEY_ID = "test-kid-1"
USER_POOL_ID = "us-east-1_TEST"
CLIENT_ID = "test-client-id"
REGION = "us-east-1"


def _b64url_uint(value: int) -> str:
    n_bytes = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode("ascii")


@pytest.fixture(scope="module")
def rsa_keys() -> dict[str, Any]:
    """A fresh RSA keypair plus the matching public JWK."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kid": KEY_ID,
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "n": _b64url_uint(public_numbers.n),
        "e": _b64url_uint(public_numbers.e),
    }
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return {"private_pem": pem, "jwk": jwk}


@pytest.fixture
def configured_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", USER_POOL_ID)
    monkeypatch.setenv("COGNITO_USER_POOL_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AWS_REGION", REGION)
    yield


@pytest.fixture
def patched_jwks(
    rsa_keys: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Patch the JWKS fetcher to return our in-process JWK.

    Clears the lru_cache before AND after the test so other tests
    in the module don't see a poisoned cache.
    """

    auth_mod._fetch_jwks.cache_clear()

    def fake_fetch_jwks(region: str, user_pool_id: str) -> dict[str, Any]:
        assert region == REGION
        assert user_pool_id == USER_POOL_ID
        return {"keys": [rsa_keys["jwk"]]}

    monkeypatch.setattr(auth_mod, "_fetch_jwks", fake_fetch_jwks)
    yield
    auth_mod._fetch_jwks.cache_clear() if hasattr(
        auth_mod._fetch_jwks, "cache_clear"
    ) else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    rsa_keys: dict[str, Any],
    *,
    sub: str = "test-user",
    email: str = "test@example.com",
    groups: list[str] | None = None,
    expires_in: int = 3600,
    audience: str = CLIENT_ID,
    issuer: str | None = None,
    token_use: str = "id",
) -> str:
    now = int(time.time())
    if issuer is None:
        issuer = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
    claims = {
        "sub": sub,
        "email": email,
        "cognito:groups": groups or [],
        "iss": issuer,
        "aud": audience,
        "token_use": token_use,
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(
        claims,
        rsa_keys["private_pem"],
        algorithm="RS256",
        headers={"kid": KEY_ID, "alg": "RS256"},
    )


def _build_test_app() -> FastAPI:
    """Tiny FastAPI app exposing one endpoint per dependency."""

    app = FastAPI()

    @app.get("/private")
    async def private(user: AuthedUser = Depends(require_auth)) -> dict[str, Any]:
        return {"sub": user.sub, "email": user.email, "groups": list(user.groups)}

    @app.get("/maybe")
    async def maybe(
        user: AuthedUser | None = Depends(optional_auth),
    ) -> dict[str, Any]:
        if user is None:
            return {"authed": False}
        return {"authed": True, "sub": user.sub}

    @app.get("/admin")
    async def admin(
        user: AuthedUser = Depends(require_group("admin")),
    ) -> dict[str, Any]:
        return {"sub": user.sub}

    return app


# ---------------------------------------------------------------------------
# require_auth tests
# ---------------------------------------------------------------------------


class TestRequireAuth:
    def test_missing_token_returns_401(self, configured_env, patched_jwks) -> None:
        client = TestClient(_build_test_app())
        resp = client.get("/private")
        assert resp.status_code == 401
        assert "bearer" in resp.json()["detail"].lower()

    def test_malformed_header_returns_401(self, configured_env, patched_jwks) -> None:
        client = TestClient(_build_test_app())
        resp = client.get("/private", headers={"Authorization": "NotBearer xyz"})
        assert resp.status_code == 401

    def test_missing_cognito_config_returns_503(
        self, monkeypatch: pytest.MonkeyPatch, patched_jwks
    ) -> None:
        # Deliberately UNSET the cognito env to simulate
        # "auth not configured on this deployment".
        monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
        monkeypatch.delenv("COGNITO_USER_POOL_CLIENT_ID", raising=False)
        client = TestClient(_build_test_app())
        resp = client.get("/private", headers={"Authorization": "Bearer doesnt-matter"})
        assert resp.status_code == 503

    def test_valid_token_returns_user(
        self, configured_env, patched_jwks, rsa_keys
    ) -> None:
        token = _make_token(rsa_keys, sub="alice", email="alice@example.com")
        client = TestClient(_build_test_app())
        resp = client.get("/private", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["sub"] == "alice"
        assert body["email"] == "alice@example.com"
        assert body["groups"] == []

    def test_expired_token_returns_401(
        self, configured_env, patched_jwks, rsa_keys
    ) -> None:
        token = _make_token(rsa_keys, expires_in=-60)
        client = TestClient(_build_test_app())
        resp = client.get("/private", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_wrong_audience_returns_401(
        self, configured_env, patched_jwks, rsa_keys
    ) -> None:
        token = _make_token(rsa_keys, audience="some-other-client")
        client = TestClient(_build_test_app())
        resp = client.get("/private", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_wrong_issuer_returns_401(
        self, configured_env, patched_jwks, rsa_keys
    ) -> None:
        token = _make_token(rsa_keys, issuer="https://evil.example.com/")
        client = TestClient(_build_test_app())
        resp = client.get("/private", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_access_token_use_returns_401(
        self, configured_env, patched_jwks, rsa_keys
    ) -> None:
        """An access token (token_use=access) must not be accepted on
        an ID-token-only verifier — mirrors the Express verifier's
        tokenUse: 'id' configuration.
        """
        token = _make_token(rsa_keys, token_use="access")
        client = TestClient(_build_test_app())
        resp = client.get("/private", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_unsigned_garbage_returns_401(self, configured_env, patched_jwks) -> None:
        # A literal "not a JWT" — verifies that arbitrary input
        # surfaces as 401, not 500.
        client = TestClient(_build_test_app())
        resp = client.get(
            "/private", headers={"Authorization": "Bearer not.a.real.jwt"}
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# optional_auth tests
# ---------------------------------------------------------------------------


class TestOptionalAuth:
    def test_no_token_returns_none(self, configured_env, patched_jwks) -> None:
        client = TestClient(_build_test_app())
        resp = client.get("/maybe")
        assert resp.status_code == 200
        assert resp.json() == {"authed": False}

    def test_valid_token_returns_user(
        self, configured_env, patched_jwks, rsa_keys
    ) -> None:
        token = _make_token(rsa_keys, sub="bob")
        client = TestClient(_build_test_app())
        resp = client.get("/maybe", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json() == {"authed": True, "sub": "bob"}

    def test_bad_token_returns_none(self, configured_env, patched_jwks) -> None:
        """Mirrors Express's optionalAuth: failed verify silently falls
        back to anon. The Express version logged a warn for failed
        verifies; we trust JWT-decoding errors and don't.
        """

        client = TestClient(_build_test_app())
        resp = client.get(
            "/maybe", headers={"Authorization": "Bearer garbage.token.here"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"authed": False}


# ---------------------------------------------------------------------------
# require_group tests
# ---------------------------------------------------------------------------


class TestRequireGroup:
    def test_non_admin_returns_403(
        self, configured_env, patched_jwks, rsa_keys
    ) -> None:
        token = _make_token(rsa_keys, groups=["users"])
        client = TestClient(_build_test_app())
        resp = client.get("/admin", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_admin_returns_200(self, configured_env, patched_jwks, rsa_keys) -> None:
        token = _make_token(rsa_keys, sub="root", groups=["admin"])
        client = TestClient(_build_test_app())
        resp = client.get("/admin", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json() == {"sub": "root"}

    def test_no_token_returns_401(self, configured_env, patched_jwks) -> None:
        client = TestClient(_build_test_app())
        resp = client.get("/admin")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Smoke for the readonly middleware — see test_readonly_middleware.py for
# the full coverage; this just confirms a private endpoint reachable from
# the cognito-config'd app still returns 200 when readonly isn't on.
# ---------------------------------------------------------------------------


def test_extract_bearer_strips_whitespace() -> None:
    from app.backend_py.src.middleware.auth import _extract_bearer

    assert _extract_bearer("Bearer   token-value  ") == "token-value"
    assert _extract_bearer("") is None
    assert _extract_bearer(None) is None
    assert _extract_bearer("Basic abc") is None
