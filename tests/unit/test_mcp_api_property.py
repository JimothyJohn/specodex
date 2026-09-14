"""Property tests for the MCP tool-argument validators (specodex/mcp/api.py).

These strings become URL path segments, query parameters and a JSON
body sent to the public API on behalf of an LLM, so they are an
adversarial surface: path traversal, CR/LF, second parameters, NUL,
unicode look-alikes. Contract: every validator either returns a value
that is exactly what was documented, or raises SpecodexApiError — never
anything else — and accepted values contain nothing that could change
the request shape.
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from specodex.mcp.api import (
    SpecodexApiError,
    validate_limit,
    validate_product_id,
    validate_product_type,
    validate_sort,
    validate_where,
)

anything = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(min_size=0, max_size=80),
    st.binary(max_size=40),
    st.lists(st.text(max_size=20), max_size=5),
    st.dictionaries(st.text(max_size=5), st.integers(), max_size=3),
)

attacks = st.sampled_from(
    [
        "../admin",
        "..%2f..%2fadmin",
        "4c960ae8-e5b3-555d-8808-b06f9d2e9357/../x",
        "4c960ae8-e5b3-555d-8808-b06f9d2e9357?type=drive",
        "4c960ae8-e5b3-555d-8808-b06f9d2e9357&limit=100000",
        "motor\r\nX-Injected: 1",
        "motor\x00",
        "MOTOR",
        "mоtor",  # Cyrillic о
        "rated_power>=1&type=drive",
        "rated_power>=1\n",
        "rated_power=>1",
        "rated_power",
        "field:desc:asc",
        "",
        " ",
    ]
)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@settings(max_examples=300)
@given(st.one_of(anything, attacks))
def test_product_id_accepts_only_lowercase_uuids(v):
    try:
        out = validate_product_id(v)
    except SpecodexApiError:
        return
    assert out is v and UUID_RE.match(out)


@settings(max_examples=300)
@given(st.one_of(anything, attacks))
def test_product_type_is_a_safe_snake_case_token(v):
    try:
        out = validate_product_type(v)
    except SpecodexApiError:
        return
    assert out is v and re.fullmatch(r"[a-z][a-z_]{1,40}", out)


@settings(max_examples=300)
@given(
    st.one_of(anything, st.lists(st.one_of(st.text(max_size=60), attacks), max_size=6))
)
def test_where_clauses_keep_field_op_value_shape(v):
    try:
        out = validate_where(v)
    except SpecodexApiError:
        return
    except TypeError:
        # A non-list is documented as unsupported; iterating a non-iterable is
        # the only other path. Anything else is a bug.
        assert not isinstance(v, (list, type(None)))
        return
    assert isinstance(out, list)
    for clause in out:
        assert "\r" not in clause and "\n" not in clause
        m = re.match(r"^([a-z][a-z0-9_.]*)(>=|<=|!=|>|<|=)(.{1,200})$", clause)
        assert m, clause
        assert "&" not in m.group(1)


@settings(max_examples=300)
@given(
    st.one_of(anything, st.lists(st.one_of(st.text(max_size=40), attacks), max_size=6))
)
def test_sort_keys_are_field_with_optional_direction(v):
    try:
        out = validate_sort(v)
    except SpecodexApiError:
        return
    except TypeError:
        assert not isinstance(v, (list, type(None)))
        return
    for key in out:
        assert re.fullmatch(r"[a-z][a-z0-9_.]*(:(asc|desc))?", key), key


@settings(max_examples=300)
@given(
    st.one_of(anything, st.integers(min_value=-10, max_value=5000)),
    st.integers(min_value=1, max_value=2000),
)
def test_limit_is_a_bounded_int_or_the_default(v, maximum):
    default = min(20, maximum)
    try:
        out = validate_limit(v, default=default, maximum=maximum)
    except SpecodexApiError:
        assert v is not None
        return
    assert isinstance(out, int) and not isinstance(out, bool)
    assert 1 <= out <= maximum
    if v is None:
        assert out == default
