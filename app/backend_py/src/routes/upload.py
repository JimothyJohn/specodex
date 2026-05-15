"""Upload route — handles PDF submission for the extraction queue.

Port of ``app/backend/src/routes/upload.ts``. Available in both
public and admin mode (the upload route is in the readonly guard's
allow-list — uploading only queues work).

Flow:
1. Client POST /api/upload with metadata.
2. Backend creates a Datasheet record (status=queued) + returns a
   presigned S3 PUT URL.
3. Client PUTs the PDF directly to S3 using the presigned URL.
4. Local processor (``./Quickstart process``) picks up queued items
   and runs extraction.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import boto3
from fastapi import APIRouter, Body, HTTPException, status

from app.backend_py.src.db.dynamodb import BackendDB
from specodex.models.datasheet import Datasheet


router = APIRouter(prefix="/api/upload")
logger = logging.getLogger(__name__)


def _bucket_name() -> str:
    """Look up the upload bucket. Falls back to the same convention
    the Express stack uses, so both backends can land payloads in the
    same place during the parallel-deploy window.
    """

    explicit = os.environ.get("UPLOAD_BUCKET")
    if explicit:
        return explicit
    stage = os.environ.get("STAGE", "dev")
    account = os.environ.get("AWS_ACCOUNT_ID", "")
    return f"datasheetminer-uploads-{stage}-{account}".rstrip("-")


def _s3_client():
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))


@router.post("", status_code=status.HTTP_201_CREATED)
def create_upload(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    required = ("product_name", "manufacturer", "product_type", "filename")
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Missing required fields: product_name, manufacturer, "
                "product_type, filename"
            ),
        )

    filename = str(payload["filename"])
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted",
        )

    datasheet_id = str(uuid4())
    s3_key = f"queue/{datasheet_id}/{filename}"
    bucket = _bucket_name()

    datasheet = Datasheet(
        datasheet_id=datasheet_id,  # type: ignore[arg-type] — pydantic coerces from str
        product_type=payload["product_type"],
        product_name=payload["product_name"],
        manufacturer=payload["manufacturer"],
        pages=payload.get("pages"),
        url=f"s3://{bucket}/{s3_key}",
        status="queued",
        s3_key=s3_key,
    )

    db = BackendDB()
    if not db.create_datasheet(datasheet):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create datasheet record",
        )

    # Presigned PUT URL, 15 min expiry — matches the Express setting.
    try:
        upload_url = _s3_client().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": s3_key,
                "ContentType": "application/pdf",
            },
            ExpiresIn=900,
        )
    except Exception as exc:
        logger.error("[upload] Failed to generate presigned URL: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate upload URL",
        )

    # s3_key embeds the user-supplied filename — newline-strip before
    # logging (CodeQL log-injection finding shape).
    safe_key = s3_key.replace("\r", "").replace("\n", "")
    logger.info("[upload] Queued datasheet %s (key=%s)", datasheet_id, safe_key)

    # uploaded_at is metadata the Express response carried in the
    # datasheet's tracking fields but not in the URL envelope.
    _ = datetime.now(timezone.utc).isoformat()

    return {
        "success": True,
        "data": {
            "datasheet_id": datasheet_id,
            "s3_key": s3_key,
            "upload_url": upload_url,
        },
    }
