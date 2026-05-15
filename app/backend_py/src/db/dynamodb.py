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
