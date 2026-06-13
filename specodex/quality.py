"""Product data quality scoring and filtering.

Scores extracted products by how many spec fields are populated vs None.
Rejects products below a configurable threshold to prevent low-quality
entries from polluting the database.
"""

from __future__ import annotations

import logging
from typing import List, Tuple, Type

from specodex.models.product import ProductBase
from specodex.placeholders import is_placeholder


logger: logging.Logger = logging.getLogger(__name__)

# Fields that exist on every product but aren't "specs" — they come from
# context or are auto-generated, so they shouldn't count toward quality.
_META_FIELDS = frozenset(
    {
        "product_id",
        "product_type",
        "product_name",
        "product_family",
        "manufacturer",
        "PK",
        "SK",
        "datasheet_url",
        "pages",
        "msrp_source_url",
        "msrp_fetched_at",
        # Lead time is sourced from distributor / manufacturer data, not
        # the datasheet — the LLM extraction will almost never populate
        # it, so counting it as a "spec" would unfairly tank every score.
        "lead_time",
        # Availability is a per-seller stock snapshot scraped from
        # distributor pages (availability-enrich), never from the
        # datasheet — same rationale as lead_time. Its provenance fields
        # are pure metadata.
        "availability",
        "availability_source_url",
        "availability_fetched_at",
    }
)

# Minimum fraction of spec fields that must be populated (0.0–1.0).
DEFAULT_MIN_QUALITY = 0.25


def spec_fields_for_model(model_class: Type[ProductBase]) -> list[str]:
    """Return the names of spec-only fields (excluding metadata) for a model class."""
    return [name for name in model_class.model_fields if name not in _META_FIELDS]


def score_product(product: ProductBase) -> Tuple[float, int, int, list[str]]:
    """Score a product's data completeness.

    Args:
        product: A validated product model instance.

    Returns:
        Tuple of (score 0.0–1.0, filled_count, total_count, missing_fields).
    """
    fields = spec_fields_for_model(type(product))
    total = len(fields)
    if total == 0:
        return 1.0, 0, 0, []

    missing: list[str] = []
    for name in fields:
        value = getattr(product, name, None)
        # Placeholder strings like "N/A" or "TBD" count as missing, not filled.
        # Without this check, a record whose LLM extraction punted on every
        # field with "N/A" would score 100% and pass the quality gate.
        if is_placeholder(value):
            missing.append(name)

    filled = total - len(missing)
    score = filled / total
    return score, filled, total, missing


def filter_products(
    products: List[ProductBase],
    min_quality: float = DEFAULT_MIN_QUALITY,
) -> Tuple[List[ProductBase], List[ProductBase]]:
    """Partition products into those passing and failing the quality threshold.

    Args:
        products: List of validated product models.
        min_quality: Minimum score (0.0–1.0) to pass. Default 0.25.

    Returns:
        Tuple of (passed, rejected) product lists.
    """
    passed: list[ProductBase] = []
    rejected: list[ProductBase] = []

    for product in products:
        score, filled, total, missing = score_product(product)
        part_id = product.part_number or product.product_name

        if score >= min_quality:
            passed.append(product)
            logger.info(
                "Quality PASS: '%s' — %d/%d fields (%.0f%%)",
                part_id,
                filled,
                total,
                score * 100,
            )
        else:
            rejected.append(product)
            logger.warning(
                "Quality FAIL: '%s' — %d/%d fields (%.0f%%). Missing: %s",
                part_id,
                filled,
                total,
                score * 100,
                ", ".join(missing),
            )

    if rejected:
        logger.warning(
            "Rejected %d/%d products below %.0f%% quality threshold",
            len(rejected),
            len(products),
            min_quality * 100,
        )

    return passed, rejected
