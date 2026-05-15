"""DynamoDB access layer for the FastAPI backend.

Wraps ``specodex.db.dynamo.DynamoDBClient`` with the small handful of
aggregations the Express backend exposed but that aren't on the
pipeline DAL: ``get_categories`` (per-type counts + display names),
plus a thin ``list_by_type`` that picks the right Pydantic model
class from the auto-discovered registry in ``specodex.config``.

Single source of truth for "what product types exist" is still
``specodex/config.py:SCHEMA_CHOICES`` — this module derives from it,
never duplicates it. That keeps the new-product-type runbook from
growing back to six files (see ``CLAUDE.md`` "Adding a new product
type").
"""

from __future__ import annotations

from typing import Any, Optional

from specodex.config import SCHEMA_CHOICES
from specodex.db.dynamo import DynamoDBClient
from specodex.models.datasheet import Datasheet
from specodex.models.product import ProductBase


def format_display_name(product_type: str) -> str:
    """Mirror the Express ``formatDisplayName`` helper.

    ``motor`` → ``Motors``; ``robot_arm`` → ``Robot Arms``.
    """

    words = [w.capitalize() for w in product_type.split("_")]
    return " ".join(words) + "s"


class BackendDB:
    """Composition over inheritance — wraps the pipeline DAL and adds
    backend-shaped aggregations on top.

    Don't subclass ``DynamoDBClient`` here; the pipeline owns that
    class's contract and shouldn't have backend-only methods bolted
    on. The wrapper makes the seam explicit.
    """

    def __init__(self, table_name: Optional[str] = None) -> None:
        if table_name is None:
            self._service = DynamoDBClient()
        else:
            self._service = DynamoDBClient(table_name=table_name)

    @property
    def service(self) -> DynamoDBClient:
        return self._service

    # ------------------------------------------------------------------
    # Aggregations the Express backend exposed
    # ------------------------------------------------------------------

    def get_categories(self) -> list[dict[str, Any]]:
        """Return every registered product type with its row count and
        display name. Mirrors ``app/backend/src/db/dynamodb.ts:getCategories``.

        Zero-count types are included so the frontend can render the
        full catalog even before any rows exist for a new type.
        """

        out: list[dict[str, Any]] = []
        for product_type, model_class in SCHEMA_CHOICES.items():
            rows = self._service.list(model_class)
            out.append(
                {
                    "type": product_type,
                    "count": len(rows),
                    "display_name": format_display_name(product_type),
                }
            )
        out.sort(key=lambda c: c["type"])
        return out

    def list_by_type(
        self, product_type: str, limit: Optional[int] = None
    ) -> list[ProductBase]:
        """List products of a single type. ``all`` returns every type."""

        if product_type == "all":
            rows: list[ProductBase] = []
            for model_class in SCHEMA_CHOICES.values():
                rows.extend(self._service.list(model_class, limit=limit))
                if limit is not None and len(rows) >= limit:
                    return rows[:limit]
            return rows

        model_class = SCHEMA_CHOICES.get(product_type)
        if model_class is None:
            return []
        return self._service.list(model_class, limit=limit)

    def read_by_id(self, product_id: str, product_type: str) -> Optional[ProductBase]:
        """Single-product lookup. ``None`` for unknown type or missing row."""

        model_class = SCHEMA_CHOICES.get(product_type)
        if model_class is None:
            return None
        return self._service.read(product_id, model_class)

    # ------------------------------------------------------------------
    # Counts + summary aggregations
    # ------------------------------------------------------------------

    def count_by_type(self, product_type: str) -> int:
        if product_type == "all":
            return sum(self.count_by_type(t) for t in SCHEMA_CHOICES.keys())
        model_class = SCHEMA_CHOICES.get(product_type)
        if model_class is None:
            return 0
        return len(self._service.list(model_class))

    def count(self) -> dict[str, int]:
        """Mirror Express ``count()`` — per-type plus ``total``."""

        per_type: dict[str, int] = {}
        total = 0
        for product_type, model_class in SCHEMA_CHOICES.items():
            n = len(self._service.list(model_class))
            per_type[product_type] = n
            total += n
        per_type["total"] = total
        return per_type

    # ------------------------------------------------------------------
    # Unique-attribute aggregations
    # ------------------------------------------------------------------

    def _all_products(self) -> list[ProductBase]:
        rows: list[ProductBase] = []
        for model_class in SCHEMA_CHOICES.values():
            rows.extend(self._service.list(model_class))
        return rows

    def get_unique_manufacturers(self) -> list[str]:
        return sorted({p.manufacturer for p in self._all_products() if p.manufacturer})

    def get_unique_names(self) -> list[str]:
        return sorted({p.product_name for p in self._all_products() if p.product_name})

    # ------------------------------------------------------------------
    # Mutations — create / update / delete
    # ------------------------------------------------------------------

    def create(self, product: ProductBase) -> bool:
        return self._service.create(product)

    def batch_create(self, products: list[ProductBase]) -> int:
        if not products:
            return 0
        return self._service.batch_create(products)

    def update_by_id(
        self,
        product_id: str,
        product_type: str,
        updates: dict[str, Any],
    ) -> bool:
        """Read-modify-write update.

        The pipeline DAL's ``update`` takes a full Pydantic model;
        we adapt by reading the existing row, applying ``updates`` to
        the dict form, and re-validating before write. Field-level
        validation runs again — a malicious update payload can't slip
        a placeholder string past the Pydantic alias.
        """

        model_class = SCHEMA_CHOICES.get(product_type)
        if model_class is None:
            return False
        existing = self._service.read(product_id, model_class)
        if existing is None:
            return False
        merged = existing.model_dump(mode="json")
        # Don't let callers reassign the immutable identity fields.
        for protected in ("product_id", "PK", "SK", "product_type"):
            updates.pop(protected, None)
        merged.update(updates)
        try:
            updated = model_class(**merged)
        except Exception:
            return False
        return self._service.update(updated)

    def delete_by_id(self, product_id: str, product_type: str) -> bool:
        """Delete a product by ID, after confirming it exists.

        DynamoDB's ``delete_item`` is idempotent — deleting a missing
        row returns success. Express returned 404 in that case by
        doing a read-then-delete; mirror it here so callers can
        distinguish "deleted" from "wasn't there" via the boolean.
        """

        model_class = SCHEMA_CHOICES.get(product_type)
        if model_class is None:
            return False
        existing = self._service.read(product_id, model_class)
        if existing is None:
            return False
        return self._service.delete(product_id, model_class)

    # ------------------------------------------------------------------
    # Bulk delete by scan attribute
    # ------------------------------------------------------------------

    def _delete_by_scan(
        self, attribute_name: str, attribute_value: str
    ) -> dict[str, int]:
        """Scan, filter to PK starting with PRODUCT#, delete matches.

        Mirrors ``app/backend/src/db/dynamodb.ts:deleteByScan`` so the
        Express endpoint contract holds: returns ``{deleted, failed}``,
        scoped to Products (not Datasheets), one DynamoDB delete per
        match.
        """

        table = self._service.table
        response = table.scan(
            FilterExpression="#attr = :val",
            ExpressionAttributeNames={"#attr": attribute_name},
            ExpressionAttributeValues={":val": attribute_value},
            ProjectionExpression="PK, SK",
        )
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = table.scan(
                FilterExpression="#attr = :val",
                ExpressionAttributeNames={"#attr": attribute_name},
                ExpressionAttributeValues={":val": attribute_value},
                ProjectionExpression="PK, SK",
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))

        product_items = [
            i for i in items if str(i.get("PK", "")).startswith("PRODUCT#")
        ]

        deleted = 0
        failed = 0
        for item in product_items:
            try:
                table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
                deleted += 1
            except Exception:
                failed += 1
        return {"deleted": deleted, "failed": failed}

    def delete_by_part_number(self, part_number: str) -> dict[str, int]:
        return self._delete_by_scan("part_number", part_number)

    def delete_by_manufacturer(self, manufacturer: str) -> dict[str, int]:
        return self._delete_by_scan("manufacturer", manufacturer)

    def delete_by_product_name(self, name: str) -> dict[str, int]:
        return self._delete_by_scan("product_name", name)

    # ------------------------------------------------------------------
    # Datasheet CRUD
    # ------------------------------------------------------------------

    def list_datasheets(self) -> list[Datasheet]:
        return self._service.get_all_datasheets()

    def datasheet_exists(self, url: str) -> bool:
        return self._service.datasheet_exists(url)

    def create_datasheet(self, datasheet: Datasheet) -> bool:
        return self._service.create(datasheet)

    def delete_datasheet(self, datasheet_id: str, product_type: str) -> bool:
        """Delete a datasheet by ID + product_type.

        DynamoDB's delete_item is idempotent; mirror Express by
        doing a read-first existence check via scan-by-key so the
        404 path stays honest.
        """

        table = self._service.table
        pk = f"DATASHEET#{product_type.upper()}"
        sk = f"DATASHEET#{datasheet_id}"
        existing = table.get_item(Key={"PK": pk, "SK": sk}).get("Item")
        if not existing:
            return False
        try:
            table.delete_item(Key={"PK": pk, "SK": sk})
            return True
        except Exception:
            return False

    def update_datasheet(
        self,
        datasheet_id: str,
        product_type: str,
        updates: dict[str, Any],
    ) -> bool:
        """Partial update on a Datasheet — read-modify-write through
        the Pydantic model so the validator re-runs on the merged
        payload.
        """

        table = self._service.table
        pk = f"DATASHEET#{product_type.upper()}"
        sk = f"DATASHEET#{datasheet_id}"
        existing = table.get_item(Key={"PK": pk, "SK": sk}).get("Item")
        if not existing:
            return False
        # Drop the persistence-layer keys before round-tripping
        # through the Pydantic model — Datasheet computes them as
        # @property and rejects them as field inputs.
        clean = {k: v for k, v in existing.items() if k not in ("PK", "SK")}
        for protected in ("datasheet_id", "PK", "SK", "product_type"):
            updates.pop(protected, None)
        clean.update(updates)
        try:
            updated = Datasheet(**clean)
        except Exception:
            return False
        return self._service.create(updated)
