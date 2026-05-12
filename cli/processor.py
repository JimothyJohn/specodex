"""
Queue processor — downloads PDFs from the S3 upload queue,
runs them through the specodex extraction pipeline,
and writes results to DynamoDB.

Usage:
    ./Quickstart process [--stage dev] [--once]

Zero external dependencies beyond what specodex already requires.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("processor")

# Resolved at import time so the module works standalone
ROOT = Path(__file__).resolve().parent.parent


def _get_s3_client():
    """Lazy-load boto3 S3 client."""
    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("s3", region_name=region)


def _get_dynamo_client():
    """Lazy-load the specodex DynamoDB client."""
    from specodex.db.dynamo import DynamoDBClient

    return DynamoDBClient()


def parse_datasheet_id_from_key(key: str) -> str | None:
    """Extract the datasheet_id from an S3 upload-queue key.

    Upload-queue keys are shaped ``<prefix>/<datasheet_id>/<filename>``
    where ``<prefix>`` is ``good_examples`` or ``queue``. This helper
    returns the datasheet_id segment, or ``None`` if the key is
    malformed (insufficient segments) or carries an empty id slot.

    Contract:
      * Returns ``None`` when ``key`` is not a ``str``.
      * Returns ``None`` when the key has fewer than 3 ``/``-separated
        segments (no id slot present).
      * Returns ``None`` when the id slot is empty
        (``queue//file.pdf``-style keys).
      * Otherwise returns ``parts[1]`` unchanged — no normalisation.

    The function never raises. Adversarial S3 keys (unicode,
    control characters, leading slashes) produce ``None`` or a
    string but never an exception.
    """
    if not isinstance(key, str):
        return None
    parts = key.split("/")
    if len(parts) < 3:
        return None
    datasheet_id = parts[1]
    if not datasheet_id:
        return None
    return datasheet_id


def list_queued(bucket: str) -> list[dict]:
    """List all objects under good_examples/ (primary) and queue/ (legacy) in S3."""
    s3 = _get_s3_client()
    items = []
    paginator = s3.get_paginator("list_objects_v2")
    for prefix in ("good_examples/", "queue/"):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.lower().endswith(".pdf"):
                    items.append({"key": key, "size": obj["Size"]})
    return items


def download_pdf(bucket: str, key: str) -> bytes:
    """Download a PDF from S3 and return the raw bytes."""
    s3 = _get_s3_client()
    resp = s3.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


def move_to_done(bucket: str, key: str) -> None:
    """Move an object from good_examples/ or queue/ to done/ prefix."""
    s3 = _get_s3_client()
    if key.startswith("good_examples/"):
        done_key = key.replace("good_examples/", "done/", 1)
    else:
        done_key = key.replace("queue/", "done/", 1)
    s3.copy_object(
        Bucket=bucket,
        CopySource={"Bucket": bucket, "Key": key},
        Key=done_key,
    )
    s3.delete_object(Bucket=bucket, Key=key)
    log.info(f"Moved {key} -> {done_key}")


def find_datasheet_record(dynamo, datasheet_id: str) -> dict | None:
    """Find a datasheet record by ID with status approved or queued."""

    import boto3
    from boto3.dynamodb.conditions import Attr

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "products-dev")
    dynamodb = boto3.resource(
        "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )
    table = dynamodb.Table(table_name)

    # Primary: look for approved (intake flow) then fall back to queued (legacy)
    resp = table.scan(
        FilterExpression=Attr("datasheet_id").eq(datasheet_id)
        & Attr("status").is_in(["approved", "queued"]),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def process_pdf(
    pdf_bytes: bytes,
    metadata: dict,
    api_key: str,
) -> str:
    """
    Run the extraction pipeline on raw PDF bytes.
    Returns: 'success', 'skipped', or 'failed'
    """
    from specodex.config import SCHEMA_CHOICES
    from specodex.llm import generate_content
    from specodex.utils import parse_gemini_response
    from specodex.quality import filter_products

    product_type = metadata["product_type"]
    manufacturer = metadata.get("manufacturer", "Unknown")
    product_name = metadata.get("product_name", "")
    product_family = metadata.get("product_family", "")
    pages = metadata.get("pages")

    if product_type not in SCHEMA_CHOICES:
        log.error(f"Unknown product type: {product_type}")
        return "failed"

    context = {
        "product_name": product_name,
        "manufacturer": manufacturer,
        "product_family": product_family,
        "pages": pages,
    }

    log.info(f"Extracting: {product_name} ({product_type}) by {manufacturer}")

    try:
        response: Any = generate_content(
            pdf_bytes, api_key, product_type, context, "pdf"
        )

        parsed_models = parse_gemini_response(
            response, SCHEMA_CHOICES[product_type], product_type, context
        )

        if not parsed_models:
            log.error("No valid data extracted from PDF")
            return "failed"

        # Generate deterministic IDs (same logic as scraper.py)
        import uuid
        import re

        PRODUCT_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

        def normalize(s: str | None) -> str:
            if not s:
                return ""
            return re.sub(r"[^a-z0-9]", "", s.lower().strip())

        valid_models = []
        dynamo = _get_dynamo_client()

        for model in parsed_models:
            model.pages = pages
            norm_mfg = normalize(model.manufacturer) or normalize(manufacturer)
            norm_pn = normalize(model.part_number)
            norm_name = normalize(model.product_name)

            if norm_mfg and norm_pn:
                id_string = f"{norm_mfg}:{norm_pn}"
            elif norm_mfg and norm_name:
                id_string = f"{norm_mfg}:{norm_name}"
            else:
                log.warning(
                    "Cannot generate ID — skipping. product_name=%r "
                    "manufacturer=%r part_number=%r",
                    model.product_name,
                    model.manufacturer,
                    getattr(model, "part_number", None),
                )
                continue

            model.product_id = uuid.uuid5(PRODUCT_NAMESPACE, id_string)

            if dynamo.read(model.product_id, type(model)):
                log.info(f"Product {model.product_id} already exists, skipping")
                continue

            valid_models.append(model)

        # Quality filter
        valid_models, rejected = filter_products(valid_models)
        if rejected:
            log.warning(f"Dropped {len(rejected)} low-quality products")

        if not valid_models:
            log.warning("No new products to insert after filtering")
            return "skipped"

        count = dynamo.batch_create(valid_models)
        log.info(f"Inserted {count}/{len(valid_models)} products into DynamoDB")

        return "success" if count > 0 else "failed"

    except Exception as e:
        log.error(f"Extraction failed: {e}")
        return "failed"


def update_datasheet_status(datasheet_id: str, status: str) -> None:
    """Update the status of a datasheet record (queued -> processed/failed)."""
    import boto3

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "products-dev")
    dynamodb = boto3.resource(
        "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )
    table = dynamodb.Table(table_name)

    # Find the record first to get PK/SK
    from boto3.dynamodb.conditions import Attr

    resp = table.scan(
        FilterExpression=Attr("datasheet_id").eq(datasheet_id),
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        return

    item = items[0]
    table.update_item(
        Key={"PK": item["PK"], "SK": item["SK"]},
        UpdateExpression="SET #s = :s, processed_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": status,
            ":t": __import__("datetime").datetime.now().isoformat(),
        },
    )


def run(bucket: str, *, once: bool = False, api_key: str | None = None) -> None:
    """Main processing loop."""
    from specodex.utils import validate_api_key

    key = api_key or os.environ.get("GEMINI_API_KEY")
    validated_key = validate_api_key(key)

    while True:
        items = list_queued(bucket)
        if not items:
            if once:
                log.info("Queue empty, exiting")
                return
            log.info("Queue empty, waiting 30s...")
            time.sleep(30)
            continue

        log.info(f"Found {len(items)} PDFs in queue")

        for item in items:
            s3_key = item["key"]
            # Extract datasheet_id from key: queue/{id}/{filename}.pdf
            datasheet_id = parse_datasheet_id_from_key(s3_key)
            if datasheet_id is None:
                log.warning(f"Unexpected key format: {s3_key}")
                continue

            # Look up metadata from DynamoDB
            record = find_datasheet_record(None, datasheet_id)
            if not record:
                log.warning(f"No queued record for {datasheet_id}, skipping")
                continue

            log.info(f"Processing {s3_key} (datasheet={datasheet_id})")

            pdf_bytes = download_pdf(bucket, s3_key)
            result = process_pdf(pdf_bytes, record, validated_key)

            if result in ("success", "skipped"):
                update_datasheet_status(datasheet_id, "processed")
                move_to_done(bucket, s3_key)
            else:
                update_datasheet_status(datasheet_id, "failed")
                log.error(f"Failed to process {datasheet_id}")

        if once:
            return
