"""Shared fixtures for app/backend_py tests.

We stand up a moto-mocked DynamoDB table here (rather than reusing
``tests/conftest.py`` from the parent project) so this package
remains independently testable when the FastAPI deps land in their
own venv per the plan in ``todo/PYTHON_BACKEND.md`` §1.1.
"""

from __future__ import annotations

import os
from typing import Iterator

import boto3
import pytest
from moto import mock_aws

# Force AWS region + dummy creds before any boto3 client is imported.
# moto needs these at import time, not just at call time, on some
# platforms.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("DYNAMODB_TABLE_NAME", "products")


@pytest.fixture
def dynamodb_table() -> Iterator[object]:
    """Moto-mocked single-table DynamoDB matching the production schema."""

    with mock_aws():
        client = boto3.resource("dynamodb", region_name="us-east-1")
        table = client.create_table(
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
        yield table
