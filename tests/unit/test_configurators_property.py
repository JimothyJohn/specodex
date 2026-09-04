"""Property tests for the Stober configurator's pure parsers.

The example-based companion (``test_configurators.py``) pins the parsers
against **captured live responses** plus a hand-picked list of malformed
payloads. This file feeds *generated* adversarial payloads — recursive
JSON, non-string ``filterId``\\ s (including unhashable ones), bool and
non-finite numbers, unicode-laced names, ``values`` lists of the wrong
shape — and asserts the documented contract holds for everything the
strategy can produce.

Why this matters: ``parse_requirements`` / ``parse_group_selection`` are
the deserialization boundary for **vendor-controlled JSON**. Stober can
redeploy its SPA, a WAF can substitute an error document, and a locale
switch can reshape a field, all without notice. Anything that escapes
these parsers as an exception takes the whole harvest run down; anything
that escapes as a malformed ``RequirementParam`` poisons the sizing
payload built from it.

**Contracts under test:**

1. Neither parser raises on any JSON-shaped input.
2. ``parse_requirements`` always returns a ``list``;
   ``parse_group_selection`` always returns a ``GroupSelection`` whose
   ``requirements`` is a ``list``.
3. Every emitted ``RequirementParam`` is well-formed: ``filter_id`` is a
   non-empty ``str``, ``name`` is a ``str``, ``kind`` is ``"range"`` or
   ``"select"``, ``unit`` is ``None`` or a non-empty ``str``,
   ``minimum`` / ``maximum`` are ``None`` or *finite* floats, ``options``
   is a tuple of non-empty ``str``, and ``gearhead_field`` is ``None`` or
   one of :data:`FILTER_TO_GEARHEAD_FIELD`'s values.
4. ``kind == "select"`` implies at least one option (a selection with no
   real options carries no information and is skipped).
5. ``filter_id`` is unique within a result — the dedupe contract.
6. Every emitted param is hashable (the parsers put ``filter_id`` in a
   ``set``, and callers put the params themselves in sets).

Three bugs this file's contract pins, all fixed in the same PR:

* an unhashable ``filterId`` (``{}``, ``[]``, ``set()``) raised
  ``TypeError`` out of ``FILTER_TO_GEARHEAD_FIELD.get`` — a vendor
  payload could crash the parser outright;
* a non-string ``filterId`` / ``filterName`` / option ``key`` flowed
  straight into the dataclass, violating its annotations;
* ``_as_float`` accepted bools (``float(True) == 1.0``) and NaN/±inf,
  putting non-comparable bounds on a requirement slider.
"""

from __future__ import annotations

import math
from typing import Any

from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.configurators.stober import (
    FILTER_TO_GEARHEAD_FIELD,
    GroupSelection,
    RequirementParam,
    parse_group_selection,
    parse_requirements,
)

_MAPPED_FIELDS = set(FILTER_TO_GEARHEAD_FIELD.values())


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Values that show up where the API documents a scalar. Includes the
# unhashable containers that used to crash the parser.
_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(),
    st.text(alphabet="ig Macc Fr Phi n1zbµΩ\x00\n"),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
    st.frozensets(st.integers(), max_size=2),
)

# The filter ids the adapter actually maps, plus adversarial neighbours,
# so the mapped-field branch is exercised rather than always missing.
_filter_ids = st.one_of(
    st.sampled_from(sorted(FILTER_TO_GEARHEAD_FIELD)),
    st.sampled_from(["", "  ", "unknown", "IG", "ig "]),
    _scalars,
)

_types = st.sampled_from(
    [
        "ParameterDoubleValue",
        "ParameterSingleSelection",
        "ParameterUnknown",
        "",
    ]
)


@st.composite
def _value_entry(draw: st.DrawFn) -> Any:
    """One entry of a ``ParameterSingleSelection``'s ``values`` list."""
    return draw(
        st.one_of(
            st.fixed_dictionaries({"key": _scalars}),
            st.just({"key": "NoSelection"}),
            st.just({}),
            _scalars,
        )
    )


@st.composite
def _filter_obj(draw: st.DrawFn) -> Any:
    """One API filter object — sometimes not an object at all."""
    if draw(st.booleans()):
        return draw(_scalars)
    obj: dict[str, Any] = {
        "filterId": draw(_filter_ids),
        "$type": draw(_types),
    }
    for key, strategy in (
        ("filterName", _scalars),
        ("units", _scalars),
        ("minimumValue", _scalars),
        ("maximumValue", _scalars),
        ("values", st.lists(_value_entry(), max_size=4)),
        (
            "infoFlyoutContents",
            st.one_of(_scalars, st.fixed_dictionaries({"titleText": _scalars})),
        ),
    ):
        if draw(st.booleans()):
            obj[key] = draw(strategy)
    return obj


def _groups(inner_key: str) -> st.SearchStrategy[Any]:
    """``filterGroups``-shaped payloads keyed by the parser's inner field."""
    group = st.one_of(
        st.fixed_dictionaries({inner_key: st.lists(_filter_obj(), max_size=4)}),
        st.fixed_dictionaries({inner_key: _scalars}),
        st.just({}),
        _scalars,
    )
    return st.one_of(st.lists(group, max_size=4), _scalars)


_json = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=True, allow_infinity=True),
        st.text(),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(max_size=4), children, max_size=3),
    ),
    max_leaves=12,
)


# ---------------------------------------------------------------------------
# Shared assertions
# ---------------------------------------------------------------------------


def _assert_well_formed(params: list[RequirementParam]) -> None:
    assert isinstance(params, list)
    for p in params:
        assert isinstance(p, RequirementParam)
        assert isinstance(p.filter_id, str) and p.filter_id != ""
        assert isinstance(p.name, str)
        assert p.kind in ("range", "select")
        assert p.unit is None or (isinstance(p.unit, str) and p.unit != "")
        for bound in (p.minimum, p.maximum):
            assert bound is None or (isinstance(bound, float) and math.isfinite(bound))
        assert isinstance(p.options, tuple)
        assert all(isinstance(o, str) and o != "" for o in p.options)
        assert p.gearhead_field is None or p.gearhead_field in _MAPPED_FIELDS
        # kind == "select" carries information only if it has options.
        if p.kind == "select":
            assert p.options
        else:
            assert p.options == ()
        # Callers put params in sets; frozen dataclass must stay hashable.
        hash(p)
    ids = [p.filter_id for p in params]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# parse_requirements
# ---------------------------------------------------------------------------


class TestParseRequirements:
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(_groups("parameters"))
    def test_never_raises_and_emits_well_formed_params(self, payload: Any) -> None:
        _assert_well_formed(parse_requirements(payload))

    @settings(max_examples=300, deadline=None)
    @given(_json)
    def test_never_raises_on_arbitrary_json(self, payload: Any) -> None:
        _assert_well_formed(parse_requirements(payload))

    @settings(max_examples=200, deadline=None)
    @given(st.lists(_filter_obj(), max_size=5))
    def test_group_nesting_is_flattened_not_dropped(self, filters: Any) -> None:
        """One group of N filters == N groups of one filter each."""
        flat = parse_requirements([{"parameters": filters}])
        split = parse_requirements([{"parameters": [f]} for f in filters])
        assert [p.filter_id for p in flat] == [p.filter_id for p in split]

    @settings(max_examples=200, deadline=None)
    @given(st.lists(_filter_obj(), max_size=4))
    def test_duplicated_input_is_deduped(self, filters: Any) -> None:
        once = parse_requirements([{"parameters": filters}])
        twice = parse_requirements([{"parameters": filters + filters}])
        assert [p.filter_id for p in twice] == [p.filter_id for p in once]


# ---------------------------------------------------------------------------
# parse_group_selection
# ---------------------------------------------------------------------------


class TestParseGroupSelection:
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(
        st.one_of(
            st.fixed_dictionaries(
                {
                    "configurationId": _scalars,
                    "productSelectionStrip": _scalars,
                    "productGroupFilters": _groups("filters"),
                }
            ),
            _scalars,
        )
    )
    def test_never_raises_and_emits_well_formed_params(self, payload: Any) -> None:
        sel = parse_group_selection(payload)
        assert isinstance(sel, GroupSelection)
        _assert_well_formed(sel.requirements)

    @settings(max_examples=300, deadline=None)
    @given(_json)
    def test_never_raises_on_arbitrary_json(self, payload: Any) -> None:
        sel = parse_group_selection(payload)
        assert isinstance(sel, GroupSelection)
        _assert_well_formed(sel.requirements)

    @settings(max_examples=200, deadline=None)
    @given(st.dictionaries(st.text(max_size=4), _scalars, max_size=4))
    def test_missing_config_id_is_none_not_a_raise(self, payload: Any) -> None:
        sel = parse_group_selection(payload)
        assert sel.configuration_id == payload.get("configurationId")
        assert sel.strip == payload.get("productSelectionStrip")
        assert sel.requirements == []
