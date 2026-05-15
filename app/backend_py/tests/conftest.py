"""Shared fixtures for app/backend_py tests.

Provides the moto-mocked DynamoDB table plus the Cognito JWT
fixtures (RSA keypair, JWKS shim, env vars) so each test module
can request them via standard pytest dependency injection. Keeping
the fixtures here — rather than re-exporting from test modules —
avoids ruff's F811 false-positive on the re-import-as-parameter
pattern.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any, Iterator

import boto3
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from moto import mock_aws

# Force AWS region + dummy creds before any boto3 client is imported.
# moto needs these at import time, not just at call time, on some
# platforms.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("DYNAMODB_TABLE_NAME", "products")


# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------


@pytest.fixture
def dynamodb_table() -> Iterator[object]:
    """Moto-mocked single-table DynamoDB matching the production schema."""

    with mock_aws():
        client = boto3.resource("dynamodb", region_name="us-east-1")
        table = client.create_table(
            TableName="products",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield table


# ---------------------------------------------------------------------------
# RSA + JWKS fixtures for Cognito auth tests
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
    """Set the Cognito env vars so require_auth fires the 401/200 paths
    instead of the no-config 503 path."""

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

    from app.backend_py.src.middleware import auth as auth_mod

    auth_mod._fetch_jwks.cache_clear()

    def fake_fetch_jwks(region: str, user_pool_id: str) -> dict[str, Any]:
        assert region == REGION
        assert user_pool_id == USER_POOL_ID
        return {"keys": [rsa_keys["jwk"]]}

    monkeypatch.setattr(auth_mod, "_fetch_jwks", fake_fetch_jwks)
    yield
    if hasattr(auth_mod._fetch_jwks, "cache_clear"):
        auth_mod._fetch_jwks.cache_clear()


def make_token(
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
    """Build a signed Cognito-shaped ID token. Exported as a plain
    function (not a fixture) so test modules can call it directly
    with whatever claims they need.
    """

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
