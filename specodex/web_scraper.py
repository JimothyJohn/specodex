#!/usr/bin/env python3
"""CLI entry point for the product webpage scraper.

Fetches JS-rendered product pages via Playwright, extracts specs using
the same Gemini LLM pipeline as specodex, and pushes results to
DynamoDB. Operates in two modes:

  - **add** (default): create new product entries
  - **enrich**: fill null fields on an existing product without overwriting

Usage:
    web-scraper --url <URL> --type motor --manufacturer "Acme" --product-name "X100"
    web-scraper --url <URL> --type drive --manufacturer "Acme" --product-name "X100" --enrich
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from specodex.browser import PageContent, fetch_page
from specodex.config import SCHEMA_CHOICES
from specodex.db.dynamo import DynamoDBClient
from specodex.extract import call_llm_and_parse
from specodex.models.product import ProductBase
from specodex.quality import filter_products
from specodex.spec_rules import partition_plausible
from specodex.utils import validate_api_key, UUIDEncoder


logger: logging.Logger = logging.getLogger(__name__)

# Same namespace used by specodex.scraper for deterministic IDs.
PRODUCT_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


class _ElapsedFormatter(logging.Formatter):
    def __init__(self, fmt: str | None = None) -> None:
        super().__init__(fmt)
        self._start = time.time()

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        elapsed = record.created - self._start
        m, s = divmod(elapsed, 60)
        return f"{int(m)}:{int(s):02}"


_handler = logging.StreamHandler()
_handler.setFormatter(
    _ElapsedFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    handlers=[_handler],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize(s: str | None) -> str:
    """Lowercase, strip non-alphanumeric for deterministic ID generation."""
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", s.lower().strip())


def _build_structured_context(page: PageContent) -> str:
    """Format JSON-LD and metadata as extra context for the LLM prompt."""
    parts: list[str] = []
    if page.metadata.breadcrumbs:
        parts.append("Breadcrumbs: " + " > ".join(page.metadata.breadcrumbs))
    if page.metadata.description:
        parts.append(f"Page description: {page.metadata.description}")

    # Include Product-type JSON-LD verbatim — it often has price, SKU, specs.
    for ld in page.structured_data:
        ld_type = ld.get("@type", "")
        if ld_type in ("Product", "IndividualProduct", "ProductModel"):
            parts.append(
                f"Structured data (JSON-LD {ld_type}):\n{json.dumps(ld, indent=2)}"
            )

    return "\n\n".join(parts)


def _merge_products(existing: ProductBase, new: ProductBase) -> ProductBase:
    """Fill null fields on *existing* with values from *new*. Returns a new instance."""
    merged = existing.model_dump()
    new_data = new.model_dump()

    for key, value in new_data.items():
        if key in ("product_id", "PK", "SK", "product_type"):
            continue
        if merged.get(key) is None and value is not None:
            merged[key] = value
            logger.info("Enriched field '%s' with value from web scrape", key)

    return type(existing)(**merged)


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------


def process_page(
    client: DynamoDBClient,
    api_key: str,
    url: str,
    product_type: str,
    manufacturer: str,
    product_name: str,
    product_family: str = "",
    enrich: bool = False,
    output_path: Path | None = None,
) -> str:
    """Scrape a product page and push results to DynamoDB.

    Returns: "success", "skipped", or "failed".
    """
    # --- Fetch page ---
    try:
        page: PageContent = fetch_page(url)
    except RuntimeError as e:
        logger.error("Browser fetch failed: %s", e)
        return "failed"

    if not page.html:
        logger.error("No HTML content retrieved from %s", url)
        return "failed"

    # --- Build LLM input ---
    structured_ctx = _build_structured_context(page)
    html_for_llm = page.html
    if structured_ctx:
        html_for_llm = structured_ctx + "\n\n---\n\nPage content:\n" + html_for_llm

    context: dict[str, Any] = {
        "product_name": product_name,
        "manufacturer": manufacturer,
        "product_family": product_family,
        "datasheet_url": url,
        "pages": None,
    }

    # --- LLM extraction + parse (shared with PDF scraper via specodex.extract) ---
    logger.info("Sending %d chars to LLM for extraction", len(html_for_llm))
    try:
        parsed_models = call_llm_and_parse(
            html_for_llm, api_key, product_type, context, content_type="html"
        )
    except Exception as e:
        logger.error("LLM extraction or parse failed: %s", e)
        return "failed"

    if not parsed_models:
        logger.error("No valid products extracted")
        return "failed"

    # --- Deterministic IDs + dedup ---
    valid_models: list[Any] = []
    for model in parsed_models:
        model.datasheet_url = url

        norm_manuf = _normalize(model.manufacturer) or _normalize(manufacturer)
        norm_part = _normalize(model.part_number)
        norm_name = _normalize(model.product_name)

        if norm_manuf and norm_part:
            id_string = f"{norm_manuf}:{norm_part}"
        elif norm_manuf and norm_name:
            id_string = f"{norm_manuf}:{norm_name}"
            logger.warning(
                "Missing part number for '%s', using name for ID", model.product_name
            )
        else:
            logger.error("Cannot generate ID for '%s' — skipping", model.product_name)
            continue

        model.product_id = uuid.uuid5(PRODUCT_NAMESPACE, id_string)
        logger.info("Generated ID %s from key '%s'", model.product_id, id_string)

        # Check DB for existing
        existing = client.read(model.product_id, ProductBase)
        if existing and not enrich:
            logger.info(
                "Product %s already exists — skipping (use --enrich to update)",
                model.product_id,
            )
            continue

        if existing and enrich:
            model = _merge_products(existing, model)
            logger.info("Merged enrichment data for %s", model.product_id)

        valid_models.append(model)

    # --- Quality gate ---
    valid_models, rejected = filter_products(valid_models)
    if rejected:
        logger.warning("Dropped %d low-quality products", len(rejected))

    # Hold back physically implausible rows (unit slips) — see spec_rules.
    valid_models, held = partition_plausible(valid_models)
    if held:
        logger.warning("Held %d product(s) for review: implausible specs", len(held))

    if not valid_models:
        logger.error("No products passed quality filter")
        return "failed"

    # --- Output ---
    parsed_data = [item.model_dump() for item in valid_models]
    if output_path:
        try:
            output_path.write_text(
                json.dumps(parsed_data, indent=2, cls=UUIDEncoder), encoding="utf-8"
            )
            logger.info("Saved output to %s", output_path)
        except Exception as e:
            logger.warning("Could not save output: %s", e)

    # --- Push to DB ---
    success_count = client.batch_create(valid_models)
    failure_count = len(valid_models) - success_count
    logger.info("Pushed %d items to DynamoDB (%d failed)", success_count, failure_count)

    return "success" if success_count > 0 else "failed"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape product webpages and extract specs via Gemini AI.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("-u", "--url", required=True, help="Product page URL")
    parser.add_argument(
        "-t",
        "--type",
        required=True,
        choices=list(SCHEMA_CHOICES.keys()),
        help="Product type schema",
    )
    parser.add_argument("-m", "--manufacturer", required=True, help="Manufacturer name")
    parser.add_argument("-n", "--product-name", required=True, help="Product name")
    parser.add_argument(
        "-f", "--product-family", default="", help="Product family/series"
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Enrich existing product (fill nulls) instead of creating new",
    )
    parser.add_argument(
        "--x-api-key",
        help="Gemini API key (or set GEMINI_API_KEY env var)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output JSON file path",
    )

    args = parser.parse_args()

    api_key = args.x_api_key or os.environ.get("GEMINI_API_KEY")
    try:
        api_key = validate_api_key(api_key)
    except argparse.ArgumentTypeError as e:
        parser.error(str(e))

    client = DynamoDBClient()

    result = process_page(
        client=client,
        api_key=api_key,
        url=args.url,
        product_type=args.type,
        manufacturer=args.manufacturer,
        product_name=args.product_name,
        product_family=args.product_family,
        enrich=args.enrich,
        output_path=args.output,
    )

    if result == "failed":
        sys.exit(1)

    logger.info("Done — result: %s", result)


if __name__ == "__main__":
    main()
