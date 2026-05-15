"""Auth route handlers — proxies to Cognito via boto3.

Port of ``app/backend/src/routes/auth.ts``. Keeps the SDK off the
SPA bundle, gives one place to add rate-limiting and audit logging,
and means the frontend only talks to one origin.

Endpoints (all POST except /me):
- /register      — SignUp
- /confirm       — ConfirmSignUp (email verification code)
- /resend        — ResendConfirmationCode
- /login         — InitiateAuth (USER_PASSWORD_AUTH)
- /refresh       — InitiateAuth (REFRESH_TOKEN_AUTH)
- /logout        — RevokeToken (refresh-token revocation)
- /forgot        — ForgotPassword
- /reset         — ConfirmForgotPassword
- /me  (GET)     — returns the authed user, require_auth-gated
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator
from typing_extensions import Annotated

from app.backend_py.src.middleware.auth import AuthedUser, require_auth


router = APIRouter(prefix="/api/auth")
logger = logging.getLogger(__name__)


def _cognito_client():
    return boto3.client(
        "cognito-idp",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )


def _client_id() -> Optional[str]:
    return os.environ.get("COGNITO_USER_POOL_CLIENT_ID") or None


def _require_configured() -> str:
    cid = _client_id()
    if not cid:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured on this deployment",
        )
    return cid


# Cognito error name → (status, public-facing error) map. Pulled
# verbatim from the Express port so the frontend can keep its
# existing message table.
_COGNITO_ERROR_MAP: dict[str, tuple[int, str]] = {
    "UsernameExistsException": (409, "Account already exists"),
    "NotAuthorizedException": (401, "Invalid credentials"),
    "UserNotConfirmedException": (403, "Email not verified"),
    "CodeMismatchException": (400, "Invalid verification code"),
    "ExpiredCodeException": (400, "Verification code expired"),
    "InvalidPasswordException": (400, "Password does not meet policy"),
    "LimitExceededException": (429, "Too many attempts; try again later"),
    "TooManyRequestsException": (429, "Too many requests"),
    "UserNotFoundException": (404, "No account for that email"),
}


def _cognito_error(err: ClientError) -> HTTPException:
    name = err.response.get("Error", {}).get("Code", "UnknownError")
    mapped = _COGNITO_ERROR_MAP.get(name)
    if mapped:
        return HTTPException(status_code=mapped[0], detail=mapped[1])
    logger.error("[auth] unmapped Cognito error: %s", name)
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Authentication request failed",
    )


# ---------------------------------------------------------------------------
# Validation shapes — mirror the Express zod schemas
# ---------------------------------------------------------------------------


_PASSWORD_LOWER = re.compile(r"[a-z]")
_PASSWORD_UPPER = re.compile(r"[A-Z]")
_PASSWORD_DIGIT = re.compile(r"[0-9]")


class _PasswordMixin(BaseModel):
    password: Annotated[str, StringConstraints(min_length=12, max_length=256)]

    @field_validator("password")
    @classmethod
    def _password_policy(cls, v: str) -> str:
        if not _PASSWORD_LOWER.search(v):
            raise ValueError("Password must contain a lowercase letter")
        if not _PASSWORD_UPPER.search(v):
            raise ValueError("Password must contain an uppercase letter")
        if not _PASSWORD_DIGIT.search(v):
            raise ValueError("Password must contain a number")
        return v


class _RegisterBody(_PasswordMixin):
    email: EmailStr = Field(..., max_length=254)


class _ConfirmBody(BaseModel):
    email: EmailStr = Field(..., max_length=254)
    code: Annotated[str, StringConstraints(min_length=1, max_length=32)]


class _LoginBody(BaseModel):
    email: EmailStr = Field(..., max_length=254)
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class _RefreshBody(BaseModel):
    refresh_token: Annotated[str, StringConstraints(min_length=1)]


class _LogoutBody(_RefreshBody):
    pass


class _ForgotBody(BaseModel):
    email: EmailStr = Field(..., max_length=254)


class _ResendBody(_ForgotBody):
    pass


class _ResetBody(_PasswordMixin):
    email: EmailStr = Field(..., max_length=254)
    code: Annotated[str, StringConstraints(min_length=1, max_length=32)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/register")
def register(body: _RegisterBody = Body(...)) -> dict[str, Any]:
    cid = _require_configured()
    try:
        _cognito_client().sign_up(
            ClientId=cid,
            Username=body.email,
            Password=body.password,
            UserAttributes=[{"Name": "email", "Value": body.email}],
        )
    except ClientError as e:
        raise _cognito_error(e)
    return {
        "success": True,
        "data": {"message": "Verification code sent to email", "next": "confirm"},
    }


@router.post("/confirm")
def confirm(body: _ConfirmBody = Body(...)) -> dict[str, Any]:
    cid = _require_configured()
    try:
        _cognito_client().confirm_sign_up(
            ClientId=cid,
            Username=body.email,
            ConfirmationCode=body.code,
        )
    except ClientError as e:
        raise _cognito_error(e)
    return {"success": True, "data": {"message": "Email verified", "next": "login"}}


@router.post("/resend")
def resend(body: _ResendBody = Body(...)) -> dict[str, Any]:
    cid = _require_configured()
    try:
        _cognito_client().resend_confirmation_code(ClientId=cid, Username=body.email)
    except ClientError as e:
        raise _cognito_error(e)
    return {"success": True, "data": {"message": "Verification code resent"}}


@router.post("/login")
def login(body: _LoginBody = Body(...)) -> dict[str, Any]:
    cid = _require_configured()
    try:
        result = _cognito_client().initiate_auth(
            ClientId=cid,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": body.email,
                "PASSWORD": body.password,
            },
        )
    except ClientError as e:
        raise _cognito_error(e)

    auth = result.get("AuthenticationResult")
    if not auth:
        # MFA / NEW_PASSWORD_REQUIRED challenge — not yet supported.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Login required additional challenge (MFA not yet supported)",
        )
    return {
        "success": True,
        "data": {
            "id_token": auth.get("IdToken"),
            "access_token": auth.get("AccessToken"),
            "refresh_token": auth.get("RefreshToken"),
            "expires_in": auth.get("ExpiresIn"),
        },
    }


@router.post("/refresh")
def refresh(body: _RefreshBody = Body(...)) -> dict[str, Any]:
    cid = _require_configured()
    try:
        result = _cognito_client().initiate_auth(
            ClientId=cid,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": body.refresh_token},
        )
    except ClientError as e:
        raise _cognito_error(e)

    auth = result.get("AuthenticationResult")
    if not auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh failed"
        )
    return {
        "success": True,
        "data": {
            "id_token": auth.get("IdToken"),
            "access_token": auth.get("AccessToken"),
            "expires_in": auth.get("ExpiresIn"),
        },
    }


@router.post("/logout")
def logout(body: _LogoutBody = Body(...)) -> dict[str, Any]:
    cid = _require_configured()
    try:
        _cognito_client().revoke_token(ClientId=cid, Token=body.refresh_token)
        return {
            "success": True,
            "data": {"message": "Refresh token revoked"},
        }
    except ClientError as e:
        # Best-effort: a token that was already revoked, expired, or
        # never valid still gets a 200 — client-side logout proceeds
        # regardless. NotAuthorizedException +
        # UnsupportedTokenTypeException are the expected "already-done"
        # codes per the Express comments.
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NotAuthorizedException", "UnsupportedTokenTypeException"):
            return {
                "success": True,
                "data": {"message": "Refresh token revoked (or already invalid)"},
            }
        raise _cognito_error(e)


@router.post("/forgot")
def forgot(body: _ForgotBody = Body(...)) -> dict[str, Any]:
    cid = _require_configured()
    try:
        _cognito_client().forgot_password(ClientId=cid, Username=body.email)
    except ClientError as e:
        raise _cognito_error(e)
    return {
        "success": True,
        "data": {"message": "Reset code sent if account exists"},
    }


@router.post("/reset")
def reset(body: _ResetBody = Body(...)) -> dict[str, Any]:
    cid = _require_configured()
    try:
        _cognito_client().confirm_forgot_password(
            ClientId=cid,
            Username=body.email,
            ConfirmationCode=body.code,
            Password=body.password,
        )
    except ClientError as e:
        raise _cognito_error(e)
    return {
        "success": True,
        "data": {"message": "Password reset; you can now log in"},
    }


@router.get("/me")
def me(user: AuthedUser = Depends(require_auth)) -> dict[str, Any]:
    return {
        "success": True,
        "data": {"sub": user.sub, "email": user.email, "groups": list(user.groups)},
    }
