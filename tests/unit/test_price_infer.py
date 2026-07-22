"""Tests for ``cli.price_infer`` — the price-comps-v1 backfill CLI.

The apply path runs against a real (moto-mocked) DynamoDB table — no
mocked client methods — seeding a priced Acme ladder plus an unpriced
target and asserting the written ``price_estimate`` round-trips with a
valid shape while ``msrp`` stays untouched.
"""

from __future__ import annotations

import os
from typing import Any, Iterator, Optional
from uuid import UUID

import boto3
import moto
import pytest

from cli.price_infer import resolve_table_name, run
from specodex.db.dynamo import DynamoDBClient
from specodex.models.commercial import SourcedFigure
from specodex.models.motor import Motor
from specodex.pricing.inference import METHOD_FAMILY

TABLE = "products"
RETRIEVED_AT = "2026-07-21T00:00:00+00:00"


@pytest.fixture()
def client() -> Iterator[DynamoDBClient]:
    with moto.mock_aws():
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName=TABLE,
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
        yield DynamoDBClient(table_name=TABLE)


def motor(
    i: int, *, power: float, price: Optional[float] = None, **overrides: Any
) -> Motor:
    kwargs: dict[str, Any] = {
        "product_id": UUID(int=i),
        "product_name": f"AKM-{i:03d}",
        "manufacturer": "Acme",
        "product_type": "motor",
        "product_family": "AKM",
        "part_number": f"AKM-{i:03d}",
        "rated_power": {"value": power, "unit": "W"},
    }
    if price is not None:
        kwargs["msrp"] = {"value": price, "unit": "USD"}
    kwargs.update(overrides)
    return Motor(**kwargs)


def seed_ladder(client: DynamoDBClient) -> Motor:
    """Five priced Acme/AKM motors on a power law + one unpriced target."""
    for i, power in enumerate([100.0, 200.0, 400.0, 800.0, 1600.0]):
        assert client.create(motor(i + 1, power=power, price=10.0 * power**0.8))
    target = motor(99, power=300.0)
    assert client.create(target)
    return target


@pytest.mark.unit
class TestApplyPath:
    def test_apply_writes_valid_estimate_and_leaves_msrp_alone(
        self, client: DynamoDBClient
    ) -> None:
        target = seed_ladder(client)

        stats = run(
            product_types=["motor"],
            apply=True,
            client=client,
            retrieved_at=RETRIEVED_AT,
        )
        assert stats.estimated == 1
        assert stats.written == 1
        assert stats.failures == []

        stored = client.read(target.product_id, Motor)
        assert stored is not None
        # msrp untouched — an estimate never masquerades as a listed price.
        assert stored.msrp is None
        figure = stored.price_estimate
        assert figure is not None
        # Round-trips through DynamoDB (Decimal) back into a valid figure.
        assert SourcedFigure.model_validate(figure.model_dump())
        assert figure.value.unit == "USD"
        assert figure.value.value == pytest.approx(10.0 * 300.0**0.8, rel=0.01)
        assert figure.confidence == "inferred"
        citation = figure.sources[0]
        assert citation.kind == "db_inference"
        assert citation.method_version == METHOD_FAMILY
        assert citation.retrieved_at == RETRIEVED_AT
        assert citation.comparable_ids == sorted(str(UUID(int=i)) for i in range(1, 6))

    def test_apply_does_not_touch_priced_comparables(
        self, client: DynamoDBClient
    ) -> None:
        seed_ladder(client)
        run(
            product_types=["motor"],
            apply=True,
            client=client,
            retrieved_at=RETRIEVED_AT,
        )
        comp = client.read(UUID(int=1), Motor)
        assert comp is not None
        assert comp.msrp is not None
        assert comp.msrp.value == pytest.approx(10.0 * 100.0**0.8, rel=0.01)
        assert comp.price_estimate is None

    def test_second_run_skips_rows_with_existing_estimate(
        self, client: DynamoDBClient
    ) -> None:
        seed_ladder(client)
        first = run(
            product_types=["motor"],
            apply=True,
            client=client,
            retrieved_at=RETRIEVED_AT,
        )
        assert first.written == 1
        second = run(
            product_types=["motor"],
            apply=True,
            client=client,
            retrieved_at=RETRIEVED_AT,
        )
        assert second.estimated == 0
        assert second.written == 0
        assert second.skipped_has_estimate == 1


@pytest.mark.unit
class TestDryRun:
    def test_dry_run_writes_nothing(self, client: DynamoDBClient) -> None:
        target = seed_ladder(client)
        stats = run(
            product_types=["motor"],
            apply=False,
            client=client,
            retrieved_at=RETRIEVED_AT,
        )
        assert stats.estimated == 1
        assert stats.written == 0
        stored = client.read(target.product_id, Motor)
        assert stored is not None
        assert stored.price_estimate is None

    def test_limit_caps_targets(self, client: DynamoDBClient) -> None:
        seed_ladder(client)
        assert client.create(motor(98, power=500.0))  # second unpriced target
        stats = run(
            product_types=["motor"],
            apply=False,
            limit=1,
            client=client,
            retrieved_at=RETRIEVED_AT,
        )
        assert stats.targets == 1


@pytest.mark.unit
class TestStageResolution:
    def test_stage_convention_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)
        assert resolve_table_name("dev") == "products-dev"
        assert resolve_table_name("staging") == "products-staging"

    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", "products")
        assert resolve_table_name("dev") == "products"
