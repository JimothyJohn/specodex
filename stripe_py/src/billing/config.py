"""Environment loader and test-mode hard guard.

Mirrors stripe/src/config.rs: required env vars are validated at load
time; refuses to load if STRIPE_SECRET_KEY is not a sk_test_ key. The
Lambda fails to initialise (CloudWatch shows the error) — same UX as
the Rust panic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_price_id: str
    users_table_name: str
    frontend_url: str
    # Optional metered price for per-API-query charging. When unset, the
    # per-query paygate is dormant: checkout adds no query line item and
    # /usage/query reports nothing. Set STRIPE_QUERY_PRICE_ID to a
    # metered price to switch it on. Keeping it optional means the
    # existing token-only deploy keeps working untouched.
    stripe_query_price_id: str | None = None

    @property
    def is_test_mode(self) -> bool:
        return self.stripe_secret_key.startswith("sk_test_")

    @property
    def per_query_billing_enabled(self) -> bool:
        return bool(self.stripe_query_price_id)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} must be set")
    return value


def load_config() -> Config:
    config = Config(
        stripe_secret_key=_required("STRIPE_SECRET_KEY"),
        stripe_webhook_secret=_required("STRIPE_WEBHOOK_SECRET"),
        stripe_price_id=_required("STRIPE_PRICE_ID"),
        users_table_name=os.environ.get("USERS_TABLE_NAME", "datasheetminer-users"),
        frontend_url=os.environ.get("FRONTEND_URL", "http://localhost:3000"),
        stripe_query_price_id=os.environ.get("STRIPE_QUERY_PRICE_ID") or None,
    )
    if not config.is_test_mode:
        raise ConfigError(
            "REFUSING TO START: STRIPE_SECRET_KEY is not a test key. Set sk_test_... to proceed."
        )
    return config
