"""API-key issuance + verification — including the adversarial paths."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from billing.apikeys import (
    ApiKeyError,
    _hash_key,
    create_api_key,
    verify_api_key,
)
from billing.models import (
    ApiKeyCreateRequest,
    ApiKeyVerifyRequest,
    SubscriptionStatus,
    UserRecord,
)


def _user(db, user_id="user-1", status=SubscriptionStatus.ACTIVE, sub_id="sub_1"):
    db.put_user(
        UserRecord(
            user_id=user_id,
            stripe_customer_id="cus_1",
            subscription_id=sub_id,
            subscription_status=status,
            created_at=datetime.now(UTC).isoformat(),
        )
    )


def test_create_requires_existing_user(db):
    with pytest.raises(ApiKeyError, match="User not found"):
        create_api_key(db, ApiKeyCreateRequest(user_id="ghost"))


def test_create_returns_prefixed_key_and_stores_only_hash(db):
    _user(db)
    resp = create_api_key(db, ApiKeyCreateRequest(user_id="user-1"))
    assert resp.api_key.startswith("sk_query_")
    # The plaintext key is NOT in the table — only its hash is.
    raw = db.client.get_item(
        TableName=db.table_name,
        Key={"user_id": {"S": f"apikey#{_hash_key(resp.api_key)}"}},
    )
    assert raw["Item"]["owner_user_id"]["S"] == "user-1"
    assert "api_key" not in raw["Item"]
    assert resp.api_key not in str(raw["Item"])


def test_create_allowed_without_active_subscription(db):
    # A user may mint a key then subscribe; activeness is checked per
    # query, not at issuance.
    _user(db, status=SubscriptionStatus.NONE, sub_id="")
    resp = create_api_key(db, ApiKeyCreateRequest(user_id="user-1"))
    assert resp.api_key.startswith("sk_query_")


def test_keys_are_unique_across_calls(db):
    _user(db)
    a = create_api_key(db, ApiKeyCreateRequest(user_id="user-1")).api_key
    b = create_api_key(db, ApiKeyCreateRequest(user_id="user-1")).api_key
    assert a != b


def test_verify_roundtrips_owner_and_status(db):
    _user(db)
    key = create_api_key(db, ApiKeyCreateRequest(user_id="user-1")).api_key
    out = verify_api_key(db, ApiKeyVerifyRequest(api_key=key))
    assert out.valid is True
    assert out.user_id == "user-1"
    assert out.subscription_status == SubscriptionStatus.ACTIVE


def test_verify_reflects_current_status_not_issue_time(db):
    # Key minted while inactive, then the subscription activates: verify
    # must report the *current* status, not a snapshot.
    _user(db, status=SubscriptionStatus.NONE, sub_id="")
    key = create_api_key(db, ApiKeyCreateRequest(user_id="user-1")).api_key
    db.update_subscription_status("user-1", "sub_1", SubscriptionStatus.ACTIVE)
    out = verify_api_key(db, ApiKeyVerifyRequest(api_key=key))
    assert out.subscription_status == SubscriptionStatus.ACTIVE


def test_verify_unknown_key_is_invalid_not_error(db):
    out = verify_api_key(db, ApiKeyVerifyRequest(api_key="sk_query_totally-made-up"))
    assert out.valid is False
    assert out.user_id is None
    assert out.subscription_status == SubscriptionStatus.NONE


def test_verify_empty_key_is_invalid(db):
    out = verify_api_key(db, ApiKeyVerifyRequest(api_key=""))
    assert out.valid is False


def test_verify_does_not_accept_the_stored_hash_as_a_key(db):
    # Defends the obvious attack: a table leak exposes the hash, not the
    # key. Presenting the hash must NOT authenticate (it hashes again).
    _user(db)
    key = create_api_key(db, ApiKeyCreateRequest(user_id="user-1")).api_key
    leaked_hash = _hash_key(key)
    out = verify_api_key(db, ApiKeyVerifyRequest(api_key=leaked_hash))
    assert out.valid is False


def test_verify_valid_key_for_deleted_user_is_valid_but_none_status(db):
    # Owner row vanished (e.g. GDPR delete) but the key record lingers:
    # the key resolves to an owner with no active subscription, so the
    # paygate will 402 rather than crash.
    _user(db)
    key = create_api_key(db, ApiKeyCreateRequest(user_id="user-1")).api_key
    db.client.delete_item(TableName=db.table_name, Key={"user_id": {"S": "user-1"}})
    out = verify_api_key(db, ApiKeyVerifyRequest(api_key=key))
    assert out.valid is True
    assert out.subscription_status == SubscriptionStatus.NONE


def test_one_users_key_never_resolves_to_another_user(db):
    # IDOR guard: keys are owner-scoped by construction.
    _user(db, user_id="alice")
    _user(db, user_id="bob")
    alice_key = create_api_key(db, ApiKeyCreateRequest(user_id="alice")).api_key
    out = verify_api_key(db, ApiKeyVerifyRequest(api_key=alice_key))
    assert out.user_id == "alice"
