from __future__ import annotations

from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from specodex.models.common import ValueUnit

# PDFs above this byte count are flagged as "large" — likely multi-family
# catalogs that need manual review before automatic processing.
LARGE_PDF_THRESHOLD = 10_000_000  # 10 MB


def classify_pdf_size(byte_count: int) -> str:
    """Return 'large' or 'standard' based on PDF byte count."""
    return "large" if byte_count >= LARGE_PDF_THRESHOLD else "standard"


class Datasheet(BaseModel):
    """
    Represents a datasheet document and its associated metadata.
    Separated from the Product model to allow independent existence and linking.
    """

    model_config = {"populate_by_name": True}

    # PK/SK are persistence-layer concerns — kept as plain @property so they
    # don't appear in `model_dump()` or the generated TS interface, but are
    # still readable as attributes for the DynamoDB writer (see
    # specodex/db/dynamo.py:_serialize_item which assigns them explicitly).
    # Per MODELGEN.md OQ1 (resolved 2026-05-07): "drop the computed-field
    # decorator and compute PK/SK on read in the API."
    @property
    def PK(self) -> str:
        return f"DATASHEET#{self.product_type.upper()}"

    @property
    def SK(self) -> str:
        return f"DATASHEET#{self.datasheet_id}"

    datasheet_id: UUID = Field(
        default_factory=uuid4, description="Unique identifier for the datasheet entry"
    )
    url: str = Field(..., description="URL to the datasheet")
    pages: Optional[List[int]] = Field(None, description="Relevant page numbers")

    # Shared product metadata
    product_type: str = Field(
        ..., description="Type of product (e.g., 'motor', 'drive')"
    )
    product_name: str = Field(..., description="Product name")
    product_family: Optional[str] = Field(
        None, description="Product family or sub-series"
    )
    manufacturer: str = Field(..., description="Manufacturer name")
    category: Optional[str] = Field(
        None, description="Category or type of the datasheet product"
    )

    # Pipeline tracking
    status: Optional[str] = Field(
        None,
        description="Pipeline state: triaged, approved, processing, processed, failed, blacklisted",
    )
    s3_key: Optional[str] = Field(
        None, description="S3 object key where the PDF is stored"
    )
    content_hash: Optional[str] = Field(
        None, description="SHA-256 hex digest of the PDF bytes for dedup"
    )
    failure_count: int = Field(
        0, description="Number of extraction failures; auto-blacklisted at 2"
    )
    spec_density: Optional[float] = Field(
        None, description="Estimated spec field coverage (0-1) from intake triage scan"
    )
    size_category: Optional[str] = Field(
        None,
        description="'large' (>=10MB, likely multi-family catalog) or 'standard'. "
        "Large datasheets may need manual review before processing.",
    )

    # Additional metadata
    release_year: Optional[int] = None
    warranty: Optional[ValueUnit] = None
