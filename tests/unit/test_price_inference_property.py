"""Property tests for ``specodex.pricing.inference.estimate_price``.

The example-based companion (``test_price_inference.py``) pins the
happy paths (power-law ladder exactness, KNN weighting) and each gate.
This file generates *adversarial* comparable pools — NaN/inf prices
and specs, non-USD currencies, missing specs, mismatched units,
MinMaxUnit ranges, empty and giant values — and asserts the documented
contract holds for every input the strategy produces.

**Contracts under test:**

1. ``estimate_price`` never raises on any pool the store could
   plausibly hand it (including junk rows the pool filter must
   exclude: NaN/inf prices, non-positive values, missing specs).
2. The return value is ``None`` or a valid ``SourcedFigure``:
   USD value, positive and finite, ``confidence == "inferred"``,
   exactly one ``db_inference`` citation whose ``comparable_ids``
   are sorted, non-empty, drawn from the pool, and never contain
   the target; ``method_version`` is one of the two published
   method strings; the figure round-trips through
   ``model_dump`` -> ``model_validate`` (citation invariants are
   enforced by the Pydantic model on both trips).
3. Determinism under input permutation: shuffling the pool produces
   a bit-identical figure (COMMERCIAL.md ground rule 4).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.motor import Motor
from specodex.pricing.inference import (
    METHOD_FAMILY,
    METHOD_KNN,
    MIN_POOL,
    estimate_price,
)

RETRIEVED_AT = "2026-07-21T00:00:00+00:00"

# Adversarial numerics: routine magnitudes, giants, subnormals, zero,
# negatives, NaN and infinities. The pool filter must survive them all.
_adversarial_floats = st.one_of(
    st.floats(min_value=0.01, max_value=1e6),
    st.floats(allow_nan=True, allow_infinity=True),
    st.sampled_from(
        [0.0, -1.0, 1e-300, 1e300, float("nan"), float("inf"), -float("inf")]
    ),
)

_units = st.sampled_from(["W", "kW", "hp", "Nm", ""])
_price_units = st.sampled_from(["USD", "EUR", "usd", ""])
_manufacturers = st.sampled_from(["Acme", "Bolt", "", "  "])
_families = st.sampled_from([None, "AKM", "ZZ", "", "  "])


@st.composite
def _spec_value(draw: st.DrawFn):
    """None, a ValueUnit, a MinMaxUnit (possibly empty), or a bare float."""
    shape = draw(st.sampled_from(["none", "value_unit", "min_max", "bare"]))
    if shape == "none":
        return None
    if shape == "bare":
        return draw(_adversarial_floats)
    unit = draw(_units)
    if shape == "value_unit":
        return ValueUnit.model_construct(value=draw(_adversarial_floats), unit=unit)
    lo = draw(st.one_of(st.none(), _adversarial_floats))
    hi = draw(st.one_of(st.none(), _adversarial_floats))
    return MinMaxUnit.model_construct(min=lo, max=hi, unit=unit)


@st.composite
def _product(draw: st.DrawFn, index: int) -> Motor:
    """A Motor whose commercial/spec fields may be arbitrarily junky.

    Built via constructor for the well-formed skeleton, then the spec
    and price fields are overwritten unvalidated (``model_construct``
    values) — mirroring the worst rows a DB scan could deserialize.
    """
    product = Motor(
        product_id=UUID(int=index + 1),
        product_name=f"M{index}",
        manufacturer=draw(_manufacturers),
        product_type="motor",
        product_family=draw(_families),
        part_number=f"PN-{index:03d}",
    )
    product.rated_power = draw(_spec_value())
    product.rated_torque = draw(_spec_value())
    if draw(st.booleans()):
        product.msrp = ValueUnit.model_construct(
            value=draw(_adversarial_floats), unit=draw(_price_units)
        )
    return product


@st.composite
def _pool(draw: st.DrawFn, min_size: int = 0, max_size: int = 10) -> list[Motor]:
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    return [draw(_product(index=i)) for i in range(size)]


def _assert_valid_figure(figure, target: Motor, pool: list[Motor]) -> None:
    assert figure.confidence == "inferred"
    assert figure.value.unit == "USD"
    assert figure.value.value > 0
    assert figure.value.value == figure.value.value  # not NaN
    assert figure.value.value != float("inf")
    if figure.observed_range is not None:
        assert figure.observed_range.unit == "USD"
        assert figure.observed_range.min <= figure.observed_range.max
    assert len(figure.sources) == 1
    citation = figure.sources[0]
    assert citation.kind == "db_inference"
    assert citation.retrieved_at == RETRIEVED_AT
    assert citation.method_version in (METHOD_FAMILY, METHOD_KNN)
    assert citation.comparable_ids is not None
    assert len(citation.comparable_ids) >= MIN_POOL
    assert citation.comparable_ids == sorted(citation.comparable_ids)
    assert len(set(citation.comparable_ids)) == len(citation.comparable_ids)
    pool_ids = {str(p.product_id) for p in pool}
    assert set(citation.comparable_ids) <= pool_ids
    assert str(target.product_id) not in citation.comparable_ids
    # Round-trip: the serialized figure re-validates (the model
    # enforces the db_inference invariants on both directions).
    from specodex.models.commercial import SourcedFigure

    revalidated = SourcedFigure.model_validate(figure.model_dump())
    assert revalidated.model_dump() == figure.model_dump()


@pytest.mark.unit
class TestNeverRaisesAndShape:
    @given(pool=_pool(), target=_product(index=1000))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_raises_and_returns_none_or_valid_figure(
        self, pool: list[Motor], target: Motor
    ) -> None:
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        if figure is not None:
            _assert_valid_figure(figure, target, pool)

    @given(target=_product(index=1000))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_empty_pool_returns_none(self, target: Motor) -> None:
        assert estimate_price(target, [], retrieved_at=RETRIEVED_AT) is None

    @given(pool=_pool(min_size=1, max_size=2), target=_product(index=1000))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_sub_minimum_pool_returns_none(
        self, pool: list[Motor], target: Motor
    ) -> None:
        assert estimate_price(target, pool, retrieved_at=RETRIEVED_AT) is None


@pytest.mark.unit
class TestDeterminismUnderPermutation:
    @given(
        data=st.data(), pool=_pool(min_size=3, max_size=10), target=_product(index=1000)
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_permuted_pool_gives_identical_result(
        self, data: st.DataObject, pool: list[Motor], target: Motor
    ) -> None:
        reference: Optional[object] = estimate_price(
            target, pool, retrieved_at=RETRIEVED_AT
        )
        shuffled = data.draw(st.permutations(pool))
        result = estimate_price(target, shuffled, retrieved_at=RETRIEVED_AT)
        if reference is None:
            assert result is None
        else:
            assert result is not None
            assert result.model_dump() == reference.model_dump()

    @given(pool=_pool(min_size=3, max_size=8), target=_product(index=1000))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_repeat_call_is_idempotent(self, pool: list[Motor], target: Motor) -> None:
        first = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        second = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        if first is None:
            assert second is None
        else:
            assert second is not None
            assert first.model_dump() == second.model_dump()
