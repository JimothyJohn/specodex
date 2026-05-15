"""JWT auth dependencies backed by Cognito.

FastAPI-native port of ``app/backend/src/middleware/auth.ts``.
Same contract, expressed through Depends() rather than express
middleware:

- ``require_auth``  — 401 unless a valid Cognito ID token is in the
                      Authorization header. Returns the AuthedUser.
- ``optional_auth`` — same verify, but missing token is fine
                      (returns None). Used on read endpoints that
                      personalise if signed in but don't require it.
- ``require_group`` — 403 unless the authed user is in the named
                      group. Stack after ``require_auth``.

The verifier is lazily built so config can be filled in by SSM
secrets fetchers post-import. If Cognito IDs aren't configured at
all, every authed request fails 503 — preferable to a noisy "auth
disabled" mode that silently lets everyone through.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

import httpx
from fastapi import Header, HTTPException, status
from jose import jwt
from jose.exceptions import JWTError


@dataclass(frozen=True)
class AuthedUser:
    sub: str
    email: str
    groups: tuple[str, ...]


def _cognito_config() -> tuple[Optional[str], Optional[str], str]:
    """Return ``(user_pool_id, client_id, region)`` from env.

    Reads at call time so SSM hydration can populate the env after
    module import (Lambda cold-start path). Both IDs missing means
    "auth not configured"; the dependencies translate that into 503.
    """

    return (
        os.environ.get("COGNITO_USER_POOL_ID") or None,
        os.environ.get("COGNITO_USER_POOL_CLIENT_ID") or None,
        os.environ.get("AWS_REGION", "us-east-1"),
    )


@lru_cache(maxsize=4)
def _fetch_jwks(region: str, user_pool_id: str) -> dict[str, Any]:
    """Fetch + cache the Cognito JWKS for a user pool.

    Cognito's JWKS effectively never rotates, so an LRU on
    (region, user_pool_id) is fine. Tests clear this between cases
    via ``_fetch_jwks.cache_clear()``.
    """

    url = (
        f"https://cognito-idp.{region}.amazonaws.com/"
        f"{user_pool_id}/.well-known/jwks.json"
    )
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    return response.json()


def _extract_bearer(header_value: Optional[str]) -> Optional[str]:
    if not header_value or not header_value.startswith("Bearer "):
        return None
    token = header_value[len("Bearer ") :].strip()
    return token or None


def _verify_token_sync(token: str) -> Optional[AuthedUser]:
    """Verify a Cognito ID token. Returns ``None`` if auth isn't
    configured; raises ``JWTError`` (or its subclasses) on invalid /
    expired tokens; returns a populated ``AuthedUser`` on success.
    """

    user_pool_id, client_id, region = _cognito_config()
    if not user_pool_id or not client_id:
        return None

    # Find the matching key by kid.
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")
    if not kid:
        raise JWTError("Token missing kid")

    jwks = _fetch_jwks(region, user_pool_id)
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        raise JWTError("Signing key not in JWKS")

    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    payload = jwt.decode(
        token,
        key,
        algorithms=[key.get("alg", "RS256")],
        audience=client_id,
        issuer=issuer,
    )

    # Mirror aws-jwt-verify's token_use=id check — the Express side
    # constructs the verifier with tokenUse: 'id'; we have to enforce
    # it manually since python-jose doesn't know about the claim.
    if payload.get("token_use") != "id":
        raise JWTError("token_use is not 'id'")

    return AuthedUser(
        sub=payload["sub"],
        email=payload.get("email", "") or "",
        groups=tuple(payload.get("cognito:groups", []) or []),
    )


async def require_auth(
    authorization: Optional[str] = Header(None),
) -> AuthedUser:
    """FastAPI dependency: 401 without a valid token, 503 if Cognito
    isn't configured on this deployment.
    """

    user_pool_id, client_id, _ = _cognito_config()
    if not user_pool_id or not client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured on this deployment",
        )

    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    try:
        user = _verify_token_sync(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured",
        )
    return user


async def optional_auth(
    authorization: Optional[str] = Header(None),
) -> Optional[AuthedUser]:
    """FastAPI dependency: returns the user if a valid token is
    presented, ``None`` otherwise. Failed verify also returns None
    — same best-effort semantics as the Express ``optionalAuth``.
    """

    token = _extract_bearer(authorization)
    if not token:
        return None
    try:
        return _verify_token_sync(token)
    except JWTError:
        return None


def require_group(group: str):
    """Build a FastAPI dependency that 403s unless the authed user is
    in ``group``. Stack after ``require_auth``:

        @router.get("/admin")
        async def admin(user = Depends(require_group('admin'))): ...
    """

    async def _guard(
        authorization: Optional[str] = Header(None),
    ) -> AuthedUser:
        user = await require_auth(authorization)
        if group not in user.groups:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Group '{group}' required",
            )
        return user

    return _guard


# Test hook — clear the cached JWKS so tests can swap in a fake key
# set between cases. Production code never needs this.
def _reset_jwks_cache_for_tests() -> None:
    _fetch_jwks.cache_clear()
