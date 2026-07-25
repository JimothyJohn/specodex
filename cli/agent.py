#!/usr/bin/env python3
"""Agent-facing CLI for end-to-end datasheet-to-database conversion.

Designed for programmatic use by AI agents. All structured output goes to
stdout as JSON. Logs and progress go to stderr. Exit codes signal outcome.

Exit codes:
    0 — success
    1 — runtime error (bad input, extraction failure, etc.)
    2 — no work to do (empty queue, already processed, etc.)

Usage:
    uv run dsm-agent list                          # list queued PDFs in S3
    uv run dsm-agent schemas                       # list available product types
    uv run dsm-agent process <s3_key> --type motor  # process one PDF → DB
    uv run dsm-agent process-all                   # drain the entire queue
    uv run dsm-agent extract <s3_key> --type motor  # extract to JSON only
    uv run dsm-agent status <datasheet_id>         # check datasheet status
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from specodex.db.lookups import scan_first_match

# ---------------------------------------------------------------------------
# Logging — all on stderr so stdout stays clean JSON
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).resolve().parent.parent / ".logs"
LOG_DIR.mkdir(exist_ok=True)

_log_formatter = logging.Formatter(
    "%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(_log_formatter)

_file_handler = logging.FileHandler(LOG_DIR / "agent_cli.log")
_file_handler.setFormatter(_log_formatter)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    handlers=[_stderr_handler, _file_handler],
)
log = logging.getLogger("dsm-agent")

# Quiet noisy third-party loggers — TLS handshake debug from httpx/httpcore
# and AFC retry chatter from google_genai used to swamp every Gemini call
# with ~14 lines of unread chatter. Override with LOG_LEVEL=DEBUG if needed.
for _noisy in ("httpcore", "httpx", "google_genai.models.AFC", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PRODUCT_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _json_out(data: Any, *, exit_code: int = 0) -> None:
    """Write JSON to stdout and exit."""
    json.dump(data, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    sys.exit(exit_code)


def _resolve_bucket(args: argparse.Namespace) -> str:
    bucket = getattr(args, "bucket", None) or os.environ.get("UPLOAD_BUCKET")
    if bucket:
        return bucket
    stage = getattr(args, "stage", None) or os.environ.get("STAGE", "dev")
    account_id = os.environ.get("AWS_ACCOUNT_ID", "")
    if account_id:
        return f"datasheetminer-uploads-{stage}-{account_id}"
    return f"datasheetminer-uploads-{stage}"


def _get_s3():
    import boto3

    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _get_dynamo():
    from specodex.db.dynamo import DynamoDBClient

    table = os.environ.get("DYNAMODB_TABLE_NAME", "products")
    return DynamoDBClient(table_name=table)


def _validate_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or len(key) < 10:
        log.error("GEMINI_API_KEY env var missing or too short")
        _json_out({"error": "GEMINI_API_KEY not set"}, exit_code=1)
    return key


def _normalize(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", s.lower().strip())


def _download_s3_pdf(bucket: str, key: str) -> bytes:
    s3 = _get_s3()
    resp = s3.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


def _extract_products(
    pdf_bytes: bytes,
    product_type: str,
    api_key: str,
    metadata: dict[str, Any],
) -> list[Any]:
    """Run Gemini extraction + validation + quality filtering. Returns models."""
    from specodex.config import SCHEMA_CHOICES
    from specodex.llm import generate_content
    from specodex.utils import parse_gemini_response
    from specodex.quality import filter_products

    if product_type not in SCHEMA_CHOICES:
        raise ValueError(
            f"Unknown product type: {product_type}. "
            f"Valid: {list(SCHEMA_CHOICES.keys())}"
        )

    context = {
        "product_name": metadata.get("product_name", ""),
        "manufacturer": metadata.get("manufacturer", ""),
        "product_family": metadata.get("product_family", ""),
        "datasheet_url": metadata.get("datasheet_url", ""),
        "pages": metadata.get("pages"),
    }

    log.info(
        "Extracting %s from %d bytes — %s by %s",
        product_type,
        len(pdf_bytes),
        context["product_name"] or "(unknown)",
        context["manufacturer"] or "(unknown)",
    )

    response = generate_content(pdf_bytes, api_key, product_type, context, "pdf")

    schema_cls = SCHEMA_CHOICES[product_type]
    parsed = parse_gemini_response(response, schema_cls, product_type, context)

    if not parsed:
        raise ValueError("Gemini returned no valid products")

    # Build (model, base_id_str) pairs
    id_pairs: list[tuple[Any, str]] = []
    for model in parsed:
        model.datasheet_url = context.get("datasheet_url")
        model.pages = context.get("pages")

        norm_mfg = _normalize(model.manufacturer) or _normalize(
            metadata.get("manufacturer")
        )
        norm_pn = _normalize(model.part_number)
        norm_name = _normalize(model.product_name)

        if norm_mfg and norm_pn:
            id_str = f"{norm_mfg}:{norm_pn}"
        elif norm_mfg and norm_name:
            id_str = f"{norm_mfg}:{norm_name}"
        else:
            log.warning(
                "Cannot generate ID — skipping. product_name=%r manufacturer=%r "
                "part_number=%r datasheet_url=%s",
                model.product_name,
                model.manufacturer,
                getattr(model, "part_number", None),
                context.get("datasheet_url"),
            )
            continue

        id_pairs.append((model, id_str))

    identified = _assign_unique_ids(id_pairs)

    passed, rejected = filter_products(identified)
    if rejected:
        log.warning("Dropped %d low-quality products", len(rejected))

    return passed


def _spec_suffix(model: Any) -> str:
    """Build a distinguishing suffix from a product's specs for ID dedup."""
    parts: list[str] = []
    for attr in (
        "rated_speed",
        "rated_voltage",
        "rated_torque",
        "rated_power",
        "rated_current",
    ):
        val = getattr(model, attr, None)
        if val:
            parts.append(_normalize(str(val)))
    dims = getattr(model, "dimensions", None)
    if dims and getattr(dims, "length", None) is not None:
        parts.append(f"l{dims.length}")
    return "_".join(parts)


def _assign_unique_ids(id_pairs: list[tuple[Any, str]]) -> list[Any]:
    """Assign deterministic product IDs, differentiating same-name products by specs."""
    from collections import Counter

    base_counts = Counter(id_str for _, id_str in id_pairs)

    identified: list[Any] = []
    seen_keys: set[str] = set()

    for model, id_str in id_pairs:
        full_key = id_str

        if base_counts[id_str] > 1:
            suffix = _spec_suffix(model)
            if suffix:
                full_key = f"{id_str}:{suffix}"

        if full_key in seen_keys:
            log.warning(
                "Duplicate product '%s' — no distinguishing specs, skipping",
                model.product_name,
            )
            continue

        seen_keys.add(full_key)
        model.product_id = uuid.uuid5(PRODUCT_NAMESPACE, full_key)
        log.info("ID %s from key '%s'", model.product_id, full_key)
        identified.append(model)

    return identified


def _models_to_dicts(models: list[Any]) -> list[dict]:
    return [m.model_dump(mode="json") for m in models]


def _move_to_done(bucket: str, key: str) -> None:
    s3 = _get_s3()
    # Support both legacy queue/ and new good_examples/ prefixes
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
    log.info("Moved %s -> %s", key, done_key)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_schemas(_args: argparse.Namespace) -> None:
    """List available product schemas."""
    from specodex.config import SCHEMA_CHOICES
    from specodex.quality import spec_fields_for_model

    schemas = []
    for name, cls in sorted(SCHEMA_CHOICES.items()):
        fields = spec_fields_for_model(cls)
        schemas.append(
            {
                "type": name,
                "class": cls.__name__,
                "spec_fields": len(fields),
                "fields": fields,
            }
        )
    _json_out(schemas)


def cmd_list(args: argparse.Namespace) -> None:
    """List PDFs ready for processing in good_examples/ (and legacy queue/)."""
    bucket = _resolve_bucket(args)
    s3 = _get_s3()
    items: list[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    for prefix in ("good_examples/", "queue/"):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.lower().endswith(".pdf"):
                    parts = key.split("/")
                    ds_id = parts[1] if len(parts) >= 3 else None
                    items.append(
                        {
                            "s3_key": key,
                            "datasheet_id": ds_id,
                            "prefix": prefix.rstrip("/"),
                            "size_bytes": obj["Size"],
                            "last_modified": obj["LastModified"].isoformat(),
                        }
                    )

    log.info("Found %d PDFs in s3://%s/{good_examples,queue}/", len(items), bucket)
    _json_out(
        {"bucket": bucket, "count": len(items), "items": items},
        exit_code=0 if items else 2,
    )


def cmd_status(args: argparse.Namespace) -> None:
    """Check the status of a datasheet by ID."""
    import boto3
    from boto3.dynamodb.conditions import Attr

    datasheet_id = args.datasheet_id
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "products")
    region = os.environ.get("AWS_REGION", "us-east-1")

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    resp = table.scan(
        FilterExpression=Attr("datasheet_id").eq(datasheet_id),
        Limit=10,
    )
    items = resp.get("Items", [])

    if not items:
        _json_out({"datasheet_id": datasheet_id, "found": False}, exit_code=2)

    # Convert Decimal to float for JSON
    import decimal

    def _clean(obj: Any) -> Any:
        if isinstance(obj, decimal.Decimal):
            return float(obj) if obj % 1 else int(obj)
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(i) for i in obj]
        return obj

    _json_out(
        {
            "datasheet_id": datasheet_id,
            "found": True,
            "records": [_clean(i) for i in items],
        }
    )


def cmd_extract(args: argparse.Namespace) -> None:
    """Extract product data from an S3 PDF — output JSON only, no DB write."""
    bucket = _resolve_bucket(args)
    api_key = _validate_api_key()

    log.info("Downloading s3://%s/%s", bucket, args.s3_key)
    pdf_bytes = _download_s3_pdf(bucket, args.s3_key)

    metadata = _build_metadata(args)

    try:
        models = _extract_products(pdf_bytes, args.type, api_key, metadata)
    except ValueError as e:
        _json_out({"error": str(e), "s3_key": args.s3_key}, exit_code=1)

    if not models:
        _json_out({"products": [], "s3_key": args.s3_key, "count": 0}, exit_code=2)

    products = _models_to_dicts(models)

    if args.output:
        Path(args.output).write_text(
            json.dumps(products, indent=2, default=str), encoding="utf-8"
        )
        log.info("Wrote %d products to %s", len(products), args.output)

    _json_out({"s3_key": args.s3_key, "count": len(products), "products": products})


def cmd_process(args: argparse.Namespace) -> None:
    """Process a single S3 PDF: extract → deduplicate → write to DynamoDB."""
    bucket = _resolve_bucket(args)
    api_key = _validate_api_key()
    dynamo = _get_dynamo()

    # Blacklist gate — refuse to process blacklisted datasheets
    ds_record = _lookup_datasheet_by_s3_key(args.s3_key)
    if _is_blacklisted(ds_record):
        log.warning("Skipping blacklisted PDF: %s", args.s3_key)
        _json_out(
            {
                "s3_key": args.s3_key,
                "status": "skipped",
                "reason": "blacklisted",
            },
            exit_code=2,
        )

    log.info("Downloading s3://%s/%s", bucket, args.s3_key)
    pdf_bytes = _download_s3_pdf(bucket, args.s3_key)

    metadata = _build_metadata(args)

    try:
        models = _extract_products(pdf_bytes, args.type, api_key, metadata)
    except ValueError as e:
        _record_failure(args.s3_key)
        _json_out(
            {"error": str(e), "s3_key": args.s3_key, "status": "failed"}, exit_code=1
        )

    if not models:
        _record_failure(args.s3_key)
        _json_out(
            {
                "s3_key": args.s3_key,
                "status": "skipped",
                "reason": "no products extracted after filtering",
            },
            exit_code=2,
        )

    # Deduplicate against DB — use type(m) so read() gets the concrete
    # subclass (Motor, Drive, …) whose product_type default is defined.
    new_models = []
    for m in models:
        if dynamo.read(m.product_id, type(m)):
            log.info("Product %s already exists — skipping", m.product_id)
        else:
            new_models.append(m)

    if not new_models:
        _json_out(
            {
                "s3_key": args.s3_key,
                "status": "skipped",
                "reason": "all products already in database",
                "count": 0,
            },
            exit_code=2,
        )

    written = dynamo.batch_create(new_models)
    log.info("Wrote %d/%d products to DynamoDB", written, len(new_models))

    # Move to done/ if requested
    if not args.keep:
        _move_to_done(bucket, args.s3_key)

    _json_out(
        {
            "s3_key": args.s3_key,
            "status": "success",
            "written": written,
            "total_extracted": len(models),
            "skipped_duplicates": len(models) - len(new_models),
            "products": _models_to_dicts(new_models),
        }
    )


def cmd_process_all(args: argparse.Namespace) -> None:
    """Drain the S3 queue: process every queued PDF end-to-end."""
    bucket = _resolve_bucket(args)
    api_key = _validate_api_key()
    dynamo = _get_dynamo()

    # Discover PDFs in good_examples/ (primary) and queue/ (legacy)
    s3 = _get_s3()
    queue_items: list[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    for prefix in ("good_examples/", "queue/"):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.lower().endswith(".pdf"):
                    parts = key.split("/")
                    ds_id = parts[1] if len(parts) >= 3 else None
                    queue_items.append({"s3_key": key, "datasheet_id": ds_id})

    if not queue_items:
        log.info("Queue empty")
        _json_out({"status": "empty", "processed": 0, "results": []}, exit_code=2)

    log.info("Processing %d queued PDFs", len(queue_items))

    # Look up metadata for each from DynamoDB
    import boto3
    from boto3.dynamodb.conditions import Attr

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "products")
    region = os.environ.get("AWS_REGION", "us-east-1")
    ddb_resource = boto3.resource("dynamodb", region_name=region)
    table = ddb_resource.Table(table_name)

    results: list[dict] = []
    success_count = 0
    fail_count = 0
    skip_count = 0

    for item in queue_items:
        s3_key = item["s3_key"]
        ds_id = item["datasheet_id"]
        result_entry: dict[str, Any] = {"s3_key": s3_key, "datasheet_id": ds_id}

        # Fetch metadata from DB — try datasheet_id first, fall back to s3_key
        record: dict[str, Any] | None = None
        if ds_id:
            record = scan_first_match(
                table,
                FilterExpression=Attr("datasheet_id").eq(ds_id),
            )

        if not record:
            record = _lookup_datasheet_by_s3_key(s3_key)

        if not record:
            log.warning("No metadata for %s — skipping", s3_key)
            result_entry["status"] = "skipped"
            result_entry["reason"] = "no metadata in database"
            skip_count += 1
            results.append(result_entry)
            continue

        # Blacklist gate
        if _is_blacklisted(record):
            log.info("Skipping blacklisted PDF: %s", s3_key)
            result_entry["status"] = "skipped"
            result_entry["reason"] = "blacklisted"
            skip_count += 1
            results.append(result_entry)
            continue

        product_type = record.get("product_type", "")
        if not product_type:
            log.warning("No product_type for %s — skipping", ds_id)
            result_entry["status"] = "skipped"
            result_entry["reason"] = "missing product_type"
            skip_count += 1
            results.append(result_entry)
            continue

        metadata = {
            "product_name": record.get("product_name", ""),
            "manufacturer": record.get("manufacturer", ""),
            "product_family": record.get("product_family", ""),
            "pages": record.get("pages"),
        }

        try:
            pdf_bytes = _download_s3_pdf(bucket, s3_key)
            models = _extract_products(pdf_bytes, product_type, api_key, metadata)

            if not models:
                _record_failure(s3_key)
                result_entry["status"] = "skipped"
                result_entry["reason"] = "no products after filtering"
                skip_count += 1
                results.append(result_entry)
                continue

            # Deduplicate — use type(m) for the concrete subclass
            new_models = [m for m in models if not dynamo.read(m.product_id, type(m))]

            if not new_models:
                result_entry["status"] = "skipped"
                result_entry["reason"] = "all duplicates"
                skip_count += 1
            else:
                written = dynamo.batch_create(new_models)
                result_entry["status"] = "success"
                result_entry["written"] = written

                success_count += 1

            # Move processed PDF regardless of dupe status
            if not args.keep:
                _move_to_done(bucket, s3_key)

            # Update datasheet status
            _update_datasheet_status(table, ds_id, "processed")

        except Exception as e:
            log.error("Failed %s: %s", s3_key, e)
            result_entry["status"] = "failed"
            result_entry["error"] = str(e)
            fail_count += 1
            _record_failure(s3_key)

        results.append(result_entry)

    _json_out(
        {
            "status": "complete",
            "total": len(queue_items),
            "success": success_count,
            "failed": fail_count,
            "skipped": skip_count,
            "results": results,
        }
    )


def cmd_intake_list(args: argparse.Namespace) -> None:
    """List PDFs in the S3 triage/ prefix awaiting intake scan."""
    from cli.intake import list_triage

    bucket = _resolve_bucket(args)
    items = list_triage(bucket)
    log.info("Found %d PDFs in s3://%s/triage/", len(items), bucket)
    _json_out(
        {"bucket": bucket, "count": len(items), "items": items},
        exit_code=0 if items else 2,
    )


def cmd_intake(args: argparse.Namespace) -> None:
    """Scan a single PDF in triage/ for TOC and specs, promote if valid."""
    from cli.intake import intake_single

    bucket = _resolve_bucket(args)
    api_key = _validate_api_key()

    result = intake_single(bucket, args.s3_key, api_key)
    exit_code = 0 if result.get("status") == "approved" else 2
    _json_out(result, exit_code=exit_code)


def cmd_intake_all(args: argparse.Namespace) -> None:
    """Scan all PDFs in triage/ and promote valid datasheets."""
    from cli.intake import intake_single, list_triage

    bucket = _resolve_bucket(args)
    api_key = _validate_api_key()

    items = list_triage(bucket)
    if not items:
        log.info("No PDFs in triage/")
        _json_out({"status": "empty", "processed": 0, "results": []}, exit_code=2)

    log.info("Scanning %d PDFs in triage/", len(items))

    results: list[dict] = []
    approved = 0
    rejected = 0

    for item in items:
        s3_key = item["s3_key"]
        try:
            result = intake_single(bucket, s3_key, api_key)
            if result.get("status") == "approved":
                approved += 1
            else:
                rejected += 1
            results.append(result)
        except Exception as e:
            log.error("Failed intake for %s: %s", s3_key, e)
            results.append({"s3_key": s3_key, "status": "failed", "error": str(e)})
            rejected += 1

    _json_out(
        {
            "status": "complete",
            "total": len(items),
            "approved": approved,
            "rejected": rejected,
            "results": results,
        }
    )


def _update_datasheet_status(table: Any, datasheet_id: str, status: str) -> None:
    """Best-effort status update on the datasheet record."""
    from boto3.dynamodb.conditions import Attr
    import datetime

    try:
        item = scan_first_match(
            table,
            FilterExpression=Attr("datasheet_id").eq(datasheet_id),
        )
        if item is None:
            return
        table.update_item(
            Key={"PK": item["PK"], "SK": item["SK"]},
            UpdateExpression="SET #s = :s, processed_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": status,
                ":t": datetime.datetime.now().isoformat(),
            },
        )
    except Exception as e:
        log.warning("Could not update status for %s: %s", datasheet_id, e)


def _lookup_datasheet_by_s3_key(s3_key: str) -> dict[str, Any] | None:
    """Find a Datasheet record in DynamoDB by its s3_key."""
    import boto3
    from boto3.dynamodb.conditions import Attr

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "products")
    region = os.environ.get("AWS_REGION", "us-east-1")
    ddb = boto3.resource("dynamodb", region_name=region)
    table = ddb.Table(table_name)

    # No Limit — scan evaluates items before filtering, so Limit=1 can miss matches
    return scan_first_match(
        table,
        FilterExpression=Attr("s3_key").eq(s3_key)
        & Attr("PK").begins_with("DATASHEET#"),
    )


# Maximum extraction failures before auto-blacklisting
_MAX_FAILURES = 2


def _is_blacklisted(ds_record: dict[str, Any] | None) -> bool:
    """Check if a Datasheet record is blacklisted."""
    if not ds_record:
        return False
    return ds_record.get("status") == "blacklisted"


def _record_failure(s3_key: str) -> None:
    """Increment failure_count on a Datasheet record; auto-blacklist at threshold."""
    import boto3
    from boto3.dynamodb.conditions import Attr
    import datetime

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "products")
    region = os.environ.get("AWS_REGION", "us-east-1")
    ddb = boto3.resource("dynamodb", region_name=region)
    table = ddb.Table(table_name)

    # Find by s3_key
    resp = table.scan(
        FilterExpression=Attr("s3_key").eq(s3_key)
        & Attr("PK").begins_with("DATASHEET#"),
    )
    items = resp.get("Items", [])
    if not items:
        log.debug("No Datasheet record for %s — cannot record failure", s3_key)
        return

    item = items[0]
    current_count = int(item.get("failure_count", 0))
    new_count = current_count + 1
    new_status = "blacklisted" if new_count >= _MAX_FAILURES else "failed"

    table.update_item(
        Key={"PK": item["PK"], "SK": item["SK"]},
        UpdateExpression="SET failure_count = :fc, #s = :s, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":fc": new_count,
            ":s": new_status,
            ":t": datetime.datetime.now().isoformat(),
        },
    )

    if new_status == "blacklisted":
        log.warning("Auto-blacklisted %s after %d failures", s3_key, new_count)
    else:
        log.info("Recorded failure %d/%d for %s", new_count, _MAX_FAILURES, s3_key)


def _build_metadata(args: argparse.Namespace) -> dict[str, Any]:
    """Build context metadata from Datasheet record first, CLI args as fallback."""
    s3_key = getattr(args, "s3_key", "")
    ds_record = _lookup_datasheet_by_s3_key(s3_key) if s3_key else None

    if ds_record:
        log.info(
            "Using Datasheet record %s for context: %s by %s",
            ds_record.get("datasheet_id"),
            ds_record.get("product_name"),
            ds_record.get("manufacturer"),
        )

    # Datasheet record wins, CLI args are fallback
    cli_name = getattr(args, "product_name", "") or ""
    cli_mfg = getattr(args, "manufacturer", "") or ""
    cli_family = getattr(args, "product_family", "") or ""
    cli_pages = _parse_pages(getattr(args, "pages", None))

    return {
        "product_name": (ds_record or {}).get("product_name") or cli_name,
        "manufacturer": (ds_record or {}).get("manufacturer") or cli_mfg,
        "product_family": (ds_record or {}).get("product_family") or cli_family,
        "datasheet_url": (ds_record or {}).get("url")
        or f"s3://{_resolve_bucket(args)}/{s3_key}",
        "pages": (ds_record or {}).get("pages") or cli_pages,
    }


def _parse_pages(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    try:
        return [int(p.strip()) for p in raw.split(",") if p.strip()]
    except ValueError:
        log.warning("Invalid pages '%s' — ignoring", raw)
        return None


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dsm-agent",
        description="Agent CLI for datasheet → product extraction pipeline.",
    )
    parser.add_argument(
        "--stage",
        default=os.environ.get("STAGE", "dev"),
        help="Deployment stage (default: $STAGE or dev)",
    )
    parser.add_argument(
        "--bucket",
        default=None,
        help="Override S3 bucket name (default: auto from stage + account)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # schemas
    sub.add_parser("schemas", help="List available product type schemas")

    # list
    sub.add_parser("list", help="List queued PDFs in S3")

    # status
    p = sub.add_parser("status", help="Check datasheet status by ID")
    p.add_argument("datasheet_id", help="Datasheet UUID")

    # extract (JSON only, no DB)
    p = sub.add_parser("extract", help="Extract product data from S3 PDF (no DB write)")
    p.add_argument("s3_key", help="S3 object key (e.g. queue/{id}/file.pdf)")
    p.add_argument("-t", "--type", required=True, help="Product type schema")
    p.add_argument("--product-name", default="", help="Product name hint")
    p.add_argument("--manufacturer", default="", help="Manufacturer hint")
    p.add_argument("--product-family", default="", help="Product family hint")
    p.add_argument("--pages", default=None, help="Comma-separated page numbers")
    p.add_argument("-o", "--output", default=None, help="Also save JSON to this file")

    # process (single → DB)
    p = sub.add_parser("process", help="Process one S3 PDF → DynamoDB")
    p.add_argument("s3_key", help="S3 object key (e.g. queue/{id}/file.pdf)")
    p.add_argument("-t", "--type", required=True, help="Product type schema")
    p.add_argument("--product-name", default="", help="Product name hint")
    p.add_argument("--manufacturer", default="", help="Manufacturer hint")
    p.add_argument("--product-family", default="", help="Product family hint")
    p.add_argument("--pages", default=None, help="Comma-separated page numbers")
    p.add_argument(
        "--keep",
        action="store_true",
        help="Keep PDF in queue/ instead of moving to done/",
    )

    # process-all (drain queue → DB)
    p = sub.add_parser("process-all", help="Process all queued PDFs → DynamoDB")
    p.add_argument(
        "--keep",
        action="store_true",
        help="Keep PDFs in queue/ instead of moving to done/",
    )

    # intake-list (list triage/ PDFs)
    sub.add_parser("intake-list", help="List PDFs in triage/ awaiting intake scan")

    # intake (scan + promote one)
    p = sub.add_parser(
        "intake", help="Scan a triage/ PDF for TOC/specs, promote if valid"
    )
    p.add_argument("s3_key", help="S3 object key (e.g. triage/file.pdf)")

    # intake-all (scan all triage/)
    sub.add_parser("intake-all", help="Scan all triage/ PDFs and promote valid ones")

    return parser


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "schemas": cmd_schemas,
        "list": cmd_list,
        "status": cmd_status,
        "extract": cmd_extract,
        "process": cmd_process,
        "process-all": cmd_process_all,
        "intake-list": cmd_intake_list,
        "intake": cmd_intake,
        "intake-all": cmd_intake_all,
    }

    try:
        commands[args.command](args)
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(130)
    except Exception as e:
        log.error("Fatal: %s", e, exc_info=True)
        _json_out({"error": str(e)}, exit_code=1)


if __name__ == "__main__":
    main()
