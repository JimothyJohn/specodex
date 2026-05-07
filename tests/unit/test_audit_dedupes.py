"""Tests for cli.audit_dedupes — DEDUPE Phase 1 grouping + classification.

No DynamoDB mocking — the audit logic operates on plain dicts and is
tested directly. The boto3-bound `fetch_rows_from_dynamo` shim isn't
covered here.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli import audit_dedupes


def row(
    *,
    manufacturer: str = "Parker",
    part_number: str | None = "MPP-1152C",
    product_family: str | None = "MPP",
    pk: str = "PRODUCT#motor",
    product_id: str | None = None,
    **fields,
) -> dict:
    base = {
        "PK": pk,
        "SK": f"PRODUCT#{product_id or part_number}",
        "product_id": product_id or part_number,
        "manufacturer": manufacturer,
        "part_number": part_number,
        "product_family": product_family,
    }
    base.update(fields)
    return base


class TestFamilyAwareCore:
    def test_strips_family_from_part_number(self) -> None:
        assert audit_dedupes.family_aware_core("MPP-1152C", "MPP") == "1152c"

    def test_no_strip_without_family(self) -> None:
        assert audit_dedupes.family_aware_core("MPP-1152C", None) == "mpp1152c"

    def test_no_strip_when_family_doesnt_match(self) -> None:
        assert audit_dedupes.family_aware_core("MPP-1152C", "SGM7") == "mpp1152c"

    def test_empty_part_number_returns_empty(self) -> None:
        assert audit_dedupes.family_aware_core(None, "MPP") == ""


class TestIsJunkPartNumber:
    def test_unknown_is_junk(self) -> None:
        assert audit_dedupes.is_junk_part_number("Unknown")

    def test_real_part_is_not(self) -> None:
        assert not audit_dedupes.is_junk_part_number("MPP-1152C")

    def test_none_is_junk(self) -> None:
        assert audit_dedupes.is_junk_part_number(None)


class TestGroupRows:
    def test_collapses_prefix_drift(self) -> None:
        rows = [
            row(part_number="MPP-1152C"),
            row(part_number="MPP1152C"),
            row(part_number="1152C"),
        ]
        groups = audit_dedupes.group_rows(rows)
        assert len(groups) == 1
        (((_, core), members),) = groups.items()
        assert core == "1152c"
        assert len(members) == 3

    def test_keeps_distinct_skus_separate(self) -> None:
        rows = [
            row(part_number="MPP-1152C"),
            row(part_number="MPP-2200B"),
        ]
        groups = audit_dedupes.group_rows(rows)
        assert len(groups) == 2

    def test_skips_rows_missing_manufacturer(self) -> None:
        rows = [row(manufacturer=""), row()]
        groups = audit_dedupes.group_rows(rows)
        assert len(groups) == 1

    def test_does_not_cross_manufacturers(self) -> None:
        rows = [
            row(manufacturer="Parker", part_number="MPP-1152C"),
            row(manufacturer="Yaskawa", part_number="MPP-1152C"),
        ]
        groups = audit_dedupes.group_rows(rows)
        assert len(groups) == 2


class TestClassifyField:
    def test_all_equal_is_identical(self) -> None:
        assert audit_dedupes.classify_field([10, 10, 10]) == "identical"

    def test_some_null_rest_equal_is_complementary(self) -> None:
        assert audit_dedupes.classify_field([10, None, 10]) == "complementary"

    def test_one_value_rest_null_is_complementary(self) -> None:
        assert audit_dedupes.classify_field([None, 10, None]) == "complementary"

    def test_distinct_non_null_is_conflicting(self) -> None:
        assert audit_dedupes.classify_field([10, 20]) == "conflicting"

    def test_all_null_is_identical_vacuously(self) -> None:
        assert audit_dedupes.classify_field([None, None]) == "identical"

    def test_dict_value_equality(self) -> None:
        a = {"value": 10, "unit": "Nm"}
        b = {"unit": "Nm", "value": 10}  # different key order, same dict
        assert audit_dedupes.classify_field([a, b]) == "identical"

    def test_dict_value_difference(self) -> None:
        a = {"value": 10, "unit": "Nm"}
        b = {"value": 12, "unit": "Nm"}
        assert audit_dedupes.classify_field([a, b]) == "conflicting"


class TestSuggestAction:
    def test_all_identical_or_complementary_is_merge(self) -> None:
        cls = {"rated_torque": "identical", "max_speed": "complementary"}
        assert audit_dedupes.suggest_action(cls, [row(), row()]) == "merge"

    def test_any_conflicting_is_review(self) -> None:
        cls = {"rated_torque": "identical", "max_speed": "conflicting"}
        assert audit_dedupes.suggest_action(cls, [row(), row()]) == "review"

    def test_mixed_junk_is_delete_junk(self) -> None:
        rows = [row(part_number="Unknown"), row(part_number="MPP-1152C")]
        assert audit_dedupes.suggest_action({}, rows) == "delete-junk"

    def test_all_junk_falls_through_to_merge(self) -> None:
        # All rows are junk-named — caller decides; default to merge.
        rows = [row(part_number="Unknown"), row(part_number="N/A")]
        assert audit_dedupes.suggest_action({}, rows) == "merge"


class TestAudit:
    def test_singletons_are_excluded(self) -> None:
        report = audit_dedupes.audit([row(part_number="MPP-1152C")])
        assert report == []

    def test_safe_merge_group_classified(self) -> None:
        rows = [
            row(part_number="MPP-1152C", rated_torque={"value": 10, "unit": "Nm"}),
            row(
                part_number="MPP1152C",
                rated_torque=None,
                max_speed={"value": 5000, "unit": "rpm"},
            ),
        ]
        report = audit_dedupes.audit(rows)
        assert len(report) == 1
        r = report[0]
        assert r["suggested_action"] == "merge"
        assert r["row_count"] == 2
        assert r["normalized_core"] == "1152c"
        assert r["field_classifications"]["rated_torque"] == "complementary"
        assert r["field_classifications"]["max_speed"] == "complementary"

    def test_conflicting_group_marked_review(self) -> None:
        rows = [
            row(part_number="MPP-1152C", rated_torque={"value": 10, "unit": "Nm"}),
            row(part_number="MPP1152C", rated_torque={"value": 12, "unit": "Nm"}),
        ]
        report = audit_dedupes.audit(rows)
        assert len(report) == 1
        assert report[0]["suggested_action"] == "review"
        assert report[0]["field_classifications"]["rated_torque"] == "conflicting"

    def test_family_mismatch_demotes_to_review(self) -> None:
        # Same normalized core, two different families. Even with all
        # spec fields identical, the family mismatch forces manual review.
        rows = [
            row(part_number="MPP-1152C", product_family="MPP", rated_torque=10),
            row(part_number="MPJ-1152C", product_family="MPJ", rated_torque=10),
        ]
        report = audit_dedupes.audit(rows)
        # MPP-1152C strips to "1152c", MPJ-1152C strips to "1152c" — same.
        assert len(report) == 1
        assert report[0]["family_mismatch"] is True
        assert report[0]["suggested_action"] == "review"

    def test_pages_field_excluded_from_classification(self) -> None:
        # `pages` is provenance, not spec — its diff shouldn't gate merge.
        rows = [
            row(part_number="MPP-1152C", pages=[10, 11], rated_torque=10),
            row(part_number="MPP1152C", pages=[15, 16], rated_torque=10),
        ]
        report = audit_dedupes.audit(rows)
        assert "pages" not in report[0]["field_classifications"]
        assert report[0]["suggested_action"] == "merge"


class TestRenderReviewMd:
    def test_empty_review_says_so(self) -> None:
        out = audit_dedupes.render_review_md([])
        assert "Nothing to review" in out

    def test_review_groups_only_listed(self) -> None:
        reports = [
            {
                "manufacturer": "parker",
                "normalized_core": "1152c",
                "row_count": 2,
                "rows": [
                    {
                        "part_number": "MPP-1152C",
                        "product_family": "MPP",
                        "datasheet_url": "x",
                    },
                    {
                        "part_number": "MPP1152C",
                        "product_family": "MPP",
                        "datasheet_url": "y",
                    },
                ],
                "field_classifications": {"rated_torque": "conflicting"},
                "family_mismatch": False,
                "suggested_action": "review",
            },
            {
                "manufacturer": "parker",
                "normalized_core": "2200b",
                "row_count": 2,
                "rows": [],
                "field_classifications": {"max_speed": "complementary"},
                "family_mismatch": False,
                "suggested_action": "merge",
            },
        ]
        out = audit_dedupes.render_review_md(reports)
        assert "1152c" in out
        assert "2200b" not in out  # merge groups skipped
        assert "Conflicting fields" in out

    def test_family_mismatch_warning_rendered(self) -> None:
        reports = [
            {
                "manufacturer": "parker",
                "normalized_core": "1152c",
                "row_count": 2,
                "rows": [
                    {
                        "part_number": "MPP-1152C",
                        "product_family": "MPP",
                        "datasheet_url": "",
                    },
                    {
                        "part_number": "MPJ-1152C",
                        "product_family": "MPJ",
                        "datasheet_url": "",
                    },
                ],
                "field_classifications": {},
                "family_mismatch": True,
                "suggested_action": "review",
            }
        ]
        out = audit_dedupes.render_review_md(reports)
        assert "family mismatch" in out


class TestMain:
    def test_with_rows_file_writes_artifacts(self, tmp_path: Path, monkeypatch) -> None:
        # Override OUTPUT_DIR so the test doesn't pollute repo's outputs/.
        monkeypatch.setattr(audit_dedupes, "OUTPUT_DIR", tmp_path)
        rows_file = tmp_path / "rows.json"
        rows_file.write_text(
            json.dumps(
                [
                    row(part_number="MPP-1152C", rated_torque=10),
                    row(part_number="MPP1152C", rated_torque=12),
                    row(part_number="MPP-2200B"),
                ]
            )
        )
        rc = audit_dedupes.main(["--stage", "dev", "--rows", str(rows_file), "--quiet"])
        assert rc == 0
        json_files = list(tmp_path.glob("dedupe_audit_dev_*.json"))
        md_files = list(tmp_path.glob("dedupe_review_dev_*.md"))
        assert len(json_files) == 1
        assert len(md_files) == 1
        report = json.loads(json_files[0].read_text())
        assert report["stage"] == "dev"
        assert (
            report["total_groups"] == 1
        )  # only the duplicate group, singleton excluded
        assert (
            report["groups"][0]["suggested_action"] == "review"
        )  # rated_torque conflicts

    def test_rejects_non_dev_stage(self, tmp_path: Path) -> None:
        rows_file = tmp_path / "r.json"
        rows_file.write_text("[]")
        # argparse choices=['dev'] makes this a SystemExit, not a return.
        try:
            audit_dedupes.main(["--stage", "prod", "--rows", str(rows_file)])
            raise AssertionError("expected SystemExit for --stage prod")
        except SystemExit as e:
            assert e.code != 0


# ── Phase 2 — safe-merge logic ─────────────────────────────────────────


class TestPickCanonicalPartNumber:
    def test_longest_form_wins(self) -> None:
        rows = [
            row(part_number="MPP-1152C"),
            row(part_number="MPP1152C"),
            row(part_number="1152C"),
        ]
        assert audit_dedupes.pick_canonical_part_number(rows) == "MPP-1152C"

    def test_skips_empty(self) -> None:
        rows = [row(part_number=None), row(part_number="MPP-1152C")]
        assert audit_dedupes.pick_canonical_part_number(rows) == "MPP-1152C"

    def test_returns_none_when_all_empty(self) -> None:
        rows = [row(part_number=None), row(part_number=None)]
        assert audit_dedupes.pick_canonical_part_number(rows) is None


class TestMergeSafeGroup:
    def test_takes_non_null_per_field(self) -> None:
        rows = [
            row(
                pk="PRODUCT#MOTOR",
                product_id="aaa",
                part_number="MPP-1152C",
                product_family="MPP",
                rated_torque={"value": 10, "unit": "Nm"},
                product_type="motor",
            ),
            row(
                pk="PRODUCT#MOTOR",
                product_id="bbb",
                part_number="MPP1152C",
                product_family="MPP",
                product_type="motor",
                max_speed={"value": 5000, "unit": "rpm"},
            ),
        ]
        merged = audit_dedupes.merge_safe_group(rows)
        assert merged["rated_torque"] == {"value": 10, "unit": "Nm"}
        assert merged["max_speed"] == {"value": 5000, "unit": "rpm"}
        assert merged["part_number"] == "MPP-1152C"  # longest wins
        assert merged["product_family"] == "MPP"
        assert merged["manufacturer"] == "Parker"
        assert merged["product_type"] == "motor"
        assert merged["PK"] == "PRODUCT#MOTOR"
        assert merged["SK"].startswith("PRODUCT#")
        # New family-aware product_id ≠ either source row's id.
        assert merged["product_id"] not in {"aaa", "bbb"}

    def test_unions_pages(self) -> None:
        rows = [
            row(part_number="MPP-1152C", product_type="motor", pages=[10, 11]),
            row(part_number="MPP1152C", product_type="motor", pages=[15, 11, 16]),
        ]
        merged = audit_dedupes.merge_safe_group(rows)
        assert merged["pages"] == [10, 11, 15, 16]

    def test_picks_datasheet_url_from_richest_row(self) -> None:
        rows = [
            row(
                part_number="MPP-1152C",
                product_type="motor",
                datasheet_url="https://thin.example/ds.pdf",
            ),
            row(
                part_number="MPP1152C",
                product_type="motor",
                datasheet_url="https://rich.example/ds.pdf",
                rated_torque={"value": 10, "unit": "Nm"},
                max_speed={"value": 5000, "unit": "rpm"},
                rated_power={"value": 2.5, "unit": "kW"},
            ),
        ]
        merged = audit_dedupes.merge_safe_group(rows)
        assert merged["datasheet_url"] == "https://rich.example/ds.pdf"

    def test_canonical_id_collapses_prefix_drift(self) -> None:
        # Both rows hash to the same family-aware id once `_strip_family_prefix`
        # is applied, so the merged row inherits that single canonical id.
        rows = [
            row(part_number="MPP-1152C", product_family="MPP", product_type="motor"),
            row(part_number="1152C", product_family="MPP", product_type="motor"),
        ]
        merged = audit_dedupes.merge_safe_group(rows)
        from specodex.ids import compute_product_id

        expected = compute_product_id(
            manufacturer="Parker",
            part_number="MPP-1152C",
            product_name=None,
            product_family="MPP",
        )
        assert merged["product_id"] == str(expected)


class TestPlanSafeMerges:
    def test_only_merge_action_groups_planned(self) -> None:
        rows = [
            row(part_number="MPP-1152C", product_type="motor", rated_torque=10),
            row(part_number="MPP1152C", product_type="motor"),
            # Conflict pair → review action, no plan entry.
            row(
                part_number="SGM7-100A",
                product_family="SGM7",
                product_type="motor",
                rated_torque=10,
            ),
            row(
                part_number="SGM7100A",
                product_family="SGM7",
                product_type="motor",
                rated_torque=20,
            ),
        ]
        rows_by_pksk = {(r["PK"], r["SK"]): r for r in rows}
        reports = audit_dedupes.audit(rows)
        plan = audit_dedupes.plan_safe_merges(reports, rows_by_pksk)
        assert len(plan) == 1
        assert plan[0]["group"]["normalized_core"] == "1152c"
        assert len(plan[0]["deletes"]) >= 1
        # The canonical SK must not be in the delete list.
        canonical_sk = plan[0]["merged"]["SK"]
        assert all(sk != canonical_sk for _, sk in plan[0]["deletes"])

    def test_skips_group_with_unresolvable_rows(self) -> None:
        rows = [
            row(part_number="MPP-1152C", product_type="motor"),
            row(part_number="MPP1152C", product_type="motor"),
        ]
        reports = audit_dedupes.audit(rows)
        # Empty rows_by_pksk → audit knows about the group but plan can't
        # resolve the rows; it skips rather than half-merging.
        plan = audit_dedupes.plan_safe_merges(reports, {})
        assert plan == []


class TestApplyPlan:
    def test_executes_puts_and_deletes(self) -> None:
        plan = [
            {
                "group": {"manufacturer": "parker", "normalized_core": "1152c"},
                "merged": {"PK": "PRODUCT#MOTOR", "SK": "PRODUCT#new", "x": 1},
                "deletes": [
                    ("PRODUCT#MOTOR", "PRODUCT#old1"),
                    ("PRODUCT#MOTOR", "PRODUCT#old2"),
                ],
            }
        ]
        puts: list[dict] = []
        deletes: list[tuple[str, str]] = []
        tally = audit_dedupes.apply_plan(
            plan, puts.append, lambda pk, sk: deletes.append((pk, sk))
        )
        assert tally == {"groups": 1, "puts": 1, "deletes": 2}
        assert puts == [{"PK": "PRODUCT#MOTOR", "SK": "PRODUCT#new", "x": 1}]
        assert deletes == [
            ("PRODUCT#MOTOR", "PRODUCT#old1"),
            ("PRODUCT#MOTOR", "PRODUCT#old2"),
        ]


class TestApplyCli:
    def test_apply_requires_safe_only(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(audit_dedupes, "OUTPUT_DIR", tmp_path)
        rows_file = tmp_path / "rows.json"
        rows_file.write_text("[]")
        rc = audit_dedupes.main(
            ["--stage", "dev", "--rows", str(rows_file), "--apply", "--quiet"]
        )
        assert rc == 2

    def test_apply_refuses_with_rows_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(audit_dedupes, "OUTPUT_DIR", tmp_path)
        rows_file = tmp_path / "rows.json"
        rows_file.write_text("[]")
        rc = audit_dedupes.main(
            [
                "--stage",
                "dev",
                "--rows",
                str(rows_file),
                "--apply",
                "--safe-only",
                "--dry-run",
                "--quiet",
            ]
        )
        # Even with safe-only + dry-run, --rows is incompatible with --apply.
        assert rc == 2

    def test_apply_requires_yes_or_dry_run(self, tmp_path: Path, monkeypatch) -> None:
        # Skip the DB scan path by stubbing fetch_rows_from_dynamo.
        monkeypatch.setattr(audit_dedupes, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(audit_dedupes, "fetch_rows_from_dynamo", lambda _t: [])
        rc = audit_dedupes.main(["--stage", "dev", "--apply", "--safe-only", "--quiet"])
        assert rc == 2

    def test_dry_run_writes_plan_and_returns_zero(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(audit_dedupes, "OUTPUT_DIR", tmp_path)
        rows = [
            row(part_number="MPP-1152C", product_type="motor", rated_torque=10),
            row(part_number="MPP1152C", product_type="motor"),
        ]
        monkeypatch.setattr(audit_dedupes, "fetch_rows_from_dynamo", lambda _t: rows)
        rc = audit_dedupes.main(
            ["--stage", "dev", "--apply", "--safe-only", "--dry-run", "--quiet"]
        )
        assert rc == 0
        plan_files = list(tmp_path.glob("dedupe_plan_dev_*.md"))
        assert len(plan_files) == 1
        text = plan_files[0].read_text()
        assert "DEDUPE Phase 2" in text
        assert "1152c" in text
