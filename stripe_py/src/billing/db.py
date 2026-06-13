"""DynamoDB wrapper for the datasheetminer-users table.

Mirrors stripe/src/db.rs. boto3 client is memoised at module scope so
warm Lambda invokes reuse the connection.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import boto3

from .models import SubscriptionStatus, UserRecord


@lru_cache(maxsize=1)
def _default_client():
    return boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


class UsersDb:
    def __init__(self, table_name: str, client: Any | None = None) -> None:
        self.table_name = table_name
        self.client = client or _default_client()

    def get_user(self, user_id: str) -> UserRecord | None:
        resp = self.client.get_item(
            TableName=self.table_name,
            Key={"user_id": {"S": user_id}},
        )
        item = resp.get("Item")
        return _record_from_item(item) if item else None

    def get_user_by_customer_id(self, customer_id: str) -> UserRecord | None:
        # TODO: add a GSI on stripe_customer_id when user count > ~5k.
        resp = self.client.scan(
            TableName=self.table_name,
            FilterExpression="stripe_customer_id = :cid",
            ExpressionAttributeValues={":cid": {"S": customer_id}},
        )
        items = resp.get("Items") or []
        return _record_from_item(items[0]) if items else None

    def put_user(self, record: UserRecord) -> None:
        item: dict[str, Any] = {
            "user_id": {"S": record.user_id},
            "stripe_customer_id": {"S": record.stripe_customer_id},
            "subscription_status": {"S": record.subscription_status.value},
            "created_at": {"S": record.created_at},
        }
        if record.subscription_id:
            item["subscription_id"] = {"S": record.subscription_id}
        self.client.put_item(TableName=self.table_name, Item=item)

    def update_subscription_status(
        self,
        user_id: str,
        subscription_id: str,
        status: SubscriptionStatus,
    ) -> None:
        self.client.update_item(
            TableName=self.table_name,
            Key={"user_id": {"S": user_id}},
            UpdateExpression="SET subscription_id = :sid, subscription_status = :status",
            ExpressionAttributeValues={
                ":sid": {"S": subscription_id},
                ":status": {"S": status.value},
            },
        )

    # --- API keys -----------------------------------------------------
    #
    # API-key records live in the same table under a synthetic partition
    # key ``apikey#<sha256-hex>`` so no extra table or GSI is needed.
    # Verification is a direct get_item on the hash (O(1), no scan), and
    # the prefix can never collide with a Cognito sub. Only the hash is
    # stored — the plaintext key is shown to the user once and is
    # unrecoverable, so a table leak doesn't expose usable keys.

    @staticmethod
    def _apikey_pk(key_hash: str) -> str:
        return f"apikey#{key_hash}"

    def put_api_key(self, key_hash: str, owner_user_id: str, created_at: str) -> None:
        self.client.put_item(
            TableName=self.table_name,
            Item={
                "user_id": {"S": self._apikey_pk(key_hash)},
                "owner_user_id": {"S": owner_user_id},
                "created_at": {"S": created_at},
            },
            # Don't clobber an existing record on the astronomically
            # unlikely hash collision — surface it instead of silently
            # reassigning a live key to a new owner.
            ConditionExpression="attribute_not_exists(user_id)",
        )

    def get_api_key_owner(self, key_hash: str) -> str | None:
        resp = self.client.get_item(
            TableName=self.table_name,
            Key={"user_id": {"S": self._apikey_pk(key_hash)}},
        )
        item = resp.get("Item")
        if not item:
            return None
        owner = item.get("owner_user_id")
        return owner.get("S") if owner else None


def _record_from_item(item: dict[str, Any]) -> UserRecord:
    def get_s(key: str, default: str | None = None) -> str | None:
        v = item.get(key)
        return v.get("S") if v else default

    return UserRecord(
        user_id=get_s("user_id") or "",
        stripe_customer_id=get_s("stripe_customer_id") or "",
        subscription_id=get_s("subscription_id"),
        subscription_status=SubscriptionStatus.from_str(get_s("subscription_status")),
        created_at=get_s("created_at") or "",
    )
