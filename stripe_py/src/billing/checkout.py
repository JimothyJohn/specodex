"""Create a Stripe Checkout session for a metered subscription."""

from __future__ import annotations

from datetime import UTC, datetime

import stripe

from .config import Config
from .db import UsersDb
from .models import CheckoutRequest, CheckoutResponse, SubscriptionStatus, UserRecord


class CheckoutError(RuntimeError):
    pass


def create_checkout_session(
    config: Config,
    db: UsersDb,
    request: CheckoutRequest,
) -> CheckoutResponse:
    if not config.is_test_mode:
        raise CheckoutError("Refusing to create checkout with live keys. Use test keys.")

    existing = db.get_user(request.user_id)
    if existing and existing.subscription_status == SubscriptionStatus.ACTIVE:
        raise CheckoutError("User already has an active subscription")

    if existing:
        customer_id = existing.stripe_customer_id
    else:
        customer = stripe.Customer.create(
            api_key=config.stripe_secret_key,
            email=request.email,
            metadata={"user_id": request.user_id},
        )
        customer_id = customer["id"]
        db.put_user(
            UserRecord(
                user_id=request.user_id,
                stripe_customer_id=customer_id,
                subscription_status=SubscriptionStatus.NONE,
                created_at=datetime.now(UTC).isoformat(),
            )
        )

    # Token price is always present; the query price joins as a second
    # metered item only when per-query billing is configured, so a paid
    # subscription carries one item per usage stream.
    line_items: list[dict[str, str]] = [{"price": config.stripe_price_id}]
    if config.per_query_billing_enabled:
        line_items.append({"price": config.stripe_query_price_id})

    session = stripe.checkout.Session.create(
        api_key=config.stripe_secret_key,
        mode="subscription",
        customer=customer_id,
        success_url=config.frontend_url,
        cancel_url=config.frontend_url,
        line_items=line_items,
    )
    if not session.get("url"):
        raise CheckoutError("No checkout URL returned")
    return CheckoutResponse(checkout_url=session["url"])
