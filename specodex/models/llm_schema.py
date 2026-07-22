"""Pydantic → Gemini JSON schema converter for LLM structured-output extraction.

``to_gemini_schema`` emits the uppercase OpenAPI subset Gemini accepts via
``response_mime_type="application/json"`` + ``response_schema``.

ValueUnit / MinMaxUnit fields are emitted as OBJECT schemas of shape
``{"value": N, "unit": S}`` / ``{"min": N, "max": M, "unit": S}`` —
which is exactly the canonical in-memory representation the Pydantic
models use end-to-end.
"""

from __future__ import annotations

import typing
from typing import Any, Dict, List, Literal, Optional, Type, Union, get_args, get_origin

from pydantic import BaseModel

from specodex.models.common import (
    MinMaxUnit,
    MinMaxUnitMarker,
    ValueUnit,
    ValueUnitMarker,
    find_min_max_unit_marker,
    find_value_unit_marker,
)


# Fields the application injects after extraction (caller-provided context,
# auto-generated identifiers, computed bookkeeping). The LLM should NEVER
# emit these — they're either already known or will be overwritten.
EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "product_id",
        "product_name",
        "product_type",
        "product_family",
        "manufacturer",
        "PK",
        "SK",
        "datasheet_url",
        "pages",
        # Computed commercial estimates — produced by
        # specodex.pricing.inference with citation-backed provenance.
        # The LLM must never be asked to fill them.
        "price_estimate",
        "lead_time_estimate",
    }
)


def _unwrap_optional(annotation: Any) -> Any:
    """Strip outer ``Optional[...]`` / ``Union[X, None]`` wrappers."""
    origin = get_origin(annotation)
    if origin is Union or origin is typing.Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _value_unit_schema(description: Optional[str] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {
        "type": "OBJECT",
        "description": description
        or "A numeric value paired with its unit of measurement.",
        "properties": {
            "value": {
                "type": "NUMBER",
                "description": "Plain numeric value (no unit text, no qualifiers).",
            },
            "unit": {
                "type": "STRING",
                "description": "Unit of measurement (e.g., 'A', 'V', 'W', 'Nm').",
            },
        },
    }
    return schema


def _min_max_unit_schema(description: Optional[str] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {
        "type": "OBJECT",
        "description": description
        or "A numeric range paired with its shared unit of measurement.",
        "properties": {
            "min": {
                "type": "NUMBER",
                "description": "Lower bound of the range (plain number).",
            },
            "max": {
                "type": "NUMBER",
                "description": "Upper bound of the range (plain number).",
            },
            "unit": {
                "type": "STRING",
                "description": "Unit of measurement shared by min and max.",
            },
        },
    }
    return schema


def _scalar_schema(annotation: Any) -> Optional[Dict[str, Any]]:
    """Return a Gemini schema fragment for scalar or Literal annotations."""
    inner = _unwrap_optional(annotation)
    origin = get_origin(inner)

    if origin is Literal:
        return {
            "type": "STRING",
            "enum": [str(v) for v in get_args(inner)],
        }

    # Strip ``Annotated[...]`` to reveal the underlying scalar type. This
    # is how ValueUnit / MinMaxUnit normally look, but we check for those
    # by identity BEFORE reaching here, so any Annotated that lands here
    # is a non-ValueUnit alias and we can safely look through it.
    if hasattr(inner, "__metadata__"):
        inner = get_args(inner)[0]
        inner = _unwrap_optional(inner)

    if inner is str:
        return {"type": "STRING"}
    if inner is int:
        return {"type": "INTEGER"}
    if inner is float:
        return {"type": "NUMBER"}
    if inner is bool:
        return {"type": "BOOLEAN"}

    return None


def _annotation_markers(annotation: Any) -> tuple:
    """Return Annotated __metadata__ for annotation or its Optional-unwrapped form."""
    md = getattr(annotation, "__metadata__", None) or ()
    if md:
        return md
    inner = _unwrap_optional(annotation)
    return getattr(inner, "__metadata__", None) or ()


def _is_value_unit_annotation(annotation: Any) -> bool:
    if any(isinstance(m, ValueUnitMarker) for m in _annotation_markers(annotation)):
        return True
    inner = _unwrap_optional(annotation)
    return isinstance(inner, type) and issubclass(inner, ValueUnit)


def _is_min_max_unit_annotation(annotation: Any) -> bool:
    if any(isinstance(m, MinMaxUnitMarker) for m in _annotation_markers(annotation)):
        return True
    inner = _unwrap_optional(annotation)
    return isinstance(inner, type) and issubclass(inner, MinMaxUnit)


def _field_schema(annotation: Any) -> Optional[Dict[str, Any]]:
    """Build the Gemini schema fragment for one field annotation.

    Returns ``None`` if the annotation can't be represented (exotic
    nested types, BaseModel lists, etc.). Callers should skip the field
    entirely in that case.
    """
    # ValueUnit / MinMaxUnit (and all per-quantity narrowed aliases
    # derived from them) are Annotated[Optional[str], ...] in common.py.
    # Each carries a ValueUnitMarker / MinMaxUnitMarker in __metadata__;
    # detect by marker rather than identity so Pydantic's annotation
    # stripping doesn't defeat us.
    inner = _unwrap_optional(annotation)
    if _is_value_unit_annotation(annotation):
        return _value_unit_schema()
    if _is_min_max_unit_annotation(annotation):
        return _min_max_unit_schema()

    # List[X] — recurse into the item type.
    if get_origin(inner) in (list, List):
        args = get_args(inner)
        if len(args) != 1:
            return None
        item_type = args[0]
        # Special-case List[<ValueUnit-alias>] / List[<MinMaxUnit-alias>]
        # up front so recursion doesn't trip over the Annotated aliases.
        if _is_value_unit_annotation(item_type):
            return {"type": "ARRAY", "items": _value_unit_schema()}
        if _is_min_max_unit_annotation(item_type):
            return {"type": "ARRAY", "items": _min_max_unit_schema()}
        item_schema = _field_schema(item_type)
        if item_schema is None:
            return None
        return {"type": "ARRAY", "items": item_schema}

    # Nested BaseModel → recurse as an object, keeping ALL fields (no
    # EXCLUDED_FIELDS filtering inside nested submodels since those are
    # typically inert data-shapes like Dimensions).
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return to_gemini_schema(inner, as_array=False, include_excluded=True)

    # Scalar or Literal.
    return _scalar_schema(annotation)


def to_gemini_schema(
    model_class: Type[BaseModel],
    as_array: bool = True,
    include_excluded: bool = False,
) -> Dict[str, Any]:
    """Build a Gemini-compatible response schema dict from a Pydantic model.

    Args:
        model_class: the Pydantic model class to convert (e.g. ``Drive``).
        as_array: if True, wraps the object schema in an ``ARRAY`` so
            Gemini can emit one entry per product variant. Defaults to
            True since most calls extract catalogs with multiple variants.
        include_excluded: if True, do NOT filter out caller-injected
            fields (``product_name``, ``manufacturer``, etc.). Used for
            nested submodels where every field is part of the shape.

    The returned dict uses Gemini's uppercase type names (``STRING``,
    ``OBJECT``, etc.) and is directly usable as the ``response_schema``
    argument to ``google.genai``'s ``GenerateContentConfig``.
    """
    properties: Dict[str, Any] = {}
    for name, field in model_class.model_fields.items():
        if not include_excluded and name in EXCLUDED_FIELDS:
            continue
        # Pydantic lifts our ValueUnitMarker / MinMaxUnitMarker into
        # ``field.metadata`` for typed Annotated aliases (Voltage, Current,
        # ...). Plain ValueUnit / MinMaxUnit fields carry no marker but we
        # detect them by class identity in ``_is_*_annotation``.
        if find_value_unit_marker(field.metadata) or _is_value_unit_annotation(
            field.annotation
        ):
            schema = _value_unit_schema()
        elif find_min_max_unit_marker(field.metadata) or _is_min_max_unit_annotation(
            field.annotation
        ):
            schema = _min_max_unit_schema()
        else:
            schema = _field_schema(field.annotation)
        if schema is None:
            continue  # Silently skip fields we can't represent.
        # Prefer the pydantic Field description when the user set one;
        # otherwise keep whatever the helper produced.
        if field.description and "description" not in schema:
            schema = {**schema, "description": field.description}
        properties[name] = schema

    object_schema: Dict[str, Any] = {
        "type": "OBJECT",
        "properties": properties,
    }

    if as_array:
        return {
            "type": "ARRAY",
            "description": (
                "One entry per distinct product variant found in the "
                "document. Leave optional fields unset when the spec "
                "is genuinely absent from the document."
            ),
            "items": object_schema,
        }
    return object_schema
