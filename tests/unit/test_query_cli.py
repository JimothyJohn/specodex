"""Tests for the query CLI (cli/query.py).

All tests are offline — DynamoDB is mocked.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cli.query import (
    apply_where,
    build_parser,
    extract_numeric,
    parse_where,
    product_summary,
    text_score,
    _field_type_from_annotation,
    QUERYABLE_TYPES,
    SUMMARY_SPECS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MFG = "Maxon"


def _make_motor(**overrides):
    from specodex.models.motor import Motor

    defaults = {
        "product_name": "EC-45 flat",
        "manufacturer": MFG,
        "part_number": "339286",
        "rated_power": "150;W",
        "rated_voltage": "24;V",
        "rated_current": "6.5;A",
        "rated_speed": "3000;rpm",
        "rated_torque": "0.47;Nm",
        "peak_torque": "1.2;Nm",
        "type": "brushless dc",
    }
    defaults.update(overrides)
    return Motor(**defaults)


def _make_drive(**overrides):
    from specodex.models.drive import Drive

    defaults = {
        "product_name": "EPOS4",
        "manufacturer": MFG,
        "part_number": "607160",
        "rated_power": "500;W",
        "input_voltage": "10-50;VDC",
        "rated_current": "15;A",
        "peak_current": "30;A",
    }
    defaults.update(overrides)
    return Drive(**defaults)


def _make_args(**overrides) -> SimpleNamespace:
    defaults = {
        "command": "search",
        "query": "test",
        "type": None,
        "limit": 20,
        "manufacturer": None,
        "family": None,
        "where": None,
        "product_id": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParser:
    def test_search_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["search", "EC-45"])
        assert args.command == "search"
        assert args.query == "EC-45"

    def test_search_with_type(self):
        parser = build_parser()
        args = parser.parse_args(["search", "Maxon", "-t", "motor"])
        assert args.type == "motor"

    def test_search_with_limit(self):
        parser = build_parser()
        args = parser.parse_args(["search", "test", "-n", "5"])
        assert args.limit == 5

    def test_list_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"
        assert args.type is None
        assert args.manufacturer is None
        assert args.limit == 10

    def test_list_with_filters(self):
        parser = build_parser()
        args = parser.parse_args(
            ["list", "-t", "motor", "-m", "Maxon", "-f", "EC", "-n", "10"]
        )
        assert args.type == "motor"
        assert args.manufacturer == "Maxon"
        assert args.family == "EC"
        assert args.limit == 10

    def test_get_requires_type(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["get", "abc-123"])

    def test_get_with_type(self):
        parser = build_parser()
        args = parser.parse_args(["get", "abc-123", "-t", "motor"])
        assert args.product_id == "abc-123"
        assert args.type == "motor"

    def test_filter_requires_type(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["filter"])

    def test_filter_with_where(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "filter",
                "-t",
                "motor",
                "-w",
                "rated_power>100",
                "-w",
                "rated_voltage<=48",
            ]
        )
        assert args.type == "motor"
        assert args.where == ["rated_power>100", "rated_voltage<=48"]

    def test_types_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["types"])
        assert args.command == "types"

    def test_manufacturers_with_type(self):
        parser = build_parser()
        args = parser.parse_args(["manufacturers", "-t", "drive"])
        assert args.type == "drive"

    def test_fields_requires_type(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["fields"])

    def test_fields_with_type(self):
        parser = build_parser()
        args = parser.parse_args(["fields", "-t", "gearhead"])
        assert args.type == "gearhead"


# ---------------------------------------------------------------------------
# extract_numeric
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractNumeric:
    def test_none(self):
        assert extract_numeric(None) is None

    def test_int(self):
        assert extract_numeric(42) == 42.0

    def test_float(self):
        assert extract_numeric(3.14) == 3.14

    def test_value_unit(self):
        assert extract_numeric("24;V") == 24.0

    def test_value_unit_decimal(self):
        assert extract_numeric("0.47;Nm") == 0.47

    def test_min_max_unit(self):
        assert extract_numeric("20-40;C") == 20.0

    def test_negative_value_unit(self):
        assert extract_numeric("-10;C") == -10.0

    def test_plain_string(self):
        assert extract_numeric("brushless dc") is None

    def test_empty_string(self):
        assert extract_numeric("") is None

    def test_decimal_type(self):
        from decimal import Decimal

        assert extract_numeric(Decimal("24.5")) == 24.5

    def test_plain_numeric_string(self):
        assert extract_numeric("100") == 100.0


@pytest.mark.unit
class TestExtractNumericEdgeCases:
    """Regression cases for the 2026-09-22 Hypothesis round.

    ``extract_numeric`` is annotated ``-> float | None`` and both callers
    (`sort_products`'s comparator, `apply_where`) treat None as "not a
    number" — but it used to *raise* on several inputs a DynamoDB row can
    carry. The property companion
    (``test_query_property.py``) pins the general contract; these pin the
    exact shapes so they can't regress if the strategy drifts.
    """

    def test_multi_dot_spec_string_does_not_raise(self):
        """``[\\d.]+`` matches "1.2.3"; ``float()`` refuses it. Used to
        raise ValueError out of `dsm list --sort` / `dsm filter --where`."""
        assert extract_numeric("1.2.3;x") is None

    def test_bare_dots_spec_string_does_not_raise(self):
        assert extract_numeric("..;x") is None
        assert extract_numeric(".;x") is None

    def test_trailing_dot_spec_string_still_parses(self):
        """The fix must not swallow values `float()` does accept."""
        assert extract_numeric("1.;x") == 1.0

    def test_signalling_decimal_does_not_raise(self):
        from decimal import Decimal

        assert extract_numeric(Decimal("sNaN")) is None

    def test_oversized_int_does_not_raise(self):
        """``float(10**400)`` raises OverflowError."""
        assert extract_numeric(10**400) is None

    def test_non_finite_collapses_to_none(self):
        """NaN makes `cmp_to_key` non-transitive and compares False for
        every `apply_where` operator except `!=`. None routes into the
        well-defined "sorts last" / string-compare branches instead."""
        assert extract_numeric(float("nan")) is None
        assert extract_numeric(float("inf")) is None
        assert extract_numeric(float("-inf")) is None

    def test_bool_is_not_numeric(self):
        """``bool`` is an ``int`` subclass — ``float(True) == 1.0``."""
        assert extract_numeric(True) is None
        assert extract_numeric(False) is None

    def test_sort_survives_a_malformed_row(self):
        """End-to-end: one unusable spec string used to take down the
        whole `dsm list --sort <field>` command."""
        from cli.query import sort_products

        rows = [
            SimpleNamespace(rated_power="1.2.3;x"),
            SimpleNamespace(rated_power="5;W"),
        ]
        assert len(sort_products(rows, ["rated_power:desc"])) == 2

    def test_filter_survives_a_malformed_row(self):
        product = SimpleNamespace(rated_power="1.2.3;x")
        assert apply_where(product, "rated_power", ">=", "10") is False


# ---------------------------------------------------------------------------
# text_score
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTextScore:
    def test_exact_part_number(self):
        motor = _make_motor()
        assert text_score(motor, "339286") == 100

    def test_exact_manufacturer(self):
        motor = _make_motor()
        assert text_score(motor, "maxon") == 85

    def test_contains_product_name(self):
        motor = _make_motor()
        score = text_score(motor, "ec-45")
        assert score >= 70

    def test_no_match(self):
        motor = _make_motor()
        assert text_score(motor, "nonexistent") == 0

    def test_case_insensitive(self):
        motor = _make_motor()
        assert text_score(motor, "MAXON") == 85

    def test_partial_part_number(self):
        motor = _make_motor()
        score = text_score(motor, "3392")
        assert score == 80

    def test_type_match(self):
        motor = _make_motor()
        score = text_score(motor, "brushless")
        assert score > 0

    def test_series_match(self):
        motor = _make_motor(series="EC-max")
        score = text_score(motor, "ec-max")
        assert score >= 40

    def test_family_match(self):
        motor = _make_motor(product_family="EC flat")
        score = text_score(motor, "ec flat")
        assert score >= 40


# ---------------------------------------------------------------------------
# parse_where
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseWhere:
    def test_greater_than(self):
        assert parse_where("rated_power>100") == ("rated_power", ">", "100")

    def test_less_than(self):
        assert parse_where("rated_voltage<48") == ("rated_voltage", "<", "48")

    def test_greater_equal(self):
        assert parse_where("rated_power>=100") == ("rated_power", ">=", "100")

    def test_less_equal(self):
        assert parse_where("rated_voltage<=48") == ("rated_voltage", "<=", "48")

    def test_equals(self):
        assert parse_where("manufacturer=Maxon") == ("manufacturer", "=", "Maxon")

    def test_not_equals(self):
        assert parse_where("type!=brushed") == ("type", "!=", "brushed")

    def test_spaces(self):
        assert parse_where("rated_power > 100") == ("rated_power", ">", "100")

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_where("no_operator_here")


# ---------------------------------------------------------------------------
# apply_where
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyWhere:
    def test_numeric_gt_true(self):
        motor = _make_motor(rated_power="150;W")
        assert apply_where(motor, "rated_power", ">", "100") is True

    def test_numeric_gt_false(self):
        motor = _make_motor(rated_power="50;W")
        assert apply_where(motor, "rated_power", ">", "100") is False

    def test_numeric_lt(self):
        motor = _make_motor(rated_voltage="24;V")
        assert apply_where(motor, "rated_voltage", "<", "48") is True

    def test_numeric_gte(self):
        motor = _make_motor(rated_power="100;W")
        assert apply_where(motor, "rated_power", ">=", "100") is True

    def test_numeric_lte(self):
        motor = _make_motor(rated_power="100;W")
        assert apply_where(motor, "rated_power", "<=", "100") is True

    def test_numeric_eq(self):
        motor = _make_motor(rated_power="150;W")
        assert apply_where(motor, "rated_power", "=", "150") is True

    def test_numeric_neq(self):
        motor = _make_motor(rated_power="150;W")
        assert apply_where(motor, "rated_power", "!=", "100") is True

    def test_string_eq_substring(self):
        motor = _make_motor(type="brushless dc")
        assert apply_where(motor, "type", "=", "brushless") is True

    def test_string_neq(self):
        motor = _make_motor(type="brushless dc")
        assert apply_where(motor, "type", "!=", "brushed dc") is True

    def test_none_field_returns_false(self):
        motor = _make_motor(series=None)
        assert apply_where(motor, "series", "=", "test") is False

    def test_int_field(self):
        motor = _make_motor(poles=8)
        assert apply_where(motor, "poles", ">", "4") is True

    def test_min_max_unit(self):
        motor = _make_motor(rated_voltage="20-40;V")
        # Extracts min value (20) for comparison
        assert apply_where(motor, "rated_voltage", ">=", "20") is True
        assert apply_where(motor, "rated_voltage", ">", "25") is False


# ---------------------------------------------------------------------------
# product_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProductSummary:
    def test_motor_summary(self):
        motor = _make_motor()
        summary = product_summary(motor)
        assert summary["manufacturer"] == MFG
        assert summary["product_name"] == "EC-45 flat"
        assert summary["part_number"] == "339286"
        assert summary["product_type"] == "motor"
        assert len(summary["id"]) == 8
        # Specs are flat (not nested)
        assert "rated_power" in summary

    def test_drive_summary(self):
        drive = _make_drive()
        summary = product_summary(drive)
        assert summary["product_type"] == "drive"
        assert "rated_power" in summary

    def test_summary_excludes_none_specs(self):
        motor = _make_motor(peak_torque=None)
        summary = product_summary(motor)
        assert "peak_torque" not in summary

    def test_omit_type(self):
        motor = _make_motor()
        summary = product_summary(motor, omit_type=True)
        assert "product_type" not in summary
        assert "rated_power" in summary


# ---------------------------------------------------------------------------
# _field_type_from_annotation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFieldTypeFromAnnotation:
    def test_min_max_unit(self):
        ftype, hint = _field_type_from_annotation("Optional[MinMaxUnit]")
        assert ftype == "range"

    def test_value_unit(self):
        ftype, hint = _field_type_from_annotation("Optional[ValueUnit]")
        assert ftype == "numeric"

    def test_list(self):
        ftype, hint = _field_type_from_annotation("Optional[List[str]]")
        assert ftype == "list"

    def test_int(self):
        ftype, hint = _field_type_from_annotation("Optional[int]")
        assert ftype == "int"

    def test_float(self):
        ftype, hint = _field_type_from_annotation("Optional[float]")
        assert ftype == "float"

    def test_string(self):
        ftype, hint = _field_type_from_annotation("Optional[str]")
        assert ftype == "string"


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConstants:
    def test_queryable_types(self):
        assert "motor" in QUERYABLE_TYPES
        assert "drive" in QUERYABLE_TYPES
        assert "gearhead" in QUERYABLE_TYPES
        assert "robot_arm" in QUERYABLE_TYPES
        assert "datasheet" not in QUERYABLE_TYPES

    def test_summary_specs_covers_queryable(self):
        for ptype in QUERYABLE_TYPES:
            assert ptype in SUMMARY_SPECS
            assert len(SUMMARY_SPECS[ptype]) > 0


# ---------------------------------------------------------------------------
# cmd_fields (integration, no DB needed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCmdFields:
    def test_motor_fields_compact(self, capsys):
        from cli.query import cmd_fields

        args = _make_args(command="fields", type="motor", verbose=False)
        with pytest.raises(SystemExit) as exc:
            cmd_fields(args)
        assert exc.value.code == 0

        data = json.loads(capsys.readouterr().out)
        assert data["product_type"] == "motor"

        by_name = {f["name"]: f for f in data["fields"]}
        assert "rated_voltage" in by_name
        assert by_name["rated_voltage"]["type"] == "range"
        assert by_name["rated_power"]["type"] == "numeric"
        assert by_name["poles"]["type"] == "int"
        # Compact mode: no hint or description
        assert "hint" not in by_name["rated_voltage"]
        assert "description" not in by_name["rated_voltage"]

    def test_motor_fields_verbose(self, capsys):
        from cli.query import cmd_fields

        args = _make_args(command="fields", type="motor", verbose=True)
        with pytest.raises(SystemExit) as exc:
            cmd_fields(args)
        assert exc.value.code == 0

        data = json.loads(capsys.readouterr().out)
        by_name = {f["name"]: f for f in data["fields"]}
        assert "hint" in by_name["rated_voltage"]

    def test_drive_fields(self, capsys):
        from cli.query import cmd_fields

        args = _make_args(command="fields", type="drive", verbose=False)
        with pytest.raises(SystemExit) as exc:
            cmd_fields(args)
        assert exc.value.code == 0

        data = json.loads(capsys.readouterr().out)
        field_names = [f["name"] for f in data["fields"]]
        assert "input_voltage" in field_names
        assert "fieldbus" in field_names

    def test_gearhead_fields(self, capsys):
        from cli.query import cmd_fields

        args = _make_args(command="fields", type="gearhead", verbose=False)
        with pytest.raises(SystemExit) as exc:
            cmd_fields(args)
        assert exc.value.code == 0

        data = json.loads(capsys.readouterr().out)
        field_names = [f["name"] for f in data["fields"]]
        assert "gear_ratio" in field_names
        assert "backlash" in field_names


# ---------------------------------------------------------------------------
# parse_sort
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseSort:
    def test_desc(self):
        from cli.query import parse_sort

        field, reverse = parse_sort("rated_power:desc")
        assert field == "rated_power"
        assert reverse is True

    def test_asc(self):
        from cli.query import parse_sort

        field, reverse = parse_sort("rated_torque:asc")
        assert field == "rated_torque"
        assert reverse is False

    def test_no_direction_defaults_asc(self):
        from cli.query import parse_sort

        field, reverse = parse_sort("rated_voltage")
        assert field == "rated_voltage"
        assert reverse is False

    def test_uppercase_direction(self):
        from cli.query import parse_sort

        field, reverse = parse_sort("weight:DESC")
        assert field == "weight"
        assert reverse is True


# ---------------------------------------------------------------------------
# sort_products
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSortProducts:
    def test_sort_numeric_asc(self):
        from cli.query import sort_products

        motors = [
            _make_motor(rated_power="200;W", product_name="Big"),
            _make_motor(rated_power="50;W", product_name="Small"),
            _make_motor(rated_power="100;W", product_name="Mid"),
        ]
        result = sort_products(motors, ["rated_power:asc"])
        powers = [extract_numeric(p.rated_power) for p in result]
        assert powers == [50.0, 100.0, 200.0]

    def test_sort_numeric_desc(self):
        from cli.query import sort_products

        motors = [
            _make_motor(rated_power="50;W", product_name="Small"),
            _make_motor(rated_power="200;W", product_name="Big"),
            _make_motor(rated_power="100;W", product_name="Mid"),
        ]
        result = sort_products(motors, ["rated_power:desc"])
        powers = [extract_numeric(p.rated_power) for p in result]
        assert powers == [200.0, 100.0, 50.0]

    def test_sort_string_asc(self):
        from cli.query import sort_products

        motors = [
            _make_motor(manufacturer="Maxon", product_name="A"),
            _make_motor(manufacturer="ABB", product_name="B"),
            _make_motor(manufacturer="Faulhaber", product_name="C"),
        ]
        result = sort_products(motors, ["manufacturer:asc"])
        mfgs = [p.manufacturer for p in result]
        assert mfgs == ["ABB", "Faulhaber", "Maxon"]

    def test_sort_none_values_last(self):
        from cli.query import sort_products

        motors = [
            _make_motor(rated_power=None, product_name="NoSpec"),
            _make_motor(rated_power="50;W", product_name="HasSpec"),
        ]
        result = sort_products(motors, ["rated_power:asc"])
        assert result[0].product_name == "HasSpec"
        assert result[1].product_name == "NoSpec"

    def test_multi_level_sort(self):
        from cli.query import sort_products

        motors = [
            _make_motor(manufacturer="ABB", rated_power="100;W", product_name="A"),
            _make_motor(manufacturer="ABB", rated_power="200;W", product_name="B"),
            _make_motor(manufacturer="Maxon", rated_power="50;W", product_name="C"),
        ]
        result = sort_products(motors, ["manufacturer:asc", "rated_power:desc"])
        names = [p.product_name for p in result]
        assert names == ["B", "A", "C"]

    def test_empty_sort_keys_returns_original(self):
        from cli.query import sort_products

        motors = [_make_motor(product_name="X")]
        result = sort_products(motors, [])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Parser — find subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindParser:
    def test_find_requires_type(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["find"])

    def test_find_with_type(self):
        parser = build_parser()
        args = parser.parse_args(["find", "-t", "motor"])
        assert args.command == "find"
        assert args.type == "motor"
        assert args.query is None

    def test_find_with_query(self):
        parser = build_parser()
        args = parser.parse_args(["find", "-t", "motor", "EC-45"])
        assert args.query == "EC-45"

    def test_find_with_where_and_sort(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "find",
                "-t",
                "motor",
                "-w",
                "rated_power>100",
                "-s",
                "rated_torque:desc",
                "-s",
                "weight:asc",
            ]
        )
        assert args.where == ["rated_power>100"]
        assert args.sort == ["rated_torque:desc", "weight:asc"]

    def test_find_with_manufacturer(self):
        parser = build_parser()
        args = parser.parse_args(["find", "-t", "motor", "-m", "Maxon"])
        assert args.manufacturer == "Maxon"

    def test_find_with_limit(self):
        parser = build_parser()
        args = parser.parse_args(["find", "-t", "motor", "-n", "5"])
        assert args.limit == 5


# ---------------------------------------------------------------------------
# Parser — sort flag on list and filter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSortFlagParser:
    def test_list_with_sort(self):
        parser = build_parser()
        args = parser.parse_args(["list", "-t", "motor", "-s", "rated_power:desc"])
        assert args.sort == ["rated_power:desc"]

    def test_filter_with_sort(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "filter",
                "-t",
                "motor",
                "-w",
                "rated_voltage>=24",
                "-s",
                "rated_torque:desc",
            ]
        )
        assert args.sort == ["rated_torque:desc"]
        assert args.where == ["rated_voltage>=24"]

    def test_list_multi_sort(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "list",
                "-t",
                "motor",
                "-s",
                "manufacturer:asc",
                "-s",
                "rated_power:desc",
            ]
        )
        assert args.sort == ["manufacturer:asc", "rated_power:desc"]


# ---------------------------------------------------------------------------
# Constants — electric_cylinder
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestElectricCylinder:
    def test_electric_cylinder_in_queryable_types(self):
        assert "electric_cylinder" in QUERYABLE_TYPES

    def test_electric_cylinder_has_summary_specs(self):
        assert "electric_cylinder" in SUMMARY_SPECS
        assert len(SUMMARY_SPECS["electric_cylinder"]) > 0
