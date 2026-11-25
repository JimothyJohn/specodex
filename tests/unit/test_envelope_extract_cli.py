"""cli/envelope_extract.py: planning (pure) and the enrich-only write (moto)."""

from __future__ import annotations

from decimal import Decimal

import pytest

import json

from cli import envelope_extract
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
    "unverified": [],
}


# Pass "b" reads the same envelope within 10% on both axes (4.2 in vs 4 in).
EX_SMALL_B = {
    **EX_SMALL,
    "length": {"value": 4.2, "unit": "in"},
    "width": {"value": 58, "unit": "mm"},
}
AGREED = [[EX_SMALL], [EX_SMALL_B]]


@pytest.mark.unit
class TestPlanUpdates:
    def test_fills_only_missing_fields(self):
        rows = [
            _row("a", part_number="AB042-005"),
            _row("b", part_number="AB042-010", weight={"value": D("1"), "unit": "kg"}),
        ]
        updates = dict((r["SK"], s) for r, s in plan_updates(rows, AGREED))
        assert updates["PRODUCT#a"] == {
            # Agreed readings keep the larger value per axis.
            "dimensions": {
                "length": {"value": 106.7, "unit": "mm"},
                "width": {"value": 60.0, "unit": "mm"},
            },
            "max_dimension": {"value": 106.7, "unit": "mm"},
            "weight": {"value": 0.9072, "unit": "kg"},
        }
        assert "weight" not in updates["PRODUCT#b"]

    def test_unusable_legacy_dims_count_as_missing(self):
        rows = [_row("a", part_number="AB042-005", dimensions={"unit": "mm"})]
        ((_, to_set),) = plan_updates(rows, AGREED)
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
        assert plan_updates(rows, [junk, junk]) == []

    def test_unmatched_rows_untouched(self):
        assert plan_updates([_row("a", part_number="ZZ999")], AGREED) == []

    def test_single_reading_fills_weight_only(self):
        rows = [_row("a", part_number="AB042-005")]
        assert plan_updates(rows, [[EX_SMALL]]) == [
            (rows[0], {"weight": {"value": 0.9072, "unit": "kg"}})
        ]
        assert plan_updates(rows, [[EX_SMALL], []]) == [
            (rows[0], {"weight": {"value": 0.9072, "unit": "kg"}})
        ]

    def test_weight_from_second_reading_when_first_has_none(self):
        rows = [_row("a", part_number="AB042-005")]
        a = {**EX_SMALL, "weight": None}
        ((_, to_set),) = plan_updates(rows, [[a], [EX_SMALL_B]])
        assert to_set["weight"] == {"value": 0.9072, "unit": "kg"}

    def test_disagreeing_dims_withheld_weight_kept(self):
        # Apex AB090 shape: one reading takes the Ø80 pilot as the length.
        rows = [_row("a", part_number="AB090-005")]
        a = {
            **EX_SMALL,
            "designator": "AB090",
            "length": {"value": 126.5, "unit": "mm"},
        }
        b = {**a, "length": {"value": 80, "unit": "mm"}}
        ((_, to_set),) = plan_updates(rows, [[a], [b]])
        assert "dimensions" not in to_set and "max_dimension" not in to_set
        assert to_set["weight"] == {"value": 0.9072, "unit": "kg"}

    def test_axis_missing_in_one_reading_withheld(self):
        rows = [_row("a", part_number="AB042-005")]
        b = {**EX_SMALL, "width": None}
        ((_, to_set),) = plan_updates(rows, [[EX_SMALL], [b]])
        assert "dimensions" not in to_set

    def test_conflicting_weights_withheld(self):
        rows = [_row("a", part_number="AB042-005")]
        b = {**EX_SMALL_B, "weight": {"value": 3, "unit": "lb"}}
        ((_, to_set),) = plan_updates(rows, [[EX_SMALL], [b]])
        assert "weight" not in to_set
        assert "dimensions" in to_set

    def test_readings_matching_different_rows_never_combine(self):
        rows = [_row("a", part_number="AB042-005"), _row("b", part_number="AB060-005")]
        b = {**EX_SMALL_B, "designator": "AB060"}
        updates = dict((r["SK"], s) for r, s in plan_updates(rows, [[EX_SMALL], [b]]))
        assert set(updates["PRODUCT#a"]) == {"weight"}
        assert set(updates["PRODUCT#b"]) == {"weight"}

    def test_unverified_axis_discredits_reading_dims(self):
        rows = [_row("a", part_number="AB042-005")]
        b = {**EX_SMALL_B, "unverified": ["width"]}
        ((_, to_set),) = plan_updates(rows, [[EX_SMALL], [b]])
        assert "dimensions" not in to_set
        assert to_set["weight"] == {"value": 0.9072, "unit": "kg"}

    def test_unverified_weight_never_written(self):
        rows = [_row("a", part_number="AB042-005")]
        a = {**EX_SMALL, "unverified": ["weight"]}
        b = {**EX_SMALL_B, "weight": None}
        ((_, to_set),) = plan_updates(rows, [[a], [b]])
        assert "weight" not in to_set and "dimensions" in to_set

    def test_record_without_check_fails_closed(self):
        rows = [_row("a", part_number="AB042-005")]
        a = {k: v for k, v in EX_SMALL.items() if k != "unverified"}
        b = {k: v for k, v in EX_SMALL_B.items() if k != "unverified"}
        assert plan_updates(rows, [[a], [b]]) == []
        junk = {**EX_SMALL, "unverified": "nope"}
        assert plan_updates(rows, [[junk], [junk]]) == []

    def test_dims_and_weight_match_independently(self):
        # Parker 637: the outline drawing labels a frame range (dims only);
        # the weight sits in a table row with the exact model. A row must
        # take dims from one and weight from the other.
        rows = [_row("a", part_number="637/K D6R 04.S3-7-DEV")]
        drawing = {
            "designator": "637/K D6R 02...10",
            "length": {"value": 280, "unit": "mm"},
            "width": {"value": 65, "unit": "mm"},
            "height": {"value": 400, "unit": "mm"},
            "weight": None,
            "page_number": 18,
            "unverified": [],
        }
        table = {
            "designator": "K D6R 04 .S3-7",
            "weight": {"value": 5.0, "unit": "kg"},
            "page_number": 14,
            "unverified": [],
        }
        ((_, to_set),) = plan_updates(rows, [[drawing, table], [drawing]])
        assert to_set["weight"] == {"value": 5.0, "unit": "kg"}
        assert to_set["max_dimension"] == {"value": 400.0, "unit": "mm"}


@pytest.mark.unit
class TestCache:
    def test_old_single_reading_record_is_not_a_hit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(envelope_extract, "CACHE_DIR", tmp_path)
        url = "https://example.com/a.pdf"
        envelope_extract._cache_path(url).write_text(
            json.dumps(
                {"url": url, "pages": [1], "extractions": [EX_SMALL], "error": None}
            )
        )
        assert not envelope_extract._is_cached(url)
        envelope_extract._cache_path(url).write_text("{not json")
        assert not envelope_extract._is_cached(url)
        rec = {
            "version": envelope_extract.CACHE_VERSION,
            "verifier": envelope_extract.VERIFIER_VERSION,
            "url": url,
            "passes": {},
        }
        envelope_extract._cache_path(url).write_text(json.dumps(rec))
        assert envelope_extract._is_cached(url)
        got = envelope_extract.extract_for_datasheet(url, [], "k", 12, refresh=False)
        assert got["cached"] is True and got["url"] == url

    def test_stale_verifier_rechecks_without_gemini(self, tmp_path, monkeypatch):
        import pymupdf

        monkeypatch.setattr(envelope_extract, "CACHE_DIR", tmp_path)
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((40, 100), "SGMJV-A5A@A6@")
        page.insert_text((200, 100), "94")
        page.insert_text((260, 100), "3.1")
        pdf = doc.tobytes()
        url = "https://example.com/omron.pdf"
        reading = {
            "designator": "SGMJV-A5A A6",
            "length": {"value": 94, "unit": "mm"},
            "weight": {"value": 0.5, "unit": "kg"},  # not printed
            "page_number": 0,
            "unverified": ["length", "weight"],  # verdict of an older checker
        }
        rec = {
            "version": envelope_extract.CACHE_VERSION,
            "verifier": envelope_extract.VERIFIER_VERSION - 1,
            "url": url,
            "pages": [0],
            "passes": {"a": [reading], "b": [reading]},
            "error": None,
        }
        envelope_extract._cache_path(url).write_text(json.dumps(rec))
        monkeypatch.setattr(envelope_extract, "get_document", lambda u: pdf)

        def no_gemini(*a, **k):
            raise AssertionError("Gemini must not be called")

        monkeypatch.setattr(envelope_extract, "extract_envelopes", no_gemini)
        got = envelope_extract.extract_for_datasheet(url, [], "k", 12, refresh=False)
        assert got["verifier"] == envelope_extract.VERIFIER_VERSION
        assert got["passes"]["a"][0]["unverified"] == ["weight"]
        saved = json.loads(envelope_extract._cache_path(url).read_text())
        assert saved["passes"]["b"][0]["unverified"] == ["weight"]


@pytest.mark.unit
def test_page_fragments_share_one_document():
    u = "https://example.com/cat.pdf"
    assert envelope_extract.document_url(u + "#page=3") == u
    assert envelope_extract.document_url(u + "#page=8") == u
    assert envelope_extract.document_url(u) == u
    assert envelope_extract.document_url("https://x/a.pdf#a#b") == "https://x/a.pdf"


@pytest.mark.unit
class TestApplyUpdate:
    def test_write_then_second_write_is_conflict(self, dynamodb_table):
        row = _row("a", part_number="AB042-005")
        dynamodb_table.put_item(Item=row)
        ((_, to_set),) = plan_updates([row], AGREED)
        assert apply_update(dynamodb_table, row, to_set) == "written"
        stored = dynamodb_table.get_item(Key={"PK": row["PK"], "SK": row["SK"]})["Item"]
        assert stored["max_dimension"] == {"value": D("106.7"), "unit": "mm"}
        assert stored["part_number"] == "AB042-005"
        # Same plan replayed against the now-populated row must not overwrite.
        assert apply_update(dynamodb_table, row, to_set) == "conflict"

    def test_concurrent_value_wins(self, dynamodb_table):
        row = _row("a", part_number="AB042-005")
        dynamodb_table.put_item(Item={**row, "weight": {"value": D("7"), "unit": "kg"}})
        ((_, to_set),) = plan_updates([row], AGREED)  # planned from the stale read
        assert apply_update(dynamodb_table, row, to_set) == "conflict"
        stored = dynamodb_table.get_item(Key={"PK": row["PK"], "SK": row["SK"]})["Item"]
        assert stored["weight"] == {"value": D("7"), "unit": "kg"}
        assert "dimensions" not in stored

    def test_deleted_row_not_recreated(self, dynamodb_table):
        row = _row("gone", part_number="AB042-005")
        ((_, to_set),) = plan_updates([row], AGREED)
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
