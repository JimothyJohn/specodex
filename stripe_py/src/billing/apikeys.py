"""API-key issuance and verification for per-query billing.

A key is a high-entropy random token shown to the user once; only its
SHA-256 hash is persisted (see ``UsersDb.put_api_key``). Verification
hashes the presented key, looks up the owner, and folds in the owner's
current subscription status so the caller can gate in one round trip.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from .db import UsersDb
from .models import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyVerifyRequest,
    ApiKeyVerifyResponse,
    SubscriptionStatus,
)

# Plaintext key format: ``sk_query_<43-char url-safe base64>``. The
# prefix makes leaked keys greppable in logs/secret scanners; 32 random
# bytes is 256 bits of entropy.
_KEY_PREFIX = "sk_query_"
_KEY_BYTES = 32


class ApiKeyError(RuntimeError):
    pass


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def create_api_key(db: UsersDb, request: ApiKeyCreateRequest) -> ApiKeyCreateResponse:
    """Mint a key for an existing user.

    The user must already have a billing record (i.e. have hit
    /checkout at least once) so the key resolves to a real Stripe
    customer. Subscription *activeness* is intentionally NOT required
    here — a user may mint a key and subscribe afterwards; the paygate
    re-checks subscription status on every query.
    """
    user = db.get_user(request.user_id)
    if user is None:
        raise ApiKeyError("User not found; complete checkout before minting an API key")

    api_key = _KEY_PREFIX + secrets.token_urlsafe(_KEY_BYTES)
    db.put_api_key(
        key_hash=_hash_key(api_key),
        owner_user_id=request.user_id,
        created_at=datetime.now(UTC).isoformat(),
    )
    return ApiKeyCreateResponse(api_key=api_key)


def verify_api_key(db: UsersDb, request: ApiKeyVerifyRequest) -> ApiKeyVerifyResponse:
    """Resolve a presented key to its owner + current subscription status.

    Always returns 200-shaped data (valid=False for unknown keys) so the
    backend can distinguish "bad key" from "billing service error" by
    HTTP status rather than by parsing. Never raises on a bad key.
    """
    owner_user_id = db.get_api_key_owner(_hash_key(request.api_key))
    if owner_user_id is None:
        return ApiKeyVerifyResponse(valid=False)

    user = db.get_user(owner_user_id)
    status = user.subscription_status if user else SubscriptionStatus.NONE
    return ApiKeyVerifyResponse(
        valid=True,
        user_id=owner_user_id,
        subscription_status=status,
    )
