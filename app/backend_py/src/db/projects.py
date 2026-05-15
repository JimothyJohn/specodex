"""DynamoDB access for user-owned Projects.

Port of ``app/backend/src/db/projects.ts``. Same single-table layout:

    PK = USER#{owner_sub}
    SK = PROJECT#{id}

The per-user partition scopes ``list`` queries without a GSI.
Product membership is embedded as a list (``product_refs``); fine
up to ~hundreds of items per project before the 400KB item cap
bites.

Uses the same ``DynamoDBClient.table`` resource-style API that the
backend's other DAL methods use, so tests can reuse the existing
moto fixture without standing up a separate client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from botocore.exceptions import ClientError

from specodex.db.dynamo import DynamoDBClient


def _project_key(owner_sub: str, project_id: str) -> dict[str, str]:
    return {"PK": f"USER#{owner_sub}", "SK": f"PROJECT#{project_id}"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectsService:
    """Composition over inheritance again — wraps the pipeline DAL's
    table so we don't subclass ``DynamoDBClient`` for the per-user
    partition.
    """

    def __init__(self, table_name: Optional[str] = None) -> None:
        self._client = (
            DynamoDBClient(table_name=table_name)
            if table_name is not None
            else DynamoDBClient()
        )
        self.table = self._client.table

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list(self, owner_sub: str) -> list[dict[str, Any]]:
        response = self.table.query(
            KeyConditionExpression=("PK = :pk AND begins_with(SK, :sk)"),
            ExpressionAttributeValues={
                ":pk": f"USER#{owner_sub}",
                ":sk": "PROJECT#",
            },
        )
        return list(response.get("Items", []))

    def get(self, owner_sub: str, project_id: str) -> Optional[dict[str, Any]]:
        response = self.table.get_item(Key=_project_key(owner_sub, project_id))
        return response.get("Item")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create(self, owner_sub: str, project: dict[str, Any]) -> None:
        item = {**_project_key(owner_sub, project["id"]), **project}
        # Belt for randomUUID's already-tiny collision odds.
        self.table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(SK)",
        )

    def rename(
        self, owner_sub: str, project_id: str, name: str
    ) -> Optional[dict[str, Any]]:
        try:
            result = self.table.update_item(
                Key=_project_key(owner_sub, project_id),
                UpdateExpression="SET #n = :name, updated_at = :ts",
                ConditionExpression="attribute_exists(SK)",
                ExpressionAttributeNames={"#n": "name"},
                ExpressionAttributeValues={":name": name, ":ts": _iso_now()},
                ReturnValues="ALL_NEW",
            )
            return result.get("Attributes")
        except ClientError as e:
            if (
                e.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                return None
            raise

    def delete(self, owner_sub: str, project_id: str) -> bool:
        try:
            self.table.delete_item(
                Key=_project_key(owner_sub, project_id),
                ConditionExpression="attribute_exists(SK)",
            )
            return True
        except ClientError as e:
            if (
                e.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                return False
            raise

    def add_product(
        self,
        owner_sub: str,
        project_id: str,
        ref: dict[str, str],
    ) -> Optional[dict[str, Any]]:
        """Idempotent — a duplicate (product_type, product_id) pair
        is silently ignored. Matches the UX of a dropdown checkbox.
        """

        project = self.get(owner_sub, project_id)
        if project is None:
            return None
        existing = project.get("product_refs") or []
        already_present = any(
            r.get("product_type") == ref["product_type"]
            and r.get("product_id") == ref["product_id"]
            for r in existing
        )
        if already_present:
            return project
        return self._replace_refs(owner_sub, project_id, [*existing, ref])

    def remove_product(
        self,
        owner_sub: str,
        project_id: str,
        ref: dict[str, str],
    ) -> Optional[dict[str, Any]]:
        project = self.get(owner_sub, project_id)
        if project is None:
            return None
        kept = [
            r
            for r in (project.get("product_refs") or [])
            if not (
                r.get("product_type") == ref["product_type"]
                and r.get("product_id") == ref["product_id"]
            )
        ]
        return self._replace_refs(owner_sub, project_id, kept)

    def _replace_refs(
        self,
        owner_sub: str,
        project_id: str,
        refs: list[dict[str, str]],
    ) -> Optional[dict[str, Any]]:
        try:
            result = self.table.update_item(
                Key=_project_key(owner_sub, project_id),
                UpdateExpression=("SET product_refs = :refs, updated_at = :ts"),
                ConditionExpression="attribute_exists(SK)",
                ExpressionAttributeValues={":refs": refs, ":ts": _iso_now()},
                ReturnValues="ALL_NEW",
            )
            return result.get("Attributes")
        except ClientError as e:
            if (
                e.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                return None
            raise
