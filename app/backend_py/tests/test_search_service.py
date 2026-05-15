"""Pure-function tests for the search service.

Mirrors ``app/backend/tests/search.service.test.ts``. The
orchestrator gets an integration-shaped test via the HTTP route
in test_search_route.py; this file pins the helpers against the
explicit cases that surfaced bugs during the TS port.
"""

from __future__ import annotations

import pytest

from app.backend_py.src.services.search import (
    SearchParams,
    apply_where,
    extract_numeric,
    parse_sort,
    parse_where,
    product_summary,
    search_products,
    sort_products,
    text_score,
)
from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.motor import Motor


def _motor(**overrides) -> Motor:
    defaults = {
        "product_name": "Test Motor",
        "manufacturer": "MfgA",
        "product_type": "motor",
        "part_number": "MTR-001",
    }
    defaults.update(overrides)
    return Motor(**defaults)


class TestExtractNumeric:
    def test_int_returns_float(self) -> None:
        assert extract_numeric(42) == 42.0

    def test_float_returns_self(self) -> None:
        assert extract_numeric(3.14) == 3.14

    def test_numeric_string_parses(self) -> None:
        assert extract_numeric("42.5") == 42.5

    def test_non_numeric_string_returns_none(self) -> None:
        assert extract_numeric("not a number") is None

    def test_none_returns_none(self) -> None:
        assert extract_numeric(None) is None

    def test_value_unit_returns_value(self) -> None:
        assert extract_numeric(ValueUnit(value=100, unit="V")) == 100.0

    def test_min_max_unit_returns_min(self) -> None:
        assert extract_numeric(MinMaxUnit(min=200, max=240, unit="V")) == 200.0

    def test_bool_returns_none(self) -> None:
        """isinstance(True, int) is True; we should NOT treat that
        as a numeric value to compare against.
        """

        assert extract_numeric(True) is None
        assert extract_numeric(False) is None


class TestTextScore:
    def test_exact_part_number_match_scores_100(self) -> None:
        m = _motor(part_number="MTR-001")
        assert text_score(m, "MTR-001") == 100

    def test_contains_part_number_scores_80(self) -> None:
        m = _motor(part_number="MTR-001")
        assert text_score(m, "MTR") == 80

    def test_no_match_scores_zero(self) -> None:
        m = _motor(part_number="MTR-001")
        assert text_score(m, "nothing") == 0

    def test_case_insensitive(self) -> None:
        m = _motor(part_number="MTR-001")
        assert text_score(m, "mtr-001") == 100


class TestParseWhere:
    def test_gte(self) -> None:
        clause = parse_where("rated_power>=100")
        assert clause.field == "rated_power"
        assert clause.op == ">="
        assert clause.value == "100"

    def test_lt(self) -> None:
        clause = parse_where("rated_power<100")
        assert clause.op == "<"

    def test_eq(self) -> None:
        clause = parse_where("manufacturer=ABB")
        assert clause.op == "="
        assert clause.value == "ABB"

    def test_neq(self) -> None:
        clause = parse_where("type!=brushless dc")
        assert clause.op == "!="
        assert clause.value == "brushless dc"

    def test_unparseable_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_where("nojunkhere")


class TestParseSort:
    def test_default_ascending(self) -> None:
        s = parse_sort("rated_power")
        assert s.field == "rated_power"
        assert s.reverse is False

    def test_desc(self) -> None:
        s = parse_sort("rated_power:desc")
        assert s.reverse is True

    def test_asc_explicit(self) -> None:
        s = parse_sort("rated_power:asc")
        assert s.reverse is False


class TestApplyWhere:
    def test_numeric_gte(self) -> None:
        m = _motor(rated_power="100;W")
        assert apply_where(m, "rated_power", ">=", "50") is True
        assert apply_where(m, "rated_power", ">=", "200") is False

    def test_string_eq_substring(self) -> None:
        m = _motor(manufacturer="ABB Industrial")
        # `=` on strings is case-insensitive substring per Express.
        assert apply_where(m, "manufacturer", "=", "abb") is True
        assert apply_where(m, "manufacturer", "=", "siemens") is False

    def test_missing_field_returns_false(self) -> None:
        m = _motor()
        assert apply_where(m, "no_such_field", ">", "0") is False


class TestSortProducts:
    def test_sort_by_power_asc(self) -> None:
        m1 = _motor(part_number="A", rated_power="100;W")
        m2 = _motor(part_number="B", rated_power="50;W")
        m3 = _motor(part_number="C", rated_power="200;W")
        out = sort_products([m1, m2, m3], ["rated_power"])
        assert [p.part_number for p in out] == ["B", "A", "C"]

    def test_sort_desc(self) -> None:
        m1 = _motor(part_number="A", rated_power="100;W")
        m2 = _motor(part_number="B", rated_power="50;W")
        out = sort_products([m1, m2], ["rated_power:desc"])
        assert [p.part_number for p in out] == ["A", "B"]

    def test_nulls_sort_last(self) -> None:
        m1 = _motor(part_number="A", rated_power="100;W")
        m2 = _motor(part_number="B")  # no rated_power
        out = sort_products([m1, m2], ["rated_power"])
        # m1 has a value, m2 doesn't — m2 must be last regardless of dir.
        assert out[-1].part_number == "B"

        out_desc = sort_products([m1, m2], ["rated_power:desc"])
        assert out_desc[-1].part_number == "B"


class TestProductSummary:
    def test_includes_summary_specs(self) -> None:
        m = _motor(
            rated_power="100;W",
            rated_voltage="240;V",
            rated_torque="2.5;Nm",
        )
        summary = product_summary(m)
        assert summary["product_type"] == "motor"
        assert summary["manufacturer"] == "MfgA"
        # Per-type specs land in summary.
        assert summary["rated_power"] == {"value": 100.0, "unit": "W"}

    def test_relevance_only_included_when_positive(self) -> None:
        m = _motor()
        assert "relevance" not in product_summary(m, relevance=0)
        assert product_summary(m, relevance=42)["relevance"] == 42


class TestSearchOrchestrator:
    def test_manufacturer_filter_is_substring(self) -> None:
        a = _motor(part_number="A", manufacturer="ABB")
        b = _motor(part_number="B", manufacturer="Siemens")
        result = search_products(SearchParams(products=[a, b], manufacturer="abb"))
        assert result.count == 1
        assert result.products[0]["part_number"] == "A"

    def test_where_clause_filters(self) -> None:
        a = _motor(part_number="A", rated_power="100;W")
        b = _motor(part_number="B", rated_power="50;W")
        result = search_products(
            SearchParams(products=[a, b], where=["rated_power>=75"])
        )
        assert result.count == 1
        assert result.products[0]["part_number"] == "A"

    def test_query_filters_and_scores(self) -> None:
        a = _motor(part_number="MTR-001", product_name="Real Motor")
        b = _motor(part_number="X-NOTHING", product_name="Other Thing")
        result = search_products(SearchParams(products=[a, b], query="MTR"))
        assert result.count == 1
        assert result.products[0]["part_number"] == "MTR-001"
        # Query results include a positive relevance score.
        assert result.products[0]["relevance"] > 0

    def test_no_query_no_score(self) -> None:
        m = _motor()
        result = search_products(SearchParams(products=[m]))
        assert "relevance" not in result.products[0]

    def test_limit_clamps_to_100(self) -> None:
        motors = [_motor(part_number=f"M-{i}") for i in range(200)]
        result = search_products(SearchParams(products=motors, limit=500))
        # limit > 100 is clamped to 100 (Express ``Math.min(limit, 100)``).
        assert result.count == 100

    def test_limit_floors_at_1(self) -> None:
        motors = [_motor()]
        result = search_products(SearchParams(products=motors, limit=0))
        assert result.count == 1
