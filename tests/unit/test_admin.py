"""Unit tests for admin tooling (blacklist + dev/prod operations)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from specodex.admin.blacklist import Blacklist
from specodex.admin.operations import (
    demote,
    diff,
    format_diff_table,
    format_promote_summary,
    format_purge_summary,
    promote,
    purge,
)
from specodex.db.dynamo import DynamoDBClient
from specodex.models.drive import Drive
from specodex.models.manufacturer import Manufacturer


# ── Blacklist ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestBlacklist:
    def test_empty_when_file_missing(self, tmp_path: Path) -> None:
        bl = Blacklist(path=tmp_path / "missing.json")
        assert len(bl) == 0
        assert bl.names() == []

    def test_load_existing(self, tmp_path: Path) -> None:
        f = tmp_path / "bl.json"
        f.write_text(json.dumps({"banned_manufacturers": ["ACME", "BadCo"]}))
        bl = Blacklist(path=f)
        assert len(bl) == 2
        assert bl.contains("ACME")
        assert bl.contains("BadCo")
        assert not bl.contains("GoodCo")

    def test_add_new(self, tmp_path: Path) -> None:
        bl = Blacklist(path=tmp_path / "bl.json")
        assert bl.add("ACME") is True
        assert bl.add("ACME") is False  # idempotent
        assert bl.contains("ACME")

    def test_remove(self, tmp_path: Path) -> None:
        bl = Blacklist(path=tmp_path / "bl.json")
        bl.add("ACME")
        first = bl.remove("ACME")
        second = bl.remove("ACME")
        assert first is True
        assert second is False
        assert not bl.contains("ACME")

    def test_save_round_trip(self, tmp_path: Path) -> None:
        f = tmp_path / "bl.json"
        bl = Blacklist(path=f)
        bl.add("ACME")
        bl.add("BadCo")
        bl.save()

        fresh = Blacklist(path=f)
        assert fresh.names() == ["ACME", "BadCo"]

    def test_save_sorted_output(self, tmp_path: Path) -> None:
        f = tmp_path / "bl.json"
        bl = Blacklist(path=f)
        bl.add("Zebra")
        bl.add("Alpha")
        bl.save()
        data = json.loads(f.read_text())
        assert data["banned_manufacturers"] == ["Alpha", "Zebra"]

    def test_invalid_format_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "bl.json"
        f.write_text(json.dumps({"banned_manufacturers": "not-a-list"}))
        with pytest.raises(ValueError):
            Blacklist(path=f)

    def test_add_is_case_insensitive(self, tmp_path: Path) -> None:
        bl = Blacklist(path=tmp_path / "bl.json")
        assert bl.add("ACME") is True
        assert bl.add("acme") is False
        assert bl.add("Acme") is False
        assert len(bl) == 1
        # First-added casing preserved.
        assert bl.names() == ["ACME"]

    def test_contains_is_case_insensitive(self, tmp_path: Path) -> None:
        bl = Blacklist(path=tmp_path / "bl.json")
        bl.add("ABB")
        assert bl.contains("abb") is True
        assert bl.contains("Abb") is True
        assert bl.contains("AB B") is False

    def test_remove_is_case_insensitive(self, tmp_path: Path) -> None:
        bl = Blacklist(path=tmp_path / "bl.json")
        bl.add("ACME")
        removed = bl.remove("acme")
        assert removed is True
        assert len(bl) == 0

    def test_load_dedupes_existing_duplicates(self, tmp_path: Path) -> None:
        f = tmp_path / "bl.json"
        f.write_text(json.dumps({"banned_manufacturers": ["ACME", "acme", "Acme"]}))
        bl = Blacklist(path=f)
        assert len(bl) == 1
        assert bl.names() == ["ACME"]


# ── Fixtures for operations tests ──────────────────────────────────


def _make_drive(manufacturer: str, product_id: str) -> Drive:
    return Drive(
        product_id=UUID(product_id),
        product_name=f"{manufacturer}-drive",
        manufacturer=manufacturer,
        part_number=f"{manufacturer}-001",
    )


def _make_mock_client(
    products_by_type: dict[str, list[Drive]] | None = None,
    manufacturers: list[Manufacturer] | None = None,
) -> MagicMock:
    """Build a MagicMock standing in for DynamoDBClient.

    - ``list(model_class, ...)`` returns the products for the matching type.
    - ``batch_create(models)`` returns ``len(models)``.
    - ``table.query(...)`` returns Manufacturer items keyed by PK=MANUFACTURER.
    """
    client = MagicMock(spec=DynamoDBClient)
    client.table_name = "products-mock"

    products_by_type = products_by_type or {}
    manufacturers = manufacturers or []

    def fake_list(model_class, limit=None, filter_expr=None, filter_values=None):
        default = model_class.model_fields["product_type"].default
        items = list(products_by_type.get(default, []))
        if filter_values and ":mfg" in filter_values:
            mfg = filter_values[":mfg"]
            items = [p for p in items if p.manufacturer == mfg]
        return items

    client.list.side_effect = fake_list
    client.batch_create.side_effect = lambda models: len(list(models))

    # Manufacturer query path uses client.table.query directly.
    mfg_items = [
        {"PK": "MANUFACTURER", "SK": f"MANUFACTURER#{m.id}", "name": m.name}
        for m in manufacturers
    ]
    client.table = MagicMock()
    client.table.query.return_value = {"Items": mfg_items}

    return client


# ── Diff ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDiff:
    def test_disjoint(self) -> None:
        src = _make_mock_client(
            {
                "drive": [
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000001"),
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000002"),
                ]
            }
        )
        tgt = _make_mock_client({"drive": []})

        result = diff(src, tgt, "drive", "dev", "prod")
        assert len(result.only_in_source) == 2
        assert result.only_in_target == []
        assert result.in_both == []

    def test_partial_overlap(self) -> None:
        common = _make_drive("ABB", "00000000-0000-0000-0000-000000000001")
        src = _make_mock_client(
            {
                "drive": [
                    common,
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000002"),
                ]
            }
        )
        tgt = _make_mock_client(
            {
                "drive": [
                    common,
                    _make_drive("XYZ", "00000000-0000-0000-0000-000000000003"),
                ]
            }
        )

        result = diff(src, tgt, "drive", "dev", "prod")
        assert result.only_in_source == ["00000000-0000-0000-0000-000000000002"]
        assert result.only_in_target == ["00000000-0000-0000-0000-000000000003"]
        assert result.in_both == ["00000000-0000-0000-0000-000000000001"]


# ── Promote ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPromote:
    def test_dry_run_no_writes(self, tmp_path: Path) -> None:
        src = _make_mock_client(
            {
                "drive": [
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000001"),
                ]
            }
        )
        tgt = _make_mock_client()
        bl = Blacklist(path=tmp_path / "bl.json")

        result = promote(src, tgt, "drive", bl, apply=False)

        assert result.applied is False
        assert result.considered == 1
        assert result.promoted_products == 1  # dry-run count
        tgt.batch_create.assert_not_called()

    def test_apply_writes(self, tmp_path: Path) -> None:
        src = _make_mock_client(
            {
                "drive": [
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000001"),
                    _make_drive("Siemens", "00000000-0000-0000-0000-000000000002"),
                ]
            }
        )
        tgt = _make_mock_client()
        bl = Blacklist(path=tmp_path / "bl.json")

        result = promote(src, tgt, "drive", bl, apply=True)

        assert result.applied is True
        assert result.promoted_products == 2
        assert tgt.batch_create.called
        written_models = tgt.batch_create.call_args_list[0].args[0]
        assert len(written_models) == 2

    def test_blacklist_blocks_matching_manufacturer(self, tmp_path: Path) -> None:
        src = _make_mock_client(
            {
                "drive": [
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000001"),
                    _make_drive("BadCo", "00000000-0000-0000-0000-000000000002"),
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000003"),
                ]
            }
        )
        tgt = _make_mock_client()
        bl = Blacklist(path=tmp_path / "bl.json")
        bl.add("BadCo")

        result = promote(src, tgt, "drive", bl, apply=True)

        assert result.considered == 3
        assert result.promoted_products == 2
        assert "BadCo" in result.blocked_by_blacklist
        # Only ABB products made it through
        written = tgt.batch_create.call_args_list[0].args[0]
        assert all(m.manufacturer == "ABB" for m in written)

    def test_blacklist_match_is_case_insensitive(self, tmp_path: Path) -> None:
        src = _make_mock_client(
            {
                "drive": [
                    _make_drive("BadCo", "00000000-0000-0000-0000-000000000001"),
                ]
            }
        )
        tgt = _make_mock_client()
        bl = Blacklist(path=tmp_path / "bl.json")
        bl.add("BADCO")  # banned in all-caps, product carries mixed case

        result = promote(src, tgt, "drive", bl, apply=False)

        assert result.blocked_by_blacklist == ["BadCo"]
        assert result.promoted_products == 0

    def test_min_quality_drops_low_score_products(self, tmp_path: Path) -> None:
        # `_make_drive` only sets part_number among spec fields → score is
        # ~1/N (very low). Populating more fields on `better` lifts it above
        # a 0.20 threshold, so it survives while `bare` is dropped.
        bare = _make_drive("ABB", "00000000-0000-0000-0000-000000000001")
        better = _make_drive("ABB", "00000000-0000-0000-0000-000000000002")
        better.rated_power = "5;kW"
        better.rated_current = "10;A"
        better.input_voltage = "400;V"
        better.peak_current = "20;A"
        better.switching_frequency = "8;kHz"
        better.ip_rating = "IP20"
        better.weight = "5;kg"

        src = _make_mock_client({"drive": [bare, better]})
        tgt = _make_mock_client()
        bl = Blacklist(path=tmp_path / "bl.json")

        result = promote(src, tgt, "drive", bl, apply=False, min_quality=0.20)

        assert result.considered == 2
        assert result.blocked_by_quality == 1
        assert result.promoted_products == 1
        assert result.min_quality == 0.20

    def test_min_quality_zero_is_no_op(self, tmp_path: Path) -> None:
        bare = _make_drive("ABB", "00000000-0000-0000-0000-000000000001")
        src = _make_mock_client({"drive": [bare]})
        tgt = _make_mock_client()
        bl = Blacklist(path=tmp_path / "bl.json")

        result = promote(src, tgt, "drive", bl, apply=False, min_quality=0.0)

        assert result.blocked_by_quality == 0
        assert result.promoted_products == 1

    def test_min_quality_out_of_range_raises(self, tmp_path: Path) -> None:
        src = _make_mock_client({"drive": []})
        tgt = _make_mock_client()
        bl = Blacklist(path=tmp_path / "bl.json")

        with pytest.raises(ValueError):
            promote(src, tgt, "drive", bl, apply=False, min_quality=1.5)

    def test_manufacturer_records_tied_to_promoted_products(
        self, tmp_path: Path
    ) -> None:
        src = _make_mock_client(
            products_by_type={
                "drive": [
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000001"),
                ]
            },
            manufacturers=[
                Manufacturer(name="ABB"),
                Manufacturer(name="OrphanCo"),  # not on any promoted product
            ],
        )
        tgt = _make_mock_client()
        bl = Blacklist(path=tmp_path / "bl.json")

        result = promote(src, tgt, "drive", bl, apply=True)

        assert result.promoted_manufacturers == 1
        mfg_calls = [
            c.args[0]
            for c in tgt.batch_create.call_args_list
            if c.args[0] and isinstance(c.args[0][0], Manufacturer)
        ]
        assert mfg_calls, "expected a batch_create call for Manufacturer records"
        assert [m.name for m in mfg_calls[0]] == ["ABB"]


# ── Demote ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDemote:
    def test_no_blacklist_check(self, tmp_path: Path) -> None:
        src = _make_mock_client(
            {
                "drive": [
                    _make_drive("BadCo", "00000000-0000-0000-0000-000000000001"),
                ]
            }
        )
        tgt = _make_mock_client()

        result = demote(src, tgt, "drive", apply=True)

        assert result.promoted_products == 1
        assert result.blocked_by_blacklist == []


# ── Purge ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPurge:
    def test_requires_at_least_one_filter(self) -> None:
        client = _make_mock_client()
        with pytest.raises(ValueError):
            purge(client)

    def test_dry_run_reports_matches(self) -> None:
        client = _make_mock_client(
            {
                "drive": [
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000001"),
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000002"),
                ]
            }
        )
        result = purge(client, product_type="drive", apply=False)
        assert result.matched == 2
        assert result.deleted == 0
        assert result.applied is False

    def test_apply_issues_batch_delete(self) -> None:
        client = _make_mock_client(
            {
                "drive": [
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000001"),
                ]
            }
        )
        writer = MagicMock()
        client.table.batch_writer.return_value.__enter__.return_value = writer

        result = purge(client, product_type="drive", apply=True)

        assert result.applied is True
        assert result.deleted == 1
        writer.delete_item.assert_called_once()
        (call,) = writer.delete_item.call_args_list
        assert call.kwargs["Key"]["PK"] == "PRODUCT#DRIVE"

    def test_scope_filter_by_manufacturer(self) -> None:
        client = _make_mock_client(
            {
                "drive": [
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000001"),
                    _make_drive("Siemens", "00000000-0000-0000-0000-000000000002"),
                ]
            }
        )
        result = purge(client, product_type="drive", manufacturer="ABB", apply=False)
        assert result.matched == 1


# ── Formatters (smoke) ─────────────────────────────────────────────


@pytest.mark.unit
class TestFormatters:
    def test_format_diff_table(self) -> None:
        src = _make_mock_client(
            {
                "drive": [
                    _make_drive("ABB", "00000000-0000-0000-0000-000000000001"),
                ]
            }
        )
        tgt = _make_mock_client()
        result = diff(src, tgt, "drive", "dev", "prod")
        text = format_diff_table(result)
        assert "dev" in text
        assert "prod" in text
        assert "drive" in text

    def test_format_promote_summary_dry_run_label(self, tmp_path: Path) -> None:
        src = _make_mock_client({"drive": []})
        tgt = _make_mock_client()
        bl = Blacklist(path=tmp_path / "bl.json")
        result = promote(src, tgt, "drive", bl, apply=False)
        text = format_promote_summary("Promote dev → prod", result)
        assert "DRY RUN" in text

    def test_format_purge_summary_applied_label(self) -> None:
        client = _make_mock_client({"drive": []})
        result = purge(client, product_type="drive", apply=True)
        text = format_purge_summary(result)
        assert "APPLIED" in text
