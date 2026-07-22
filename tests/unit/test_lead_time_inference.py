"""Tests for ``specodex.pricing.lead_time`` + the ``--lead-times`` CLI mode.

The ground rule under test everywhere: **no invented lead times.** A
figure exists only when a vendor's published, citation-audited
statement parses; everything else — unknown vendors, unparseable
statements, rows with a listed ``lead_time`` — yields ``None`` (or a
skip), never a guess. Citations carry through verbatim from the
registry fact; confidence is pinned at "medium" (vendor-level
statement applied to a part — never "listed").

The CLI apply path runs against a real (moto-mocked) DynamoDB table —
no mocked client methods.
"""

from __future__ import annotations

import os
from typing import Any, Iterator, List, Optional
from uuid import UUID

import boto3
import moto
import pytest

from cli.price_infer import run_lead_times
from specodex.db.dynamo import DynamoDBClient
from specodex.models.commercial import SourceCitation, SourcedFigure
from specodex.models.motor import Motor
from specodex.pricing.lead_time import (
    LEAD_STATEMENT_CONFIDENCE,
    SAME_DAY_LEAD,
    estimate_lead_time,
    parse_lead_statement,
    parse_lead_statement_range,
)
from specodex.vendors.registry import VendorFact, VendorProfile

RETRIEVED_AT = "2026-07-22T00:00:00+00:00"

# The real Oriental Motor statement from data/vendors/oriental-motor.json.
ORIENTAL_STATEMENT = (
    "Products can be delivered in as little as 3 days, from an order of 1 piece"
)


def citation(url: str = "https://example.com/delivery") -> SourceCitation:
    return SourceCitation(
        url=url,
        kind="oem",
        retrieved_at="2026-07-01T00:00:00+00:00",
        excerpt="products ship in the stated window",
    )


def profile(
    statement: str,
    *,
    slug: str = "oriental-motor",
    display_name: str = "Oriental Motor",
    sources: Optional[List[SourceCitation]] = None,
    extra_facts: Optional[List[VendorFact]] = None,
) -> VendorProfile:
    facts = list(extra_facts or [])
    facts.append(
        VendorFact(
            label="Published delivery statement",
            value=statement,
            kind="lead_time_statement",
            sources=sources or [citation()],
        )
    )
    return VendorProfile(slug=slug, display_name=display_name, facts=facts)


def motor(
    i: int = 1, *, manufacturer: str = "Oriental Motor", **overrides: Any
) -> Motor:
    kwargs: dict[str, Any] = {
        "product_id": UUID(int=i),
        "product_name": f"NX-{i:03d}",
        "manufacturer": manufacturer,
        "product_type": "motor",
        "part_number": f"NX-{i:03d}",
    }
    kwargs.update(overrides)
    return Motor(**kwargs)


@pytest.mark.unit
class TestParseLeadStatement:
    def test_business_days(self) -> None:
        assert parse_lead_statement("3 business days") == (3.0, "days")

    def test_ships_within_weeks(self) -> None:
        assert parse_lead_statement("ships within 2 weeks") == (2.0, "weeks")

    def test_working_days_qualifier(self) -> None:
        assert parse_lead_statement("dispatched in 5 working days") == (5.0, "days")

    def test_singular_unit(self) -> None:
        assert parse_lead_statement("ships in 1 week") == (1.0, "weeks")

    def test_decimal_duration(self) -> None:
        assert parse_lead_statement("about 1.5 weeks") == (1.5, "weeks")

    def test_range_uses_max(self) -> None:
        assert parse_lead_statement("5-7 days") == (7.0, "days")

    def test_range_full_shape(self) -> None:
        assert parse_lead_statement_range("5-7 days") == (5.0, 7.0, "days")

    def test_range_with_to_and_qualifier(self) -> None:
        assert parse_lead_statement_range("5 to 7 business days") == (
            5.0,
            7.0,
            "days",
        )

    def test_range_en_dash(self) -> None:
        assert parse_lead_statement_range("2–3 weeks") == (2.0, 3.0, "weeks")

    def test_same_day_pinned_convention(self) -> None:
        # Pinned: "same day" records as 1 day, never 0 (see module doc).
        assert parse_lead_statement("same day shipping") == SAME_DAY_LEAD
        assert parse_lead_statement("Same-Day dispatch") == (1.0, "days")

    def test_real_oriental_motor_statement(self) -> None:
        assert parse_lead_statement(ORIENTAL_STATEMENT) == (3.0, "days")
        # "...order of 1 piece" must not parse as a duration.
        assert parse_lead_statement_range(ORIENTAL_STATEMENT) == (3.0, 3.0, "days")

    def test_case_insensitive(self) -> None:
        assert parse_lead_statement("2 WEEKS") == (2.0, "weeks")

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "call for availability",
            "fast shipping",
            "ships in days",  # no number
            "3 months",  # unsupported unit — stays registry-only
            "0 days",  # zero duration is not a lead time
            "7-5 days",  # malformed range (min > max)
            "0-0 weeks",
            "9" * 400 + " days",  # absurd magnitude -> float inf
            "weekdays only",  # "days" inside a longer word context
        ],
    )
    def test_unparseable_returns_none(self, text: str) -> None:
        assert parse_lead_statement(text) is None
        assert parse_lead_statement_range(text) is None

    def test_non_string_input(self) -> None:
        assert parse_lead_statement(None) is None  # type: ignore[arg-type]
        assert parse_lead_statement(3) is None  # type: ignore[arg-type]


@pytest.mark.unit
class TestEstimateLeadTime:
    def test_registry_vendor_gets_figure_with_verbatim_citations(self) -> None:
        src = citation("https://www.orientalmotor.com/contact/index.html")
        profiles = {"oriental-motor": profile(ORIENTAL_STATEMENT, sources=[src])}
        figure = estimate_lead_time(motor(), profiles, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.value.value == 3.0
        assert figure.value.unit == "days"
        assert figure.confidence == LEAD_STATEMENT_CONFIDENCE == "medium"
        assert figure.observed_range is None  # single value, no range
        # Citations carried through verbatim — not restamped.
        assert len(figure.sources) == 1
        carried = figure.sources[0]
        assert carried.url == src.url
        assert carried.excerpt == src.excerpt
        assert carried.kind == src.kind
        assert carried.retrieved_at == src.retrieved_at
        assert carried.retrieved_at != RETRIEVED_AT
        # Round-trips as a valid SourcedFigure (citation invariants hold).
        assert SourcedFigure.model_validate(figure.model_dump())

    def test_range_statement_records_observed_range(self) -> None:
        profiles = {"oriental-motor": profile("ships in 5-7 business days")}
        figure = estimate_lead_time(motor(), profiles, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.value.value == 7.0
        assert figure.observed_range is not None
        assert figure.observed_range.min == 5.0
        assert figure.observed_range.max == 7.0
        assert figure.observed_range.unit == "days"

    def test_product_with_listed_lead_time_skipped(self) -> None:
        profiles = {"oriental-motor": profile(ORIENTAL_STATEMENT)}
        product = motor(lead_time={"value": 30, "unit": "days"})
        assert estimate_lead_time(product, profiles, retrieved_at=RETRIEVED_AT) is None

    def test_unknown_manufacturer(self) -> None:
        profiles = {"oriental-motor": profile(ORIENTAL_STATEMENT)}
        product = motor(manufacturer="Unknown Corp")
        assert estimate_lead_time(product, profiles, retrieved_at=RETRIEVED_AT) is None

    def test_manufacturer_slug_normalization(self) -> None:
        profiles = {"oriental-motor": profile(ORIENTAL_STATEMENT)}
        product = motor(manufacturer="  ORIENTAL   MOTOR ")
        figure = estimate_lead_time(product, profiles, retrieved_at=RETRIEVED_AT)
        assert figure is not None

    def test_unparseable_statement_yields_none(self) -> None:
        profiles = {"oriental-motor": profile("contact us for delivery times")}
        assert estimate_lead_time(motor(), profiles, retrieved_at=RETRIEVED_AT) is None

    def test_profile_without_lead_time_facts(self) -> None:
        other = VendorFact(
            label="Warranty",
            value="2-year warranty",
            kind="warranty",
            sources=[citation()],
        )
        profiles = {
            "oriental-motor": VendorProfile(
                slug="oriental-motor",
                display_name="Oriental Motor",
                facts=[other],
            )
        }
        assert estimate_lead_time(motor(), profiles, retrieved_at=RETRIEVED_AT) is None

    def test_first_parseable_fact_wins(self) -> None:
        unparseable = VendorFact(
            label="Vague statement",
            value="ships fast",
            kind="lead_time_statement",
            sources=[citation("https://example.com/a")],
        )
        profiles = {
            "oriental-motor": profile(
                "2 weeks", extra_facts=[unparseable]
            )  # unparseable listed first
        }
        figure = estimate_lead_time(motor(), profiles, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert (figure.value.value, figure.value.unit) == (2.0, "weeks")


TABLE = "products"


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


PROFILES = {"oriental-motor": profile(ORIENTAL_STATEMENT)}


@pytest.mark.unit
class TestLeadTimesCli:
    def seed(self, client: DynamoDBClient) -> None:
        assert client.create(motor(1))  # target
        assert client.create(motor(2, manufacturer="Unknown Corp"))  # no profile
        assert client.create(
            motor(3, lead_time={"value": 30, "unit": "days"})
        )  # listed — never overridden

    def test_apply_writes_estimate_and_skips_the_rest(
        self, client: DynamoDBClient
    ) -> None:
        self.seed(client)
        stats = run_lead_times(
            product_types=["motor"],
            apply=True,
            client=client,
            retrieved_at=RETRIEVED_AT,
            profiles=PROFILES,
        )
        assert stats.estimated == 1
        assert stats.written == 1
        assert stats.skipped_has_listed == 1
        assert stats.failures == []

        stored = client.read(UUID(int=1), Motor)
        assert stored is not None
        figure = stored.lead_time_estimate
        assert figure is not None
        # Round-trips through DynamoDB (Decimal) into a valid figure.
        assert SourcedFigure.model_validate(figure.model_dump())
        assert figure.value.value == 3.0
        assert figure.value.unit == "days"
        assert figure.confidence == "medium"
        # Listed lead_time never touched.
        assert stored.lead_time is None

        untouched = client.read(UUID(int=2), Motor)
        assert untouched is not None
        assert untouched.lead_time_estimate is None

        listed = client.read(UUID(int=3), Motor)
        assert listed is not None
        assert listed.lead_time_estimate is None
        assert listed.lead_time is not None
        assert listed.lead_time.value == 30

    def test_dry_run_writes_nothing(self, client: DynamoDBClient) -> None:
        self.seed(client)
        stats = run_lead_times(
            product_types=["motor"],
            apply=False,
            client=client,
            retrieved_at=RETRIEVED_AT,
            profiles=PROFILES,
        )
        assert stats.estimated == 1
        assert stats.written == 0
        stored = client.read(UUID(int=1), Motor)
        assert stored is not None
        assert stored.lead_time_estimate is None

    def test_second_run_skips_existing_estimate(self, client: DynamoDBClient) -> None:
        self.seed(client)
        first = run_lead_times(
            product_types=["motor"],
            apply=True,
            client=client,
            retrieved_at=RETRIEVED_AT,
            profiles=PROFILES,
        )
        assert first.written == 1
        second = run_lead_times(
            product_types=["motor"],
            apply=True,
            client=client,
            retrieved_at=RETRIEVED_AT,
            profiles=PROFILES,
        )
        assert second.estimated == 0
        assert second.written == 0
        assert second.skipped_has_estimate == 1

    def test_no_parseable_statements_touches_nothing(
        self, client: DynamoDBClient
    ) -> None:
        self.seed(client)
        stats = run_lead_times(
            product_types=["motor"],
            apply=True,
            client=client,
            retrieved_at=RETRIEVED_AT,
            profiles={"oriental-motor": profile("ships fast")},
        )
        assert stats.scanned == 0  # DB never queried
        assert stats.estimated == 0
        assert stats.written == 0


@pytest.mark.unit
class TestLiveRegistryParses:
    def test_oriental_motor_statement_in_repo_registry_parses(self) -> None:
        """The shipped registry's one lead_time_statement must keep parsing.

        If someone edits data/vendors/oriental-motor.json into a shape
        the parser no longer understands, this surfaces it here instead
        of as a silent no-op run.
        """
        from specodex.vendors.registry import load_vendor_profiles

        profiles = load_vendor_profiles()
        statements = [
            fact.value
            for prof in profiles.values()
            for fact in prof.facts
            if fact.kind == "lead_time_statement"
        ]
        assert statements, "registry lost its lead_time_statement facts"
        parseable = [s for s in statements if parse_lead_statement(s) is not None]
        assert parseable, "no registry lead-time statement parses any more"
