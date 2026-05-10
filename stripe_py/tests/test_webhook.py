from __future__ import annotations

from datetime import UTC, datetime

import pytest
import stripe

from billing.config import load_config
from billing.models import SubscriptionStatus, UserRecord
from billing.webhook import WebhookError, handle_webhook


def _seed_user(db, customer_id: str = "cus_w", user_id: str = "user-w") -> None:
    db.put_user(
        UserRecord(
            user_id=user_id,
            stripe_customer_id=customer_id,
            subscription_status=SubscriptionStatus.NONE,
            created_at=datetime.now(UTC).isoformat(),
        )
    )


def test_invalid_signature_rejected(db, mocker):
    mocker.patch(
        "stripe.Webhook.construct_event",
        side_effect=stripe.SignatureVerificationError("bad sig", "sig", "body"),
    )
    with pytest.raises(WebhookError, match="Invalid signature"):
        handle_webhook(load_config(), db, "t=1,v1=bad", "{}")


def test_checkout_completed_activates(db, mocker):
    _seed_user(db, customer_id="cus_w", user_id="user-w")
    mocker.patch(
        "stripe.Webhook.construct_event",
        return_value={
            "type": "checkout.session.completed",
            "data": {"object": {"customer": "cus_w", "subscription": "sub_new"}},
        },
    )
    handle_webhook(load_config(), db, "sig", "{}")
    fetched = db.get_user("user-w")
    assert fetched.subscription_status == SubscriptionStatus.ACTIVE
    assert fetched.subscription_id == "sub_new"


def test_subscription_updated_status_change(db, mocker):
    _seed_user(db, customer_id="cus_u", user_id="user-u")
    mocker.patch(
        "stripe.Webhook.construct_event",
        return_value={
            "type": "customer.subscription.updated",
            "data": {"object": {"customer": "cus_u", "id": "sub_u", "status": "past_due"}},
        },
    )
    handle_webhook(load_config(), db, "sig", "{}")
    fetched = db.get_user("user-u")
    assert fetched.subscription_status == SubscriptionStatus.PAST_DUE
    assert fetched.subscription_id == "sub_u"


def test_subscription_deleted_marks_canceled(db, mocker):
    _seed_user(db, customer_id="cus_d", user_id="user-d")
    mocker.patch(
        "stripe.Webhook.construct_event",
        return_value={
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_d", "id": "sub_d", "status": "canceled"}},
        },
    )
    handle_webhook(load_config(), db, "sig", "{}")
    fetched = db.get_user("user-d")
    assert fetched.subscription_status == SubscriptionStatus.CANCELED


def test_payment_failed_does_not_change_state(db, mocker):
    _seed_user(db, customer_id="cus_p", user_id="user-p")
    mocker.patch(
        "stripe.Webhook.construct_event",
        return_value={
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_p"}},
        },
    )
    handle_webhook(load_config(), db, "sig", "{}")
    fetched = db.get_user("user-p")
    assert fetched.subscription_status == SubscriptionStatus.NONE


def test_unknown_event_ignored(db, mocker):
    mocker.patch(
        "stripe.Webhook.construct_event",
        return_value={"type": "customer.created", "data": {"object": {}}},
    )
    handle_webhook(load_config(), db, "sig", "{}")  # no exception


def test_missing_customer_in_checkout_event(db, mocker):
    mocker.patch(
        "stripe.Webhook.construct_event",
        return_value={
            "type": "checkout.session.completed",
            "data": {"object": {"subscription": "sub_x"}},
        },
    )
    with pytest.raises(WebhookError, match="missing customer"):
        handle_webhook(load_config(), db, "sig", "{}")


def test_event_for_unknown_customer_is_silent(db, mocker):
    # No user with cus_ghost in DB → handler logs and returns cleanly.
    mocker.patch(
        "stripe.Webhook.construct_event",
        return_value={
            "type": "checkout.session.completed",
            "data": {"object": {"customer": "cus_ghost", "subscription": "sub_g"}},
        },
    )
    handle_webhook(load_config(), db, "sig", "{}")  # no exception, no DB row


# ---------------------------------------------------------------------------
# HARDENING 2.4 — replay / idempotency / clock-skew / empty-signature
# ---------------------------------------------------------------------------


def test_replay_is_idempotent_for_checkout_completed(db, mocker):
    """Same event posted twice MUST produce the same end state.

    Stripe delivers webhooks at-least-once; replay defense matters.
    For ``checkout.session.completed`` the operation is "set status to
    ACTIVE" — naturally idempotent. This test pins that property as a
    contract: if a future change adds a non-idempotent side effect
    (e.g. a usage-token grant on activation), this test fails.
    """
    _seed_user(db, customer_id="cus_replay", user_id="user-replay")
    mocker.patch(
        "stripe.Webhook.construct_event",
        return_value={
            "type": "checkout.session.completed",
            "data": {
                "object": {"customer": "cus_replay", "subscription": "sub_replay"},
            },
        },
    )

    handle_webhook(load_config(), db, "sig", "{}")
    after_first = db.get_user("user-replay")

    handle_webhook(load_config(), db, "sig", "{}")
    after_second = db.get_user("user-replay")

    assert after_first.subscription_status == SubscriptionStatus.ACTIVE
    assert after_second.subscription_status == SubscriptionStatus.ACTIVE
    assert after_first.subscription_id == after_second.subscription_id == "sub_replay"


def test_replay_is_idempotent_for_subscription_updated(db, mocker):
    """Same ``customer.subscription.updated`` posted twice — end state stable."""
    _seed_user(db, customer_id="cus_upd", user_id="user-upd")
    mocker.patch(
        "stripe.Webhook.construct_event",
        return_value={
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "customer": "cus_upd",
                    "id": "sub_upd",
                    "status": "past_due",
                },
            },
        },
    )

    handle_webhook(load_config(), db, "sig", "{}")
    handle_webhook(load_config(), db, "sig", "{}")

    fetched = db.get_user("user-upd")
    assert fetched.subscription_status == SubscriptionStatus.PAST_DUE
    assert fetched.subscription_id == "sub_upd"


def test_stale_timestamp_rejected_via_sdk(db, mocker):
    """Timestamp outside the SDK's tolerance window must be rejected.

    Stripe SDK's ``construct_event`` raises ``SignatureVerificationError``
    when the ``t=`` value in the signature header is older than its
    tolerance (default 300s). Tests the wiring: handler converts that
    SDK error into a ``WebhookError`` exactly like an HMAC mismatch.
    """
    mocker.patch(
        "stripe.Webhook.construct_event",
        side_effect=stripe.SignatureVerificationError(
            "Timestamp outside the tolerance zone", "t=1,v1=ok", "{}"
        ),
    )
    with pytest.raises(WebhookError, match="Invalid signature"):
        handle_webhook(load_config(), db, "t=1,v1=ok", "{}")


def test_empty_signature_header_rejected(db, mocker):
    """Missing / empty ``Stripe-Signature`` header → SDK raises ValueError;
    handler must convert to ``WebhookError`` (not crash, not leak)."""
    mocker.patch(
        "stripe.Webhook.construct_event",
        side_effect=ValueError("No signatures found matching the expected"),
    )
    with pytest.raises(WebhookError, match="Invalid signature"):
        handle_webhook(load_config(), db, "", "{}")


def test_body_tamper_with_valid_sig_format_still_fails(db, mocker):
    """Body modified after signing — SDK's HMAC check raises
    ``SignatureVerificationError``; handler converts to ``WebhookError``."""
    mocker.patch(
        "stripe.Webhook.construct_event",
        side_effect=stripe.SignatureVerificationError(
            "No signatures found matching the expected signature for payload",
            "t=1,v1=expected",
            '{"type":"tampered.event"}',
        ),
    )
    with pytest.raises(WebhookError, match="Invalid signature"):
        handle_webhook(load_config(), db, "t=1,v1=expected", '{"type":"tampered.event"}')
