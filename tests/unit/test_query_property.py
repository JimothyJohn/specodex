"""Property tests for ``cli/query.py``'s pure query surface.

The example-based companion (``test_query_cli.py``) pins the happy path
for each helper. This file generates *adversarial* inputs — pathological
floats (NaN, ±inf, subnormals, huge magnitudes), oversized ints,
signalling Decimals, malformed ``"<number>;<unit>"`` spec strings,
unicode-laced text, and operator-laden where/sort expressions — and
asserts the documented contract holds for everything the strategy
produces.

Why this surface. ``extract_numeric`` sits under both of the CLI's
user-facing query paths: ``sort_products``'s ``cmp_to_key`` comparator
(``dsm list --sort <field>``) and ``apply_where`` (``dsm filter --where
"<field><op><value>"``). Both walk *arbitrary* field values straight out
of DynamoDB, so a raise on one unusable value takes down the whole
command rather than skipping that row. It is the CLI twin of the MCP
``where``/``sort`` validation that ``test_mcp_api_property.py`` already
pins.

**Bug this round surfaced (fixed in the same commit).**
``extract_numeric`` is annotated ``-> float | None`` and its callers
treat None as "not a number", but it raised ``ValueError`` on any string
containing ``;`` whose leading ``[\\d.]+`` run isn't a valid float —
``"1.2.3;x"``, ``"..;x"``, ``".;x"`` — because ``[\\d.]+`` matches
multi-dot runs that ``float()`` then refuses. A single such spec string
in the DB crashed ``dsm list --sort <field>`` and ``dsm filter --where``
outright. Two smaller crashes on the same path: ``float(Decimal("sNaN"))``
raises ``ValueError``, and ``float(10**400)`` raises ``OverflowError``.
Fix: ``_finite_float`` makes every conversion total, and non-finite
results collapse to None instead of poisoning the comparator (NaN makes
``cmp_to_key`` non-transitive) and the filter (NaN compares False for
every operator except ``!=``, which is True).

**Contracts under test:**

1. ``extract_numeric`` never raises, for any input.
2. Its return value is ``None`` or a *finite* ``float``.
3. ``bool`` is never numeric (``True`` must not become ``1.0``).
4. ``parse_where`` either returns a ``(str, str, str)`` triple whose
   operator is one of the six documented ones, or raises ``ValueError``
   — nothing else.
5. ``parse_sort`` is total and returns ``(str, bool)``.
6. ``apply_where`` is total and returns a ``bool``.
7. ``sort_products`` is total, and is a permutation of its input.
8. ``text_score`` is total and returns a non-negative ``int``.
"""

from __future__ import annotations

import math
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from cli.query import (
    apply_where,
    extract_numeric,
    parse_sort,
    parse_where,
    sort_products,
    text_score,
)

WHERE_OPERATORS = {">=", "<=", "!=", ">", "<", "="}

# Adversarial scalars: the shapes a DynamoDB row can actually carry
# (Decimal, str, int, float, None, bool) plus the pathological corners
# of each.
_PATHOLOGICAL_FLOATS = st.sampled_from(
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        0.0,
        -0.0,
        5e-324,  # smallest subnormal
        1.7976931348623157e308,  # float max
        -1.7976931348623157e308,
    ]
)

_PATHOLOGICAL_DECIMALS = st.sampled_from(
    [
        Decimal("0"),
        Decimal("24.5"),
        Decimal("-10"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("1e10000"),
    ]
)

# Strings that look numeric enough to reach the conversion paths but
# aren't — including the ";"-delimited spec shape the DB stores.
_ADVERSARIAL_STRINGS = st.sampled_from(
    [
        "",
        " ",
        ";",
        "..",
        ".",
        "-",
        "1.2.3",
        "1.2.3;x",
        "..;x",
        ".;x",
        "-;x",
        "-.-;x",
        "5;10",
        "24;V",
        "20-40;C",
        "1_0",
        "nan",
        "inf",
        "-inf",
        "1e400",
        "brushless dc",
        "Ω;µm",
        "\u0000;x",
    ]
)

adversarial_values = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    # Ints well outside float range — float() raises OverflowError here.
    st.sampled_from([10**400, -(10**400)]),
    _PATHOLOGICAL_FLOATS,
    st.floats(allow_nan=True, allow_infinity=True),
    _PATHOLOGICAL_DECIMALS,
    _ADVERSARIAL_STRINGS,
    st.text(max_size=20),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=3),
)


# ---------------------------------------------------------------------------
# extract_numeric — the load-bearing coercer
# ---------------------------------------------------------------------------


class TestExtractNumericProperty:
    @settings(max_examples=300)
    @given(value=adversarial_values)
    def test_never_raises_and_shape_is_documented(self, value: Any) -> None:
        """Contracts 1 + 2: total, and returns None or a finite float."""
        out = extract_numeric(value)
        assert out is None or isinstance(out, float)
        if isinstance(out, float):
            assert math.isfinite(out), f"{value!r} produced non-finite {out!r}"

    @settings(max_examples=50)
    @given(value=st.booleans())
    def test_bool_is_never_numeric(self, value: bool) -> None:
        """Contract 3. ``bool`` is an ``int`` subclass; ``float(True) == 1.0``
        would silently turn a flag field into the number 1."""
        assert extract_numeric(value) is None

    @settings(max_examples=200)
    @given(
        head=st.sampled_from(["1.2.3", "..", ".", "-", "-.-", "1.2.3.4"]),
        tail=st.text(max_size=8),
    )
    def test_malformed_spec_string_returns_none(self, head: str, tail: str) -> None:
        """Regression for this round's bug: a ``;``-delimited spec string
        whose leading ``[\\d.]+`` run isn't a float must coerce to None,
        not raise."""
        assert extract_numeric(f"{head};{tail}") is None

    @settings(max_examples=200)
    @given(value=st.floats(allow_nan=False, allow_infinity=False, width=32))
    def test_finite_floats_round_trip(self, value: float) -> None:
        """Finite numbers pass through unchanged — the fix must not
        swallow legitimate values along with the malformed ones."""
        assert extract_numeric(value) == value


# ---------------------------------------------------------------------------
# parse_where / parse_sort — CLI expression parsers
# ---------------------------------------------------------------------------


class TestParseWhereProperty:
    @settings(max_examples=300)
    @given(expr=st.text(max_size=40))
    def test_returns_triple_or_value_error(self, expr: str) -> None:
        """Contract 4. ``cmd_filter`` catches ``ValueError`` to print a usage
        hint; any other exception escapes as a traceback."""
        try:
            field, op, value = parse_where(expr)
        except ValueError:
            return
        assert isinstance(field, str)
        assert isinstance(value, str)
        assert op in WHERE_OPERATORS

    @settings(max_examples=200)
    @given(
        field=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
            min_size=1,
            max_size=12,
        ),
        op=st.sampled_from(sorted(WHERE_OPERATORS)),
        value=st.text(max_size=12).filter(lambda s: not any(c in s for c in "><=!")),
    )
    def test_well_formed_expressions_round_trip(
        self, field: str, op: str, value: str
    ) -> None:
        got_field, got_op, got_value = parse_where(f"{field}{op}{value}")
        assert got_field == field
        assert got_op == op
        assert got_value == value.strip()


class TestParseSortProperty:
    @settings(max_examples=300)
    @given(expr=st.text(max_size=40))
    def test_total_and_shape(self, expr: str) -> None:
        """Contract 5."""
        field, reverse = parse_sort(expr)
        assert isinstance(field, str)
        assert isinstance(reverse, bool)


# ---------------------------------------------------------------------------
# apply_where / sort_products / text_score — the consumers
# ---------------------------------------------------------------------------


@st.composite
def _products(draw: Any, size: int = 3) -> list[SimpleNamespace]:
    """Rows shaped like DB records, with adversarial values in the one
    field the query paths will read."""
    n = draw(st.integers(min_value=0, max_value=size))
    return [
        SimpleNamespace(
            rated_power=draw(adversarial_values),
            manufacturer=draw(st.one_of(st.none(), st.text(max_size=8))),
            part_number=draw(st.one_of(st.none(), st.text(max_size=8))),
        )
        for _ in range(n)
    ]


class TestConsumersProperty:
    @settings(max_examples=300)
    @given(
        value=adversarial_values,
        op=st.sampled_from(sorted(WHERE_OPERATORS)),
        operand=st.text(max_size=10),
    )
    def test_apply_where_is_total(self, value: Any, op: str, operand: str) -> None:
        """Contract 6. This is the path the bug crashed: a single
        unusable field value must fail the clause, not the command."""
        product = SimpleNamespace(rated_power=value)
        assert isinstance(apply_where(product, "rated_power", op, operand), bool)

    @settings(max_examples=200)
    @given(
        value=adversarial_values,
        op=st.sampled_from(sorted(WHERE_OPERATORS)),
        operand=st.text(max_size=10),
    )
    def test_apply_where_missing_field_is_false(
        self, value: Any, op: str, operand: str
    ) -> None:
        """A field the model doesn't carry never passes a clause."""
        product = SimpleNamespace(rated_power=value)
        assert apply_where(product, "no_such_field", op, operand) is False

    @settings(max_examples=300)
    @given(
        products=_products(), direction=st.sampled_from(["asc", "desc", "", "garbage"])
    )
    def test_sort_products_is_total_and_a_permutation(
        self, products: list[SimpleNamespace], direction: str
    ) -> None:
        """Contract 7. ``cmp_to_key`` over a comparator that raises takes
        the command down; one that's non-transitive silently mis-orders.
        Sorting must preserve exactly the input rows."""
        out = sort_products(list(products), [f"rated_power:{direction}"])
        assert len(out) == len(products)
        assert sorted(map(id, out)) == sorted(map(id, products))

    @settings(max_examples=200)
    @given(products=_products(), query=st.text(max_size=12))
    def test_text_score_is_total_and_non_negative(
        self, products: list[SimpleNamespace], query: str
    ) -> None:
        """Contract 8."""
        for product in products:
            score = text_score(product, query)
            assert isinstance(score, int)
            assert score >= 0
