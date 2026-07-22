"""Commercial data models — sourced, confidence-scored figures.

Every commercial figure (a price estimate, a lead-time estimate, a
vendor fact) carries an explicit confidence tier and at least one
citation, so any displayed number is replicable:

- Web-sourced figures cite URL + retrieval time + a verbatim excerpt
  the reader can find on the page.
- DB-inferred figures cite the sorted comparable product IDs + the
  algorithm version, so a re-run against the same DB snapshot
  reproduces the figure exactly.

Facts only — these models deliberately have no slot for review scores,
sentiment, or any other non-replicable opinion. See todo/COMMERCIAL.md
for the full design.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from specodex.models.common import MinMaxUnit, ValueUnit

# Where a figure came from, ordered roughly by the acquisition ladder
# in todo/COMMERCIAL.md. "db_inference" is the only kind that cites
# comparables instead of a URL.
SourceKind = Literal[
    "oem",
    "distributor",
    "aggregator",
    "price_book",
    "shopping",
    "article",
    "vendor_doc",
    "db_inference",
]

# Confidence tiers. "listed" is reserved for prices/terms published
# verbatim by the OEM or an authorized distributor; "inferred" marks
# DB-comparable estimates. An estimate must never carry "listed".
Confidence = Literal["listed", "high", "medium", "low", "inferred"]


class SourceCitation(BaseModel):
    """One receipt backing a commercial figure.

    Invariants (enforced):
    - ``db_inference`` citations carry ``comparable_ids`` and
      ``method_version`` (URL-less but replicable from the DB).
    - Every other kind carries a ``url``.
    """

    url: Optional[str] = Field(
        None, description="Source URL. Required for every kind except db_inference."
    )
    kind: SourceKind
    retrieved_at: str = Field(
        ..., description="ISO 8601 timestamp when the source was read."
    )
    excerpt: Optional[str] = Field(
        None,
        max_length=280,
        description=(
            "Verbatim quote from the source backing the figure. Used by "
            "the vendor-facts verify audit to confirm the source still "
            "says what we recorded."
        ),
    )
    comparable_ids: Optional[List[str]] = Field(
        None,
        description=(
            "db_inference only: sorted product_ids of the comparables the "
            "estimate was computed from."
        ),
    )
    method_version: Optional[str] = Field(
        None,
        description=(
            "db_inference only: versioned algorithm identifier "
            "(e.g. 'price-comps-v1') so estimates are reproducible."
        ),
    )

    @model_validator(mode="after")
    def _require_replicable(self) -> "SourceCitation":
        if self.kind == "db_inference":
            if not self.comparable_ids or not self.method_version:
                raise ValueError(
                    "db_inference citations must carry comparable_ids and "
                    "method_version — an estimate without its comparables "
                    "is not replicable"
                )
        elif not self.url:
            raise ValueError(
                f"{self.kind} citations must carry a url — a figure without "
                "its source is not replicable"
            )
        return self


class SourcedFigure(BaseModel):
    """A commercial value plus its confidence and receipts.

    ``value`` follows the site-wide ValueUnit shape ({value, unit}) —
    e.g. {"value": 1234.0, "unit": "USD"} for a price estimate or
    {"value": 6, "unit": "weeks"} for a lead time.
    """

    value: ValueUnit
    confidence: Confidence
    observed_range: Optional[MinMaxUnit] = Field(
        None,
        description=(
            "Dispersion across the sources/comparables (e.g. p25-p75 of "
            "the comparable pool). Same unit as value."
        ),
    )
    sources: List[SourceCitation] = Field(
        ...,
        min_length=1,
        description="At least one citation — no orphan figures.",
    )
