"""Shared AWS plumbing for the CLI tools.

One home for the env-driven naming rules (region, table, upload bucket) and
the boto3 construction that every CLI module used to repeat inline. boto3 is
imported lazily so importing this module stays cheap and offline-safe, and
the environment is read at call time, not import time.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Optional

DEFAULT_REGION = "us-east-1"
DEFAULT_TABLE = "products"


def region() -> str:
    return os.environ.get("AWS_REGION", DEFAULT_REGION)


def s3_client() -> Any:
    import boto3  # type: ignore

    return boto3.client("s3", region_name=region())


def dynamo_table(default_table: str = DEFAULT_TABLE) -> Any:
    """Table resource for ``DYNAMODB_TABLE_NAME``.

    ``default_table`` is the fallback when the env var is unset. Callers
    disagree on it (the upload-queue processor falls back to
    ``products-dev``), so it is an explicit argument, not a hidden constant.
    """
    import boto3  # type: ignore

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", default_table)
    return boto3.resource("dynamodb", region_name=region()).Table(table_name)


def upload_bucket(bucket: Optional[str] = None, stage: Optional[str] = None) -> str:
    """Resolve the upload bucket: explicit > ``UPLOAD_BUCKET`` > naming rule."""
    resolved = bucket or os.environ.get("UPLOAD_BUCKET")
    if resolved:
        return resolved
    stage = stage or os.environ.get("STAGE", "dev")
    account_id = os.environ.get("AWS_ACCOUNT_ID", "")
    if account_id:
        return f"datasheetminer-uploads-{stage}-{account_id}"
    return f"datasheetminer-uploads-{stage}"


def decimal_to_native(obj: Any) -> Any:
    """Recursively convert DynamoDB ``Decimal`` → int/float so json.dumps works."""
    if isinstance(obj, Decimal):
        as_int = int(obj)
        return as_int if as_int == obj else float(obj)
    if isinstance(obj, dict):
        return {k: decimal_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decimal_to_native(v) for v in obj]
    return obj
