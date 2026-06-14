"""Pure dispatch — testable independent of the Lambda runtime."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .apikeys import ApiKeyError, create_api_key, verify_api_key
from .checkout import CheckoutError, create_checkout_session
from .config import Config
from .db import UsersDb
from .models import (
    ApiKeyCreateRequest,
    ApiKeyVerifyRequest,
    CheckoutRequest,
    ErrorResponse,
    QueryUsageRequest,
    StatusResponse,
    SubscriptionStatus,
    UsageRequest,
)
from .query_usage import QueryUsageError, report_query_usage
from .usage import UsageError, report_usage
from .webhook import WebhookError, handle_webhook

log = logging.getLogger(__name__)


@dataclass
class HttpResponse:
    status: int
    body: dict[str, Any]


def dispatch(
    config: Config,
    db: UsersDb,
    method: str,
    path: str,
    headers: dict[str, str],
    body: str,
) -> HttpResponse:
    log.info("Incoming request method=%s path=%s", method, path)

    if method == "POST" and path == "/checkout":
        return _handle_checkout(config, db, body)
    if method == "POST" and path == "/webhook":
        return _handle_webhook(config, db, headers, body)
    if method == "POST" and path == "/usage":
        return _handle_usage(config, db, body)
    if method == "POST" and path == "/usage/query":
        return _handle_query_usage(config, db, body)
    if method == "POST" and path == "/apikey":
        return _handle_apikey_create(db, body)
    if method == "POST" and path == "/apikey/verify":
        return _handle_apikey_verify(db, body)
    if method == "GET" and path.startswith("/status/"):
        user_id = path[len("/status/") :]
        return _handle_status(db, user_id)
    if method == "GET" and path == "/health":
        return HttpResponse(200, {"status": "ok", "mode": "test"})

    return HttpResponse(404, ErrorResponse(error="Not found").model_dump())


def _handle_checkout(config: Config, db: UsersDb, body: str) -> HttpResponse:
    try:
        request = CheckoutRequest.model_validate_json(body)
    except ValidationError as e:
        return HttpResponse(400, ErrorResponse(error=f"Invalid request: {e}").model_dump())
    try:
        return HttpResponse(200, create_checkout_session(config, db, request).model_dump())
    except CheckoutError as e:
        return HttpResponse(400, ErrorResponse(error=str(e)).model_dump())


def _handle_webhook(
    config: Config, db: UsersDb, headers: dict[str, str], body: str
) -> HttpResponse:
    signature = _get_header(headers, "stripe-signature")
    try:
        handle_webhook(config, db, signature, body)
        return HttpResponse(200, {"received": True})
    except WebhookError as e:
        return HttpResponse(400, ErrorResponse(error=str(e)).model_dump())


def _handle_usage(config: Config, db: UsersDb, body: str) -> HttpResponse:
    try:
        request = UsageRequest.model_validate_json(body)
    except ValidationError as e:
        return HttpResponse(400, ErrorResponse(error=f"Invalid request: {e}").model_dump())
    try:
        return HttpResponse(200, report_usage(config, db, request).model_dump())
    except UsageError as e:
        return HttpResponse(400, ErrorResponse(error=str(e)).model_dump())


def _handle_query_usage(config: Config, db: UsersDb, body: str) -> HttpResponse:
    try:
        request = QueryUsageRequest.model_validate_json(body)
    except ValidationError as e:
        return HttpResponse(400, ErrorResponse(error=f"Invalid request: {e}").model_dump())
    try:
        return HttpResponse(200, report_query_usage(config, db, request).model_dump())
    except QueryUsageError as e:
        return HttpResponse(400, ErrorResponse(error=str(e)).model_dump())


def _handle_apikey_create(db: UsersDb, body: str) -> HttpResponse:
    try:
        request = ApiKeyCreateRequest.model_validate_json(body)
    except ValidationError as e:
        return HttpResponse(400, ErrorResponse(error=f"Invalid request: {e}").model_dump())
    try:
        return HttpResponse(200, create_api_key(db, request).model_dump())
    except ApiKeyError as e:
        return HttpResponse(400, ErrorResponse(error=str(e)).model_dump())


def _handle_apikey_verify(db: UsersDb, body: str) -> HttpResponse:
    try:
        request = ApiKeyVerifyRequest.model_validate_json(body)
    except ValidationError as e:
        return HttpResponse(400, ErrorResponse(error=f"Invalid request: {e}").model_dump())
    # verify_api_key never raises on a bad key — it returns valid=False.
    return HttpResponse(200, verify_api_key(db, request).model_dump())


def _handle_status(db: UsersDb, user_id: str) -> HttpResponse:
    if not user_id:
        return HttpResponse(400, ErrorResponse(error="Missing user_id").model_dump())
    user = db.get_user(user_id)
    if user:
        return HttpResponse(
            200,
            StatusResponse(
                user_id=user.user_id,
                subscription_status=user.subscription_status,
                stripe_customer_id=user.stripe_customer_id,
            ).model_dump(),
        )
    return HttpResponse(
        200,
        StatusResponse(
            user_id=user_id,
            subscription_status=SubscriptionStatus.NONE,
            stripe_customer_id=None,
        ).model_dump(),
    )


def _get_header(headers: dict[str, str], name: str) -> str:
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v or ""
    return ""
