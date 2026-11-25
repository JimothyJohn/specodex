"""Envelope migration: legacy dimensions/weight → typed mm/kg + max_dimension.

Runs against moto DynamoDB (real boto3 request path, condition
expressions included), not a mocked client.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from specodex.admin.envelope_migration import migrate_envelopes, plan_envelope_update
from specodex.models.motor import Motor

D = Decimal


def _row(sk: str, **attrs) -> dict:
    return {
        "PK": "PRODUCT#MOTOR",
        "SK": f"PRODUCT#{sk}",
        "product_type": "motor",
        "product_name": "M",
        "manufacturer": "Acme",
        "rated_power": {"value": D("400"), "unit": "W"},
        **attrs,
    }


LEGACY_INCH = _row(
    "inch",
    dimensions={"length": D("20.57"), "unit": "in"},
    weight={"value": D("10"), "unit": "lb"},
)
LEGACY_MM = _row(
    "mm",
    dimensions={
        "width": D("486"),
        "length": D("371"),
        "height": D("1035"),
        "unit": "mm",
    },
    weight={"value": D("1.6"), "unit": "kg"},
)
UNIT_ONLY = _row("unitonly", dimensions={"unit": "mm"})
NO_ENVELOPE = _row("none")
DATASHEET = {"PK": "DATASHEET#MOTOR", "SK": "DATASHEET#x", "dimensions": {"unit": "in"}}


@pytest.mark.unit
class TestPlan:
    def test_legacy_inch_row(self) -> None:
        to_set, to_remove = plan_envelope_update(LEGACY_INCH)
        assert to_remove == []
        assert to_set == {
            "dimensions": {"length": {"value": 522.5, "unit": "mm"}},
            "weight": {"value": 4.536, "unit": "kg"},
            "max_dimension": {"value": 522.5, "unit": "mm"},
        }

    def test_kg_weight_untouched(self) -> None:
        to_set, _ = plan_envelope_update(LEGACY_MM)
        assert "weight" not in to_set
        assert to_set["max_dimension"] == {"value": 1035.0, "unit": "mm"}

    def test_unit_only_dimensions_removed(self) -> None:
        assert plan_envelope_update(UNIT_ONLY) == ({}, ["dimensions"])

    def test_row_without_envelope_is_noop(self) -> None:
        assert plan_envelope_update(NO_ENVELOPE) == ({}, [])

    def test_stale_max_dimension_removed_when_no_dims(self) -> None:
        row = _row("stale", max_dimension={"value": D("5"), "unit": "mm"})
        assert plan_envelope_update(row) == ({}, ["max_dimension"])

    def test_plan_matches_what_the_model_writes(self) -> None:
        # The migration must produce exactly what a fresh ingest would.
        to_set, _ = plan_envelope_update(LEGACY_INCH)
        m = Motor.model_validate(
            {
                "product_name": "M",
                "manufacturer": "Acme",
                "dimensions": LEGACY_INCH["dimensions"],
                "weight": LEGACY_INCH["weight"],
            }
        ).model_dump(mode="json", exclude_none=True)
        for attr, val in to_set.items():
            assert m[attr] == val


@pytest.mark.unit
class TestMigrateAgainstDynamo:
    @pytest.fixture
    def table(self, dynamodb_table):
        for item in (LEGACY_INCH, LEGACY_MM, UNIT_ONLY, NO_ENVELOPE, DATASHEET):
            dynamodb_table.put_item(Item=item)
        return dynamodb_table

    def _get(self, table, item):
        return table.get_item(Key={"PK": item["PK"], "SK": item["SK"]})["Item"]

    def test_dry_run_writes_nothing(self, table) -> None:
        r = migrate_envelopes(table, apply=False)
        assert (r.scanned, r.planned, r.written) == (4, 3, 0), r.to_dict()
        assert self._get(table, LEGACY_INCH)["dimensions"] == LEGACY_INCH["dimensions"]

    def test_apply_then_idempotent(self, table) -> None:
        r = migrate_envelopes(table, apply=True)
        assert (r.planned, r.written, r.conflicts, r.dims_dropped) == (3, 3, 0, 1), (
            r.to_dict()
        )

        inch = self._get(table, LEGACY_INCH)
        assert inch["dimensions"] == {"length": {"value": D("522.5"), "unit": "mm"}}
        assert inch["max_dimension"] == {"value": D("522.5"), "unit": "mm"}
        assert inch["weight"] == {"value": D("4.536"), "unit": "kg"}
        # Untouched attributes survive byte-for-byte.
        assert inch["rated_power"] == LEGACY_INCH["rated_power"]
        assert "dimensions" not in self._get(table, UNIT_ONLY)
        # Non-product rows are never touched.
        assert self._get(table, DATASHEET) == DATASHEET

        # Migrated rows load through the model unchanged.
        Motor.model_validate(inch)

        again = migrate_envelopes(table, apply=True)
        assert (again.planned, again.written) == (0, 0), again.to_dict()

    def test_concurrent_edit_is_skipped_not_clobbered(self, table, monkeypatch) -> None:
        # Simulate another writer landing between scan and update.
        real_update = table.update_item

        def racing_update(**kw):
            if kw["Key"]["SK"] == LEGACY_INCH["SK"]:
                table.put_item(
                    Item={**LEGACY_INCH, "dimensions": {"length": D("1"), "unit": "mm"}}
                )
            return real_update(**kw)

        monkeypatch.setattr(table, "update_item", racing_update)
        r = migrate_envelopes(table, apply=True)
        assert r.conflicts == 1, r.to_dict()
        assert self._get(table, LEGACY_INCH)["dimensions"] == {
            "length": D("1"),
            "unit": "mm",
        }
