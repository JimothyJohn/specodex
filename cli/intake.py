"""
Intake pipeline — scans incoming PDFs in S3 triage/ for table of contents
and specification data, promotes valid datasheets to good_examples/,
and creates Datasheet records in DynamoDB.

Flow:
    upload → triage/ → scan for TOC/specs → good_examples/ + Datasheet → extract

Usage (via dsm-agent):
    dsm-agent intake-list                    # list PDFs in triage/
    dsm-agent intake <s3_key> --type motor   # scan + promote one PDF
    dsm-agent intake-all                     # scan all PDFs in triage/
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger("dsm-agent.intake")

# Gemini model for lightweight triage scanning
_TRIAGE_MODEL = "gemini-2.5-flash"

_TRIAGE_PROMPT = """Analyze this PDF document and determine if it is a valid industrial product datasheet.

Check for:
1. A table of contents that references specification sections or data tables
2. Technical specification tables with numeric values and units
3. Product identification information (manufacturer, model numbers)

If EITHER a table of contents referencing specs OR specification data tables are present,
this is a valid datasheet.

Extract the following metadata from the document:
- product_type: one of "motor", "drive", "gearhead", "robot_arm", "factory" (pick the best match)
- manufacturer: the company that makes the product
- product_name: the product name or model series
- product_family: the product family or sub-series (if identifiable)
- category: a brief category description (e.g., "brushless dc motor", "servo drive")
- spec_pages: list of page numbers that contain specification tables (1-indexed)
- spec_density: a float from 0.0 to 1.0 estimating what fraction of the product's
  technical specifications this document covers. Consider the typical fields for the
  detected product_type:
    motor: rated_voltage, rated_speed, max_speed, rated_torque, peak_torque, rated_power,
           rated_current, peak_current, voltage_constant, torque_constant, resistance,
           inductance, poles, rotor_inertia, ip_rating, dimensions, weight
    drive: input_voltage, rated_current, peak_current, rated_power, switching_frequency,
           fieldbus, encoder_feedback_support, digital_inputs, digital_outputs, ip_rating,
           dimensions, weight
    gearhead: gear_ratio, gear_type, stages, max_continuous_torque, max_peak_torque,
              backlash, efficiency, input_shaft_diameter, output_shaft_diameter,
              ip_rating, dimensions, weight
    robot_arm: payload, reach, degrees_of_freedom, pose_repeatability, max_tcp_speed,
               ip_rating, joints, dimensions, weight
  Score 0.0 if the document contains none of these fields.
  Score 0.3 if only a handful of fields are present (e.g. just voltage and dimensions).
  Score 0.6 if roughly half the fields can be extracted.
  Score 0.9-1.0 if most or all fields have explicit numeric values with units.

- distinct_product_count: how many distinct product models/variants are documented (integer).
  A single-product datasheet = 1. A catalog with 50 motor variants = 50.
- is_multi_category: true if the document covers multiple product types (e.g. motors AND
  gearheads AND drives in one catalog), false if it covers only one type or variants within
  one type. A catalog with 50 motor variants is NOT multi-category.

Important distinctions:
- The manufacturer field should be the MANUFACTURER who makes the product, not a
  distributor or reseller. If the document is from a distributor (e.g. "Multidimensions"
  reselling Portescap motors), use the original manufacturer name (e.g. "Portescap").
- A distributor brochure covering motors, gearheads, and encoders from different
  manufacturers IS multi-category.

Be conservative: only mark is_valid_datasheet=false if the document clearly has NO
technical specifications or product data whatsoever (e.g., marketing brochures with
no specs, instruction manuals, safety notices).
"""

# Minimum spec density to promote a PDF from triage
MIN_SPEC_DENSITY = 0.2


def _get_datasheet_table():
    """Return the DynamoDB Table resource for datasheets."""
    import boto3

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "products")
    region = os.environ.get("AWS_REGION", "us-east-1")
    ddb = boto3.resource("dynamodb", region_name=region)
    return ddb.Table(table_name)


def _find_by_content_hash(
    dynamo_client: Any, content_hash: str
) -> dict[str, Any] | None:
    """Check if a Datasheet with this content hash already exists."""
    from boto3.dynamodb.conditions import Attr

    table = _get_datasheet_table()
    resp = table.scan(
        FilterExpression=Attr("content_hash").eq(content_hash)
        & Attr("PK").begins_with("DATASHEET#"),
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def _find_by_url(url: str) -> dict[str, Any] | None:
    """Check if a Datasheet with this URL already exists.

    Catches re-submissions of the same external URL even when the content
    hash hasn't been computed yet (legacy datasheets, URL-based entries).
    """
    from boto3.dynamodb.conditions import Attr

    table = _get_datasheet_table()
    resp = table.scan(
        FilterExpression=Attr("url").eq(url) & Attr("PK").begins_with("DATASHEET#"),
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def _is_url_blacklisted(url: str) -> bool:
    """Check if any Datasheet with this URL has been blacklisted."""
    existing = _find_by_url(url)
    if existing and existing.get("status") == "blacklisted":
        return True
    return False


def _delete_from_triage(bucket: str, triage_key: str, s3_client: Any) -> None:
    """Remove a rejected PDF from triage/ so it doesn't get re-scanned."""
    try:
        s3_client.delete_object(Bucket=bucket, Key=triage_key)
        log.info("Deleted rejected PDF from triage: %s", triage_key)
    except Exception as e:
        log.warning("Could not delete %s from triage: %s", triage_key, e)


class IntakeScanResult(BaseModel):
    """Lightweight scan result from Gemini triage."""

    is_valid_datasheet: bool = Field(
        ..., description="Whether this PDF contains specification data"
    )
    has_table_of_contents: bool = Field(
        ..., description="Whether a TOC referencing specs was found"
    )
    has_specification_tables: bool = Field(
        ..., description="Whether data tables with specs were found"
    )
    product_type: str | None = Field(None, description="Detected product type")
    manufacturer: str | None = Field(None, description="Detected manufacturer name")
    product_name: str | None = Field(None, description="Detected product name or model")
    product_family: str | None = Field(None, description="Detected product family")
    category: str | None = Field(None, description="Brief category description")
    spec_pages: list[int] | None = Field(
        None, description="Page numbers containing specification tables"
    )
    spec_density: float | None = Field(
        None,
        description="Estimated spec field coverage 0.0-1.0 (fraction of schema fields present)",
    )
    rejection_reason: str | None = Field(
        None, description="Why the PDF was rejected (if not valid)"
    )
    distinct_product_count: int | None = Field(
        None, description="Number of distinct products described in the document"
    )
    is_multi_category: bool = Field(
        False,
        description="Whether document spans multiple product types (e.g. motors AND gearheads)",
    )


def scan_pdf(pdf_bytes: bytes, api_key: str) -> IntakeScanResult:
    """Run a lightweight Gemini scan to check for TOC and spec tables.

    Sends the PDF with a triage-specific prompt and returns structured
    metadata about whether the document is a valid datasheet.
    """
    from google import genai

    client = genai.Client(api_key=api_key)

    contents = [
        genai.types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        _TRIAGE_PROMPT,
    ]

    log.info("Scanning PDF (%d bytes) for TOC and spec tables", len(pdf_bytes))

    response = client.models.generate_content(
        model=_TRIAGE_MODEL,
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "response_schema": IntakeScanResult,
        },
    )

    import json

    raw = json.loads(response.text)
    result = IntakeScanResult.model_validate(raw)

    log.info(
        "Scan result: valid=%s toc=%s tables=%s density=%.2f type=%s mfg=%s name=%s",
        result.is_valid_datasheet,
        result.has_table_of_contents,
        result.has_specification_tables,
        result.spec_density or 0.0,
        result.product_type,
        result.manufacturer,
        result.product_name,
    )
    return result


def promote_pdf(
    bucket: str,
    triage_key: str,
    scan: IntakeScanResult,
    *,
    content_hash: str | None = None,
    s3_client: Any = None,
    dynamo_client: Any = None,
) -> dict[str, Any]:
    """Move a validated PDF from triage/ to good_examples/ and create a Datasheet record.

    Returns a summary dict with the new S3 key and datasheet_id.
    """
    from specodex.models.datasheet import Datasheet

    if s3_client is None:
        import boto3

        s3_client = boto3.client(
            "s3", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

    if dynamo_client is None:
        from specodex.db.dynamo import DynamoDBClient

        table = os.environ.get("DYNAMODB_TABLE_NAME", "products")
        dynamo_client = DynamoDBClient(table_name=table)

    # Build a flat, human-readable key in good_examples/
    datasheet_id = uuid.uuid4()
    short_id = str(datasheet_id)[:8]
    mfg_slug = re.sub(
        r"[^a-z0-9]+", "-", (scan.manufacturer or "unknown").lower()
    ).strip("-")
    name_slug = re.sub(
        r"[^a-z0-9]+", "-", (scan.product_name or "unknown").lower()
    ).strip("-")
    good_key = f"good_examples/{mfg_slug}_{name_slug}_{short_id}.pdf"

    # Move PDF: copy then delete
    s3_client.copy_object(
        Bucket=bucket,
        CopySource={"Bucket": bucket, "Key": triage_key},
        Key=good_key,
    )
    s3_client.delete_object(Bucket=bucket, Key=triage_key)
    log.info("Moved %s -> %s", triage_key, good_key)

    # Create Datasheet record
    datasheet = Datasheet(
        datasheet_id=datasheet_id,
        url=f"s3://{bucket}/{good_key}",
        pages=scan.spec_pages,
        product_type=scan.product_type or "motor",
        product_name=scan.product_name
        or triage_key.rsplit("/", 1)[-1].replace(".pdf", ""),
        product_family=scan.product_family,
        manufacturer=scan.manufacturer or "Unknown",
        category=scan.category,
        status="approved",
        s3_key=good_key,
        content_hash=content_hash,
        spec_density=scan.spec_density,
    )

    dynamo_client.create(datasheet)
    log.info(
        "Created Datasheet %s — %s by %s (%s)",
        datasheet_id,
        datasheet.product_name,
        datasheet.manufacturer,
        datasheet.product_type,
    )

    return {
        "datasheet_id": str(datasheet_id),
        "s3_key": good_key,
        "product_type": datasheet.product_type,
        "product_name": datasheet.product_name,
        "manufacturer": datasheet.manufacturer,
        "product_family": datasheet.product_family,
        "category": datasheet.category,
        "spec_pages": scan.spec_pages,
        "status": "approved",
    }


def intake_single(
    bucket: str,
    triage_key: str,
    api_key: str,
    *,
    s3_client: Any = None,
    dynamo_client: Any = None,
) -> dict[str, Any]:
    """Full intake flow for one PDF: download → scan → promote or reject.

    Rejected PDFs are deleted from triage/ so they don't get re-scanned.
    Duplicate PDFs (by content hash or URL) are also cleaned up.
    """
    if s3_client is None:
        import boto3

        s3_client = boto3.client(
            "s3", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

    if dynamo_client is None:
        from specodex.db.dynamo import DynamoDBClient

        table = os.environ.get("DYNAMODB_TABLE_NAME", "products")
        dynamo_client = DynamoDBClient(table_name=table)

    def _reject(reason: str, **extra: Any) -> dict[str, Any]:
        """Return a rejection result and delete the PDF from triage/."""
        _delete_from_triage(bucket, triage_key, s3_client)
        return {"s3_key": triage_key, "status": "rejected", "reason": reason, **extra}

    # Download
    log.info("Downloading s3://%s/%s", bucket, triage_key)
    resp = s3_client.get_object(Bucket=bucket, Key=triage_key)
    pdf_bytes: bytes = resp["Body"].read()

    # Pre-scan file integrity check — catches HTML error pages, corrupt
    # files, and truncated downloads before spending Gemini API tokens
    from cli.intake_guards import check_file_integrity

    integrity = check_file_integrity(pdf_bytes)
    if not integrity.passed:
        log.warning("File integrity FAIL for %s: %s", triage_key, integrity.reason)
        return _reject(integrity.reason, guard=integrity.guard_name)

    # Content hash dedup — skip if this exact PDF was already ingested
    content_hash = hashlib.sha256(pdf_bytes).hexdigest()
    existing = _find_by_content_hash(dynamo_client, content_hash)
    if existing:
        log.info(
            "Duplicate PDF detected (hash=%s…) — already ingested as %s (status=%s)",
            content_hash[:12],
            existing.get("datasheet_id"),
            existing.get("status"),
        )
        _delete_from_triage(bucket, triage_key, s3_client)
        return {
            "s3_key": triage_key,
            "status": "skipped",
            "reason": "duplicate content hash",
            "content_hash": content_hash,
            "existing_datasheet_id": existing.get("datasheet_id"),
            "existing_status": existing.get("status"),
        }

    # Scan
    scan = scan_pdf(pdf_bytes, api_key)

    if not scan.is_valid_datasheet:
        log.warning(
            "Rejected %s: %s",
            triage_key,
            scan.rejection_reason or "not a valid datasheet",
        )
        return _reject(
            scan.rejection_reason or "no specification data found",
            has_toc=scan.has_table_of_contents,
            has_spec_tables=scan.has_specification_tables,
            spec_density=scan.spec_density,
        )

    # Post-scan guards — manufacturer identity, document scope,
    # extraction feasibility, and calibrated spec density
    from cli.intake_guards import any_blocking, run_guards

    verdicts = run_guards(scan, pdf_bytes)
    blocker = any_blocking(verdicts)
    if blocker:
        log.warning(
            "Guard '%s' blocked %s: %s",
            blocker.guard_name,
            triage_key,
            blocker.reason,
        )
        return _reject(
            blocker.reason,
            guard=blocker.guard_name,
            spec_density=scan.spec_density,
            has_toc=scan.has_table_of_contents,
            has_spec_tables=scan.has_specification_tables,
        )

    # Promote
    result = promote_pdf(
        bucket,
        triage_key,
        scan,
        content_hash=content_hash,
        s3_client=s3_client,
        dynamo_client=dynamo_client,
    )
    result["status"] = "approved"
    result["content_hash"] = content_hash
    return result


def list_triage(bucket: str, *, s3_client: Any = None) -> list[dict[str, Any]]:
    """List all PDFs in the triage/ prefix."""
    if s3_client is None:
        import boto3

        s3_client = boto3.client(
            "s3", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

    items: list[dict[str, Any]] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="triage/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith(".pdf"):
                items.append(
                    {
                        "s3_key": key,
                        "size_bytes": obj["Size"],
                        "last_modified": obj["LastModified"].isoformat(),
                    }
                )
    return items
