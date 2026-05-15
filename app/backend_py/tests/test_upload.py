"""Upload route tests.

Uses moto's S3 mock to back the presigned URL generator and the
moto DynamoDB fixture (already in conftest.py) to back the
Datasheet record.
"""

from __future__ import annotations

import importlib
from typing import Iterator

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws


@pytest.fixture
def upload_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """Stand up both moto S3 + DynamoDB in one mock_aws context so the
    upload endpoint can write the Datasheet record AND generate a
    presigned URL in the same test."""

    monkeypatch.setenv("APP_MODE", "public")
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    with mock_aws():
        # DynamoDB
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="products",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        # S3 bucket
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-uploads")

        import app.backend_py.src.main as main_mod

        importlib.reload(main_mod)
        yield TestClient(main_mod.app)


class TestUpload:
    def test_missing_fields_returns_400(self, upload_client: TestClient) -> None:
        resp = upload_client.post(
            "/api/upload",
            json={"product_name": "x"},  # missing manufacturer, type, filename
        )
        assert resp.status_code == 400

    def test_non_pdf_filename_returns_400(self, upload_client: TestClient) -> None:
        resp = upload_client.post(
            "/api/upload",
            json={
                "product_name": "x",
                "manufacturer": "y",
                "product_type": "motor",
                "filename": "image.png",
            },
        )
        assert resp.status_code == 400

    def test_happy_path_returns_presigned_url(self, upload_client: TestClient) -> None:
        resp = upload_client.post(
            "/api/upload",
            json={
                "product_name": "Test Motor",
                "manufacturer": "MfgX",
                "product_type": "motor",
                "filename": "datasheet.pdf",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert "datasheet_id" in data
        assert data["s3_key"].endswith("/datasheet.pdf")
        assert data["s3_key"].startswith(f"queue/{data['datasheet_id']}/")
        # The presigned URL should point at our bucket + the same key.
        assert "test-uploads" in data["upload_url"]
        assert data["s3_key"] in data["upload_url"]

    def test_uppercase_pdf_filename_is_accepted(
        self, upload_client: TestClient
    ) -> None:
        resp = upload_client.post(
            "/api/upload",
            json={
                "product_name": "Test Motor",
                "manufacturer": "MfgX",
                "product_type": "motor",
                "filename": "DATASHEET.PDF",
            },
        )
        assert resp.status_code == 201
