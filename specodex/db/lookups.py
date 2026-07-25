"""Paginated point-lookup helpers for DynamoDB.

DynamoDB applies ``Limit`` BEFORE ``FilterExpression``: a filtered scan or
query with ``Limit=1`` examines exactly one item and returns it only if that
single item happens to match the filter. A point-lookup written that way
misses its target on any table with more than one row. The same class of
miss occurs without ``Limit`` when the first 1 MB page holds no match and
``LastEvaluatedKey`` is ignored.

Every "find one row by attribute" path must go through these helpers, which
paginate until a match or table exhaustion.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def _first_match(
    operation: Callable[..., Dict[str, Any]], kwargs: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    if "Limit" in kwargs:
        raise ValueError(
            "Limit caps items examined before the filter runs, so a limited "
            "point-lookup silently misses matches. Remove Limit."
        )
    while True:
        response = operation(**kwargs)
        items = response.get("Items", [])
        if items:
            return items[0]
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return None
        kwargs = {**kwargs, "ExclusiveStartKey": last_key}


def scan_first_match(table: Any, **scan_kwargs: Any) -> Optional[Dict[str, Any]]:
    """Return the first item matching a filtered scan, or None.

    Paginates past filter-emptied pages until a match or table exhaustion.
    """
    return _first_match(table.scan, scan_kwargs)


def query_first_match(table: Any, **query_kwargs: Any) -> Optional[Dict[str, Any]]:
    """Return the first item matching a filtered query, or None.

    Paginates past filter-emptied pages until a match or partition exhaustion.
    """
    return _first_match(table.query, query_kwargs)
