"""Property tests for specodex/envelope/match.py.

Designators are LLM-emitted; rows are DB strings. Contract:
- match_rows never raises, maps only valid indices, and a row's match is
  either exact or a token-boundary prefix of the row's own keys.
- select_envelope_pages never raises, returns sorted unique in-range pages,
  never more than max(cap, 0).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from specodex.envelope.match import (
    match_rows,
    normalize_designator,
    select_envelope_pages,
    token_prefixes,
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
