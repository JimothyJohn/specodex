"""Pydantic request/response models — JSON shape parity with stripe/src/models.rs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    NONE = "none"

    @classmethod
    def from_str(cls, raw: str | None) -> SubscriptionStatus:
        if raw is None:
            return cls.NONE
        if raw == "cancelled":
            return cls.CANCELED
        try:
            return cls(raw)
        except ValueError:
            return cls.NONE


class UserRecord(BaseModel):
    user_id: str
    stripe_customer_id: str
    subscription_id: str | None = None
    subscription_status: SubscriptionStatus = SubscriptionStatus.NONE
    created_at: str


class CheckoutRequest(BaseModel):
    user_id: str
    email: str | None = None


class CheckoutResponse(BaseModel):
    checkout_url: str


class UsageRequest(BaseModel):
    user_id: str
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)


class UsageResponse(BaseModel):
    total_tokens: int
    recorded: bool


class StatusResponse(BaseModel):
    user_id: str
    subscription_status: SubscriptionStatus
    stripe_customer_id: str | None = None


# --- Per-query API-key billing ----------------------------------------


class ApiKeyCreateRequest(BaseModel):
    # The Cognito sub of the authenticated user minting the key. The
    # backend extracts this from a verified JWT — the billing Lambda
    # trusts it the same way /checkout trusts the user_id it's handed.
    user_id: str


class ApiKeyCreateResponse(BaseModel):
    # Plaintext key, returned exactly once. Only its SHA-256 hash is
    # stored, so it cannot be recovered after this response.
    api_key: str


class ApiKeyVerifyRequest(BaseModel):
    api_key: str


class ApiKeyVerifyResponse(BaseModel):
    valid: bool
    user_id: str | None = None
    subscription_status: SubscriptionStatus = SubscriptionStatus.NONE


class QueryUsageRequest(BaseModel):
    user_id: str
    # Number of billable queries to report. Default 1 = one API call.
    quantity: int = Field(ge=0, default=1)


class QueryUsageResponse(BaseModel):
    quantity: int
    recorded: bool


class ErrorResponse(BaseModel):
    error: str
