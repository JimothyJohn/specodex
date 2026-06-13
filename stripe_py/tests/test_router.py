from __future__ import annotations

from datetime import UTC, datetime

from billing.config import load_config
from billing.models import SubscriptionStatus, UserRecord
from billing.router import dispatch


def test_health(db):
    resp = dispatch(load_config(), db, "GET", "/health", {}, "")
    assert resp.status == 200
    assert resp.body == {"status": "ok", "mode": "test"}


def test_unknown_route_404(db):
    resp = dispatch(load_config(), db, "GET", "/elsewhere", {}, "")
    assert resp.status == 404
    assert resp.body == {"error": "Not found"}


def test_status_for_unknown_user(db):
    resp = dispatch(load_config(), db, "GET", "/status/ghost", {}, "")
    assert resp.status == 200
    assert resp.body["subscription_status"] == "none"
    assert resp.body["stripe_customer_id"] is None


def test_status_for_known_user(db):
    db.put_user(
        UserRecord(
            user_id="u-1",
            stripe_customer_id="cus_1",
            subscription_id="sub_1",
            subscription_status=SubscriptionStatus.ACTIVE,
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    resp = dispatch(load_config(), db, "GET", "/status/u-1", {}, "")
    assert resp.status == 200
    assert resp.body["subscription_status"] == "active"
    assert resp.body["stripe_customer_id"] == "cus_1"


def test_checkout_invalid_body_returns_400(db):
    resp = dispatch(load_config(), db, "POST", "/checkout", {}, "{}")
    assert resp.status == 400
    assert "Invalid request" in resp.body["error"]


def test_status_missing_user_id_returns_400(db):
    resp = dispatch(load_config(), db, "GET", "/status/", {}, "")
    assert resp.status == 400


def test_webhook_signature_header_case_insensitive(db, mocker):
    mocker.patch(
        "stripe.Webhook.construct_event",
        return_value={"type": "customer.created", "data": {"object": {}}},
    )
    resp = dispatch(
        load_config(),
        db,
        "POST",
        "/webhook",
        {"Stripe-Signature": "t=1,v1=ok"},
        "{}",
    )
    assert resp.status == 200
    assert resp.body == {"received": True}


# --- Per-query API-key billing routes ---------------------------------


def test_apikey_create_and_verify_roundtrip(db):
    db.put_user(
        UserRecord(
            user_id="u-key",
            stripe_customer_id="cus_k",
            subscription_id="sub_k",
            subscription_status=SubscriptionStatus.ACTIVE,
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    created = dispatch(load_config(), db, "POST", "/apikey", {}, '{"user_id": "u-key"}')
    assert created.status == 200
    key = created.body["api_key"]
    assert key.startswith("sk_query_")

    verified = dispatch(load_config(), db, "POST", "/apikey/verify", {}, f'{{"api_key": "{key}"}}')
    assert verified.status == 200
    assert verified.body["valid"] is True
    assert verified.body["user_id"] == "u-key"
    assert verified.body["subscription_status"] == "active"


def test_apikey_create_unknown_user_400(db):
    resp = dispatch(load_config(), db, "POST", "/apikey", {}, '{"user_id": "ghost"}')
    assert resp.status == 400
    assert "User not found" in resp.body["error"]


def test_apikey_create_invalid_body_400(db):
    resp = dispatch(load_config(), db, "POST", "/apikey", {}, "{}")
    assert resp.status == 400


def test_apikey_verify_unknown_key_is_200_invalid(db):
    resp = dispatch(load_config(), db, "POST", "/apikey/verify", {}, '{"api_key": "sk_query_nope"}')
    assert resp.status == 200
    assert resp.body["valid"] is False


def test_query_usage_dormant_without_price(db):
    db.put_user(
        UserRecord(
            user_id="u-q",
            stripe_customer_id="cus_q",
            subscription_id="sub_q",
            subscription_status=SubscriptionStatus.ACTIVE,
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    resp = dispatch(
        load_config(), db, "POST", "/usage/query", {}, '{"user_id": "u-q", "quantity": 1}'
    )
    assert resp.status == 200
    assert resp.body["recorded"] is False


def test_query_usage_invalid_body_400(db):
    resp = dispatch(load_config(), db, "POST", "/usage/query", {}, "{}")
    assert resp.status == 400
