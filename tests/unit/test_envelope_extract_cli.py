"""cli/envelope_extract.py: planning (pure) and the enrich-only write (moto)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cli.envelope_extract import apply_update, plan_updates, scan_candidates

D = Decimal


def _row(sk, **kw):
    return {
        "PK": "PRODUCT#DRIVE",
        "SK": f"PRODUCT#{sk}",
        "product_type": "drive",
        "manufacturer": "Acme",
        "datasheet_url": "https://example.com/a.pdf",
        **kw,
    }


EX_SMALL = {
    "designator": "AB042",
    "length": {"value": 4, "unit": "in"},
    "width": {"value": 60, "unit": "mm"},
    "height": None,
    "weight": {"value": 2, "unit": "lb"},
    "page_number": 3,
}


@pytest.mark.unit
class TestPlanUpdates:
    def test_fills_only_missing_fields(self):
        rows = [
            _row("a", part_number="AB042-005"),
            _row("b", part_number="AB042-010", weight={"value": D("1"), "unit": "kg"}),
        ]
        updates = dict((r["SK"], s) for r, s in plan_updates(rows, [EX_SMALL]))
        assert updates["PRODUCT#a"] == {
            "dimensions": {
                "length": {"value": 101.6, "unit": "mm"},
                "width": {"value": 60.0, "unit": "mm"},
            },
            "max_dimension": {"value": 101.6, "unit": "mm"},
            "weight": {"value": 0.9072, "unit": "kg"},
        }
        assert "weight" not in updates["PRODUCT#b"]

    def test_unusable_legacy_dims_count_as_missing(self):
        rows = [_row("a", part_number="AB042-005", dimensions={"unit": "mm"})]
        ((_, to_set),) = plan_updates(rows, [EX_SMALL])
        assert "dimensions" in to_set

    def test_empty_or_junk_extractions_ignored(self):
        rows = [_row("a", part_number="AB042-005")]
        junk = [
            {"designator": "AB042", "page_number": 1},
            {
                "designator": "AB042",
                "length": {"value": -3, "unit": "mm"},
                "page_number": 1,
            },
            {"not": "an extraction"},
        ]
        assert plan_updates(rows, junk) == []

    def test_unmatched_rows_untouched(self):
        assert plan_updates([_row("a", part_number="ZZ999")], [EX_SMALL]) == []


@pytest.mark.unit
class TestApplyUpdate:
    def test_write_then_second_write_is_conflict(self, dynamodb_table):
        row = _row("a", part_number="AB042-005")
        dynamodb_table.put_item(Item=row)
        ((_, to_set),) = plan_updates([row], [EX_SMALL])
        assert apply_update(dynamodb_table, row, to_set) == "written"
        stored = dynamodb_table.get_item(Key={"PK": row["PK"], "SK": row["SK"]})["Item"]
        assert stored["max_dimension"] == {"value": D("101.6"), "unit": "mm"}
        assert stored["part_number"] == "AB042-005"
        # Same plan replayed against the now-populated row must not overwrite.
        assert apply_update(dynamodb_table, row, to_set) == "conflict"

    def test_concurrent_value_wins(self, dynamodb_table):
        row = _row("a", part_number="AB042-005")
        dynamodb_table.put_item(Item={**row, "weight": {"value": D("7"), "unit": "kg"}})
        ((_, to_set),) = plan_updates([row], [EX_SMALL])  # planned from the stale read
        assert apply_update(dynamodb_table, row, to_set) == "conflict"
        stored = dynamodb_table.get_item(Key={"PK": row["PK"], "SK": row["SK"]})["Item"]
        assert stored["weight"] == {"value": D("7"), "unit": "kg"}
        assert "dimensions" not in stored

    def test_deleted_row_not_recreated(self, dynamodb_table):
        row = _row("gone", part_number="AB042-005")
        ((_, to_set),) = plan_updates([row], [EX_SMALL])
        assert apply_update(dynamodb_table, row, to_set) == "conflict"
        assert "Item" not in dynamodb_table.get_item(
            Key={"PK": row["PK"], "SK": row["SK"]}
        )


@pytest.mark.unit
def test_scan_candidates_filters(dynamodb_table):
    full = _row(
        "full",
        part_number="X1",
        dimensions={"length": {"value": D("10"), "unit": "mm"}},
        weight={"value": D("1"), "unit": "kg"},
    )
    items = [
        full,
        _row("needs", part_number="X2"),
        _row("nourl", part_number="X3", datasheet_url=None),
        {
            **_row("motor", part_number="X4"),
            "PK": "PRODUCT#MOTOR",
            "product_type": "motor",
        },
        {"PK": "DATASHEET#DRIVE", "SK": "DATASHEET#1", "datasheet_url": "u"},
    ]
    for it in items:
        dynamodb_table.put_item(Item={k: v for k, v in it.items() if v is not None})
    got = {r["SK"] for r in scan_candidates(dynamodb_table, "drive", None)}
    assert got == {"PRODUCT#needs"}
    assert {r["SK"] for r in scan_candidates(dynamodb_table, None, "acme")} == {
        "PRODUCT#needs",
        "PRODUCT#motor",
    }
