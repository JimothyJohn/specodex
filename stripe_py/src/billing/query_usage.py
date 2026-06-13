"""Report per-query usage to a user's metered query subscription item.

Separate from usage.py (which meters ingest *tokens* on the primary
subscription item): a paid user's subscription carries two metered
items — the token price and the query price — and each usage stream
reports to its own item, found by price id rather than by position.
"""

from __future__ import annotations

import time

import stripe

from .config import Config
from .db import UsersDb
from .models import QueryUsageRequest, QueryUsageResponse, SubscriptionStatus


class QueryUsageError(RuntimeError):
    pass


def _find_item_for_price(subscription: object, price_id: str) -> str | None:
    """Return the subscription-item id whose price matches price_id."""
    items = (subscription.get("items") or {}).get("data") or []  # type: ignore[attr-defined]
    for item in items:
        price = item.get("price") or {}
        if price.get("id") == price_id:
            return item["id"]
    return None


def report_query_usage(
    config: Config,
    db: UsersDb,
    request: QueryUsageRequest,
) -> QueryUsageResponse:
    if not config.per_query_billing_enabled:
        # Feature dormant — accept the call but record nothing, so the
        # backend paygate can be wired before the query price exists.
        return QueryUsageResponse(quantity=0, recorded=False)
    if request.quantity == 0:
        return QueryUsageResponse(quantity=0, recorded=False)

    user = db.get_user(request.user_id)
    if not user:
        raise QueryUsageError("User not found")
    if user.subscription_status != SubscriptionStatus.ACTIVE:
        raise QueryUsageError("User does not have an active subscription")
    if not user.subscription_id:
        raise QueryUsageError("User has no subscription ID")

    subscription = stripe.Subscription.retrieve(
        user.subscription_id,
        api_key=config.stripe_secret_key,
    )
    sub_item_id = _find_item_for_price(subscription, config.stripe_query_price_id)
    if sub_item_id is None:
        raise QueryUsageError("Subscription has no query-priced item")

    # Legacy metered-billing endpoint — same approach usage.py uses for
    # tokens; see the note there on why raw_request instead of the
    # removed SubscriptionItem helper.
    client = stripe.StripeClient(config.stripe_secret_key)
    client.raw_request(
        "post",
        f"/v1/subscription_items/{sub_item_id}/usage_records",
        quantity=request.quantity,
        timestamp=int(time.time()),
    )
    return QueryUsageResponse(quantity=request.quantity, recorded=True)
