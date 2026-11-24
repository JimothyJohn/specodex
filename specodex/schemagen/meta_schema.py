"""Meta-schema the LLM fills in when we ask it to propose a new product model.

The LLM returns a ``ProposedModel`` JSON object describing the class it
thinks should represent the datasheet: class name, product_type, optional
subtype discriminator values, and the ordered list of fields (name, kind,
unit, description, enum values). The renderer converts this structured
description into a ``.py`` file — the LLM never writes executable Python,
so we can't get invalid syntax out.
"""

from __future__ import annotations

import keyword
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


FieldKind = Literal[
    "int",
    "float",
    "str",
    "bool",
    "list_str",
    "literal",
    "value_unit",
    "min_max_unit",
]

# Field names already inherited from ProductBase. Proposing any of these
# would either collide with or shadow a base-class field — reject outright.
# Kept here (instead of reflected at import time) to avoid a circular import
# with specodex.models.product.
RESERVED_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "product_id",
        "product_type",
        "product_name",
        "product_family",
        "part_number",
        "manufacturer",
        "release_year",
        "dimensions",
        "weight",
        "msrp",
        "warranty",
        "datasheet_url",
        "pages",
        "PK",
        "SK",
        # "type" is the conventional subtype discriminator — we render it
        # ourselves from ProposedModel.subtype_values, so the LLM must not
        # propose it as a regular field.
        "type",
    }
)

# Cap Literal[...] enum size. Beyond this, downgrade to plain str — very
# long Literals are almost always the LLM enumerating values that vary too
# much to be an enum (e.g. every frame size on a catalog).
MAX_LITERAL_VALUES: int = 12


def _reject_unsafe_identifier(value: str, what: str) -> None:
    """Raise ``ValueError`` unless ``value`` is safe to emit as a bare name.

    The renderer interpolates ``ProposedField.name`` and
    ``ProposedModel.class_name`` into generated Python source *unquoted*, so
    anything that isn't a plain identifier either breaks the file
    (``"3phase"``, ``"rated current"``, ``"class"``) or — with an embedded
    newline — injects arbitrary statements into a module the CLI writes under
    ``specodex/models/`` and that ``config._discover_schema_models`` imports at
    startup. The LLM controls these strings, so they get gated here rather than
    trusted downstream.

    Leading underscores are rejected too: Pydantic treats ``_foo`` as a private
    attribute, so such a field would silently vanish from the model.
    """
    if not value.isidentifier():
        raise ValueError(
            f"{what} {value!r} is not a valid Python identifier. It is emitted "
            "as a bare name in the generated model source."
        )
    if keyword.iskeyword(value):
        raise ValueError(
            f"{what} {value!r} is a Python keyword and cannot be used as a name."
        )
    if value.startswith("_"):
        raise ValueError(
            f"{what} {value!r} starts with an underscore; Pydantic would treat "
            "it as a private attribute rather than a model field."
        )


class ProposedField(BaseModel):
    """A single field the LLM wants to add to the new product model."""

    name: str = Field(
        ...,
        description=(
            "snake_case field name. Must not collide with any field inherited "
            "from ProductBase (product_id, product_name, manufacturer, etc.)."
        ),
    )
    kind: FieldKind = Field(
        ...,
        description=(
            "Type of the field. 'value_unit' for a single numeric+unit pair "
            "(e.g. rated_current = 5 A). 'min_max_unit' for a range with the "
            "same unit on both ends (e.g. input_voltage = 100-240 V). "
            "'literal' for a closed enumeration — supply literal_values. "
            "'list_str' for an open list of tags. Prefer 'value_unit' over "
            "'float' whenever a physical unit is present."
        ),
    )
    description: str = Field(
        ...,
        min_length=1,
        description=(
            "One-line description of what this field represents. Shown in the "
            "extraction schema so the LLM knows what to populate. Keep it short."
        ),
    )
    unit: Optional[str] = Field(
        None,
        description=(
            "Canonical unit for kind='value_unit' or 'min_max_unit' fields. "
            "Use SI where possible (V, A, W, Nm, rpm, °C, mm). Required for "
            "unit-carrying kinds; must be null otherwise."
        ),
    )
    literal_values: Optional[List[str]] = Field(
        None,
        description=(
            "Closed set of allowed string values. Required iff kind='literal'. "
            "Must not exceed 12 entries — longer enumerations should be plain "
            "strings instead."
        ),
    )
    section: Optional[str] = Field(
        None,
        description=(
            "Short grouping label (e.g. 'Electrical', 'Mechanical', "
            "'Environmental'). Rendered as a '# --- <section> ---' comment "
            "above the field in the generated .py. Optional."
        ),
    )
    reused_from: Optional[List[str]] = Field(
        None,
        description=(
            "Populate when this field reuses a canonical name already used "
            "elsewhere in the repo. List of existing class names that have "
            "this field (e.g. ['Motor', 'Drive'] for rated_current). "
            "Informational — does not affect rendering."
        ),
    )

    @model_validator(mode="after")
    def _check_conditional_fields(self) -> "ProposedField":
        _reject_unsafe_identifier(self.name, "Field name")
        if self.name in RESERVED_FIELD_NAMES:
            raise ValueError(
                f"Field name {self.name!r} is reserved (inherited from "
                "ProductBase or the subtype discriminator). Pick a different "
                "name."
            )
        if self.kind in ("value_unit", "min_max_unit") and not self.unit:
            raise ValueError(
                f"Field {self.name!r} has kind={self.kind!r} but no unit. "
                "value_unit and min_max_unit fields must declare a canonical "
                "unit (e.g. 'V', 'A', 'Nm')."
            )
        if self.kind not in ("value_unit", "min_max_unit") and self.unit:
            raise ValueError(
                f"Field {self.name!r} has unit={self.unit!r} but kind={self.kind!r}. "
                "Only value_unit and min_max_unit fields carry a unit."
            )
        if self.kind == "literal":
            if not self.literal_values:
                raise ValueError(
                    f"Field {self.name!r} has kind='literal' but no "
                    "literal_values. Supply the closed set of allowed values."
                )
            if len(self.literal_values) > MAX_LITERAL_VALUES:
                # Downgrade to str — we don't raise here because we want to
                # salvage the field, just warn via the side channel the
                # caller will inspect via `downgraded_literals`.
                object.__setattr__(self, "kind", "str")
                object.__setattr__(self, "literal_values", None)
        elif self.literal_values is not None:
            raise ValueError(
                f"Field {self.name!r} has literal_values but kind={self.kind!r}. "
                "literal_values is only valid for kind='literal'."
            )
        return self


class ProposedSource(BaseModel):
    """One datasheet or reference that informed a ProposedModel.

    schemagen accepts one or more PDFs (and optionally reference
    documents like the relevant IEC / UL standard) and asks the LLM to
    generalize across them. Each source the LLM leaned on gets one
    entry here; the renderer cites them in the companion .md doc so
    the schema's provenance is auditable.
    """

    name: str = Field(
        ...,
        description=(
            "Human-readable identifier for this source — vendor + product "
            "line (e.g. 'ABB AF09-AF38 Technical Data') or standard "
            "reference (e.g. 'IEC 60947-4-1:2018')."
        ),
    )
    url: Optional[str] = Field(
        None,
        description=(
            "Public URL the source was fetched from, if applicable. "
            "Required when the LLM cites facts from the web."
        ),
    )
    local_path: Optional[str] = Field(
        None,
        description=(
            "Local filesystem path (relative to repo root) for PDFs "
            "fed in via --pdf. Empty when the source is a URL or a "
            "reference cited by name."
        ),
    )
    relevance_notes: Optional[str] = Field(
        None,
        description=(
            "Short note on what this source contributed to the schema "
            "(e.g. 'AC-3 headline-voltage convention', 'NEMA hp "
            "rating block'). Appears in the .md companion doc."
        ),
    )


class ProposedModel(BaseModel):
    """The full proposal: class metadata, field list, and optional
    reasoning + source citations for the companion .md doc."""

    class_name: str = Field(
        ...,
        description=(
            "PascalCase Python class name for the new model (e.g. 'Contactor', "
            "'ProgrammableLogicController')."
        ),
    )
    product_type: str = Field(
        ...,
        description=(
            "snake_case product_type identifier, matching the --type CLI "
            "argument. Becomes the Literal value on the generated class "
            "(e.g. 'contactor')."
        ),
    )
    docstring: str = Field(
        ...,
        min_length=1,
        description=(
            "One-paragraph class docstring summarizing what this product type "
            "is and its distinguishing characteristics."
        ),
    )
    subtype_values: Optional[List[str]] = Field(
        None,
        description=(
            "Values for the conventional 'type: Optional[Literal[...]]' "
            "subtype discriminator (mirrors Motor.type / Drive.type). Populate "
            "when the datasheet covers multiple variants of the same supertype "
            "(e.g. ['magnetic', 'solid-state', 'reversing'] for contactors). "
            "Leave null when the product type has no meaningful subtypes."
        ),
    )
    scope_notes: Optional[str] = Field(
        None,
        description=(
            "Short paragraph describing which products the schema is meant "
            "to cover and explicit non-goals (e.g. 'covers general-purpose "
            "industrial contactors per IEC 60947-4-1; excludes definite-"
            "purpose HVAC and vacuum contactors'). Rendered as the "
            "'Scope' section of the companion .md."
        ),
    )
    design_notes: Optional[str] = Field(
        None,
        description=(
            "Markdown paragraph(s) explaining the non-obvious design "
            "decisions — why a list-of-objects instead of scalar fields, "
            "why headline aliases, unit normalization traps the extractor "
            "will hit, etc. Rendered as the 'Design decisions' section of "
            "the companion .md. Cite specific sources by name."
        ),
    )
    sources: Optional[List[ProposedSource]] = Field(
        None,
        description=(
            "The datasheets / standards the LLM actually read to build "
            "this proposal. Every claim in `design_notes` should be "
            "attributable to at least one source listed here."
        ),
    )
    fields: List[ProposedField] = Field(
        ...,
        description=(
            "Ordered list of fields to add to the new model. Group "
            "semantically and set 'section' on the first field of each group."
        ),
    )

    @model_validator(mode="after")
    def _check_class_metadata(self) -> "ProposedModel":
        if not self.class_name or not self.class_name[0].isupper():
            raise ValueError(f"class_name must be PascalCase, got {self.class_name!r}")
        _reject_unsafe_identifier(self.class_name, "class_name")
        if not self.product_type or not self.product_type.replace("_", "").isalnum():
            raise ValueError(
                f"product_type must be snake_case alphanumeric, got "
                f"{self.product_type!r}"
            )
        if self.product_type != self.product_type.lower():
            raise ValueError(
                f"product_type must be all lowercase, got {self.product_type!r}"
            )
        # Reject duplicate field names outright.
        seen: set[str] = set()
        for f in self.fields:
            if f.name in seen:
                raise ValueError(f"Duplicate field name: {f.name!r}")
            seen.add(f.name)
        return self
