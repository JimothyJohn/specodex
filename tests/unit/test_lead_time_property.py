"""Property tests for ``specodex.pricing.lead_time`` statement parsing.

The example-based companion (``test_lead_time_inference.py``) pins the
happy paths and the no-invention rule per input class. This file
generates adversarial statement text — arbitrary unicode, junk mixed
with real duration phrases, huge digit runs — and asserts the
documented contract:

1. ``parse_lead_statement`` / ``parse_lead_statement_range`` never
   raise on any string.
2. Any non-None result has the documented shape: unit in
   {"days", "weeks"}, values positive and finite, ``min <= max``.
3. The two functions agree: both None, or ``parse_lead_statement``
   returns the range's ``(max, unit)``.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings, strategies as st

from specodex.pricing.lead_time import (
    parse_lead_statement,
    parse_lead_statement_range,
)

# Arbitrary text plus statement-shaped fragments so the search spends
# time near the parseable boundary, not only in random unicode.
_fragments = st.sampled_from(
    [
        "ships in 3 days",
        "5-7 business days",
        "2 to 3 weeks",
        "same day",
        "same-day",
        "0 days",
        "7-5 days",
        "in as little as 3 days",
        "1.5 weeks",
        "999999999999999999999999 days",
        "days",
        "weeks",
        "-",
        "to",
        "3",
    ]
)
_statements = st.one_of(
    st.text(max_size=200),
    st.lists(st.one_of(_fragments, st.text(max_size=20)), max_size=5).map(" ".join),
)


@pytest.mark.unit
@settings(max_examples=200)
@given(text=_statements)
def test_parse_never_raises_and_result_shape(text: str) -> None:
    ranged = parse_lead_statement_range(text)
    single = parse_lead_statement(text)

    if ranged is None:
        assert single is None
        return

    lo, hi, unit = ranged
    assert unit in ("days", "weeks")
    assert math.isfinite(lo) and math.isfinite(hi)
    assert 0 < lo <= hi
    assert single == (hi, unit)
