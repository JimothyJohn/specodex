"""Per-query usage reporting — feature flag, gating, and Stripe call."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from billing.config import load_config
from billing.models import (
    QueryUsageRequest,
    SubscriptionStatus,
    UserRecord,
)
from billing.query_usage import QueryUsageError, report_query_usage


def _active_user(db, user_id="user-q", sub_id="sub_q"):
    db.put_user(
        UserRecord(
            user_id=user_id,
            stripe_customer_id="cus_q",
            subscription_id=sub_id,
            subscription_status=SubscriptionStatus.ACTIVE,
            created_at=datetime.now(UTC).isoformat(),
        )
    )


def _config_with_query_price(monkeypatch, price="price_query"):
    monkeypatch.setenv("STRIPE_QUERY_PRICE_ID", price)
    return load_config()


def test_disabled_when_query_price_unset(db):
    # No STRIPE_QUERY_PRICE_ID → dormant: accepts but records nothing.
    _active_user(db)
    out = report_query_usage(load_config(), db, QueryUsageRequest(user_id="user-q"))
    assert out.recorded is False
    assert out.quantity == 0


def test_zero_quantity_records_nothing(db, monkeypatch):
    _active_user(db)
    cfg = _config_with_query_price(monkeypatch)
    out = report_query_usage(cfg, db, QueryUsageRequest(user_id="user-q", quantity=0))
    assert out.recorded is False


def test_reports_to_the_query_priced_item(db, monkeypatch, mocker):
    _active_user(db)
    cfg = _config_with_query_price(monkeypatch, price="price_query")
    # Subscription has two metered items; only the query-priced one
    # should be reported against.
    mocker.patch(
        "stripe.Subscription.retrieve",
        return_value={
            "items": {
                "data": [
                    {"id": "si_tokens", "price": {"id": "price_dummy"}},
                    {"id": "si_query", "price": {"id": "price_query"}},
                ]
            }
        },
    )
    raw = mocker.patch("stripe.StripeClient.raw_request")

    out = report_query_usage(cfg, db, QueryUsageRequest(user_id="user-q", quantity=3))
    assert out.recorded is True
    assert out.quantity == 3
    # Reported against si_query (not si_tokens), quantity 3.
    args, kwargs = raw.call_args
    assert args[0] == "post"
    assert args[1] == "/v1/subscription_items/si_query/usage_records"
    assert kwargs["quantity"] == 3


def test_missing_query_item_raises(db, monkeypatch, mocker):
    _active_user(db)
    cfg = _config_with_query_price(monkeypatch, price="price_query")
    # Subscription exists but has no query-priced item.
    mocker.patch(
        "stripe.Subscription.retrieve",
        return_value={"items": {"data": [{"id": "si_tokens", "price": {"id": "price_dummy"}}]}},
    )
    with pytest.raises(QueryUsageError, match="no query-priced item"):
        report_query_usage(cfg, db, QueryUsageRequest(user_id="user-q"))


def test_unknown_user_raises(db, monkeypatch):
    cfg = _config_with_query_price(monkeypatch)
    with pytest.raises(QueryUsageError, match="User not found"):
        report_query_usage(cfg, db, QueryUsageRequest(user_id="ghost"))


def test_inactive_subscription_raises(db, monkeypatch):
    cfg = _config_with_query_price(monkeypatch)
    db.put_user(
        UserRecord(
            user_id="user-q",
            stripe_customer_id="cus_q",
            subscription_id="sub_q",
            subscription_status=SubscriptionStatus.PAST_DUE,
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    with pytest.raises(QueryUsageError, match="active subscription"):
        report_query_usage(cfg, db, QueryUsageRequest(user_id="user-q"))
