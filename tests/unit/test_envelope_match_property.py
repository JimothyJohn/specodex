"""Property tests for specodex/envelope/match.py.

Designators are LLM-emitted; rows are DB strings. Contract:
- match_rows never raises, maps only valid indices, and a row's match is
  either exact or a token-boundary prefix of the row's own keys.
- select_envelope_pages never raises, returns sorted unique in-range pages,
  never more than max(cap, 0).
"""

from __future__ import annotations

import pytest
import math

from hypothesis import given, settings
from hypothesis import strategies as st

from specodex.envelope.match import (
    agreed_dims,
    match_rows,
    normalize_designator,
    select_envelope_pages,
)

_text = st.one_of(
    st.none(),
    st.integers(),
    st.text(max_size=16),
    st.text(alphabet="AB01-_ /.x9", max_size=12),
    st.sampled_from(
        [
            "AB042",
            "AB042-005",
            "AB0420",
            "SGM7J-01",
            "60",
            "",
            "ＡＢ０４２",
            "AB\x00042",
        ]
    ),
)
_rows = st.lists(
    st.fixed_dictionaries(
        {"part_number": _text, "product_name": _text, "frame_size": _text}
    ),
    max_size=6,
)
_extractions = st.lists(st.tuples(_text, st.integers(0, 3)), max_size=6)


@pytest.mark.unit
@settings(max_examples=300)
@given(ex=_extractions, rows=_rows)
def test_match_rows_contract(ex, rows):
    out = match_rows(ex, rows)
    for ri, ei in out.items():
        assert 0 <= ri < len(rows) and 0 <= ei < len(ex)
        d = ex[ei][0]
        assert normalize_designator(d)
        r = rows[ri]
        keys = [r.get("part_number"), r.get("frame_size")]
        if not r.get("part_number"):
            keys.append(r.get("product_name"))
        # Some leading-words candidate of the designator shares its token
        # run with a row key (exact, prefix or infix — never unrelated).
        words = d.split()
        cands = {
            normalize_designator(" ".join(words[:n])) for n in range(1, len(words) + 1)
        }
        cands.discard("")
        assert any(
            c[:-1] in normalize_designator(k)
            for c in cands
            for k in keys
            if normalize_designator(k)
        ), (d, r)


_scores = st.lists(
    st.fixed_dictionaries(
        {},
        optional={
            "page": st.one_of(
                st.integers(-5, 60), st.booleans(), st.text(max_size=2), st.none()
            ),
            "score": st.one_of(
                st.floats(allow_nan=True, allow_infinity=True),
                st.integers(),
                st.none(),
                st.text(max_size=2),
            ),
        },
    ),
    max_size=40,
)


@pytest.mark.unit
@settings(max_examples=300)
@given(
    scores=_scores,
    spec=st.lists(st.one_of(st.integers(-5, 60), st.booleans()), max_size=30),
    cap=st.integers(-50, 50),
    total=st.integers(-2, 50),
)
def test_select_pages_contract(scores, spec, cap, total):
    out = select_envelope_pages(scores, spec, cap, total_pages=total)
    assert out == sorted(set(out))
    assert len(out) <= max(cap, 0)
    assert all(
        isinstance(p, int) and not isinstance(p, bool) and 0 <= p < total for p in out
    )


_AXES = ("length", "width", "height")
_num = st.one_of(
    st.floats(min_value=0.01, max_value=1e5, allow_nan=False),
    st.floats(),  # NaN, ±inf, negatives, zero
    st.integers(-10, 10_000),
    st.booleans(),
    st.none(),
    st.text(max_size=4),
)
_axis = st.one_of(
    st.fixed_dictionaries({"value": _num, "unit": st.sampled_from(["mm", "in", ""])}),
    st.none(),
    st.integers(),
    st.dictionaries(st.text(max_size=5), st.integers(), max_size=2),
)
_dims = st.one_of(
    st.none(),
    st.integers(),
    st.text(max_size=5),
    st.lists(st.integers(), max_size=2),
    st.dictionaries(
        st.one_of(st.sampled_from(_AXES), st.text(max_size=6)), _axis, max_size=4
    ),
)
_good = st.dictionaries(
    st.sampled_from(_AXES),
    st.floats(min_value=0.1, max_value=1e4).map(lambda v: {"value": v, "unit": "mm"}),
    min_size=1,
)


def _valid(v):
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
        and v > 0
    )


@pytest.mark.unit
@settings(max_examples=300)
@given(a=_dims, b=_dims)
def test_agreed_dims_contract(a, b):
    out = agreed_dims(a, b)
    assert out == agreed_dims(b, a)  # symmetric
    if out is None:
        return
    assert isinstance(a, dict) and isinstance(b, dict)
    assert out.keys() == a.keys() == b.keys() and out
    for axis, val in out.items():
        x, y = a[axis]["value"], b[axis]["value"]
        assert _valid(x) and _valid(y)
        assert a[axis]["unit"] == b[axis]["unit"] == val["unit"] == "mm"
        assert val["value"] == max(x, y)
        assert abs(x - y) <= 0.10 * max(x, y) + 1e-9


@pytest.mark.unit
@settings(max_examples=200)
@given(
    a=_good, factor=st.floats(min_value=0.5, max_value=1.5), axis=st.sampled_from(_AXES)
)
def test_agreed_dims_scaled_axis(a, factor, axis):
    """Scaling one present axis by ``factor`` agrees iff the gap is ≤ 10%."""
    b = {k: dict(v) for k, v in a.items()}
    if axis not in b:
        assert agreed_dims(a, b) == a
        return
    x = a[axis]["value"]
    b[axis]["value"] = x * factor
    y = b[axis]["value"]
    gap = abs(x - y) / max(x, y)
    if abs(gap - 0.10) < 1e-9:
        return  # float boundary; pinned by the example test
    assert (agreed_dims(a, b) is not None) == (gap <= 0.10)
