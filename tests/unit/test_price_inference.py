"""Tests for ``specodex.pricing.inference.estimate_price`` (price-comps-v1).

Pins the documented contract from todo/COMMERCIAL.md Phase 2:

- family ladder: exact log-log interpolation on a perfect power-law
  ladder (industrial pricing is power-law in size, so a synthetic
  power-law ladder must be reproduced bit-for-bit);
- KNN fallback: nearest comparables win, distance-weighted median;
- gates: small pool, high dispersion, and extrapolation clamp return
  None (or clamp -> KNN) rather than a junk figure;
- citations: sorted comparable ids + correct method_version;
- unit-mismatch comparables are dropped, never converted;
- determinism: shuffling the input pool changes nothing.

Property companion: ``test_price_inference_property.py``.
"""

from __future__ import annotations

import random
from typing import Optional
from uuid import UUID

import pytest

from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.motor import Motor
from specodex.pricing.inference import (
    METHOD_FAMILY,
    METHOD_KNN,
    estimate_price,
)

RETRIEVED_AT = "2026-07-21T00:00:00+00:00"


def motor(
    i: int,
    *,
    mfg: str = "Acme",
    family: Optional[str] = "AKM",
    power: Optional[float] = None,
    torque: Optional[float] = None,
    price: Optional[float] = None,
) -> Motor:
    """A motor with a deterministic product_id and optional specs/price."""
    kwargs: dict = {
        "product_id": UUID(int=i),
        "product_name": f"M{i}",
        "manufacturer": mfg,
        "product_type": "motor",
        "product_family": family,
        "part_number": f"{family or 'X'}-{i:03d}",
    }
    if power is not None:
        kwargs["rated_power"] = {"value": power, "unit": "W"}
    if torque is not None:
        kwargs["rated_torque"] = {"value": torque, "unit": "Nm"}
    if price is not None:
        kwargs["msrp"] = {"value": price, "unit": "USD"}
    return Motor(**kwargs)


def power_law_price(power: float, coeff: float = 10.0, exponent: float = 0.8) -> float:
    return coeff * power**exponent


def power_law_ladder(coeff: float = 10.0, exponent: float = 0.8) -> list[Motor]:
    """Five-point Acme/AKM ladder priced exactly on a power law."""
    return [
        motor(i + 1, power=p, price=power_law_price(p, coeff, exponent))
        for i, p in enumerate([100.0, 200.0, 400.0, 800.0, 1600.0])
    ]


@pytest.mark.unit
class TestFamilyLadder:
    def test_exact_on_perfect_power_law(self) -> None:
        """A least-squares log-log fit on perfect power-law data is exact."""
        pool = power_law_ladder()
        target = motor(99, power=300.0)
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        expected = power_law_price(300.0)
        assert figure.value.unit == "USD"
        assert figure.value.value == pytest.approx(round(expected, 2), abs=0.01)
        assert figure.sources[0].method_version == METHOD_FAMILY

    def test_interpolates_at_a_ladder_point(self) -> None:
        pool = power_law_ladder()
        target = motor(99, power=400.0)
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.value.value == pytest.approx(
            round(power_law_price(400.0), 2), abs=0.01
        )

    def test_confidence_and_range_shape(self) -> None:
        pool = power_law_ladder()
        figure = estimate_price(motor(99, power=300.0), pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.confidence == "inferred"
        assert figure.observed_range is not None
        assert figure.observed_range.unit == "USD"
        assert figure.observed_range.min <= figure.observed_range.max
        # p25/p75 of the five ladder prices, linearly interpolated.
        prices = sorted(power_law_price(p) for p in [100, 200, 400, 800, 1600])
        assert figure.observed_range.min == pytest.approx(round(prices[1], 2), abs=0.01)
        assert figure.observed_range.max == pytest.approx(round(prices[3], 2), abs=0.01)

    def test_requires_nonempty_family(self) -> None:
        """No family on the target -> ladder skipped -> KNN method."""
        pool = power_law_ladder()
        target = motor(99, family=None, power=300.0)
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.sources[0].method_version == METHOD_KNN

    def test_cross_manufacturer_rows_not_in_ladder(self) -> None:
        pool = power_law_ladder() + [
            motor(50, mfg="Bolt", power=300.0, price=1.0),
        ]
        figure = estimate_price(motor(99, power=300.0), pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.sources[0].method_version == METHOD_FAMILY
        assert str(UUID(int=50)) not in figure.sources[0].comparable_ids


@pytest.mark.unit
class TestKnnFallback:
    def test_picks_nearest_and_weights_correctly(self) -> None:
        """Zero-distance neighbour's weight (10) dominates the median."""
        pool = [
            motor(1, mfg="Bolt", family="B", power=100.0, price=100.0),  # d = 0
            motor(2, mfg="Ceta", family="C", power=1000.0, price=110.0),  # d = 1
            motor(3, mfg="Dyna", family="D", power=10000.0, price=120.0),  # d = 2
        ]
        target = motor(99, mfg="Acme", family=None, power=100.0)
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.sources[0].method_version == METHOD_KNN
        # weights: 1/0.1 = 10, 1/1.1, 1/2.1 -> cumulative crosses half
        # the total at the nearest neighbour's price.
        assert figure.value.value == pytest.approx(100.0)

    def test_caps_at_k_neighbours(self) -> None:
        # 10 comparables at increasing distance; only the 8 nearest count.
        pool = [
            motor(i + 1, mfg=f"M{i}", family=None, power=100.0 * 2**i, price=50.0 + i)
            for i in range(10)
        ]
        target = motor(99, family=None, power=100.0)
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        used = figure.sources[0].comparable_ids
        assert len(used) == 8
        # The two farthest (ids 9, 10) are excluded.
        assert str(UUID(int=9)) not in used
        assert str(UUID(int=10)) not in used

    def test_requires_at_least_one_shared_spec(self) -> None:
        """Target with no dominant spec at all -> None."""
        pool = power_law_ladder()
        target = motor(99)  # no rated_power, no rated_torque
        assert estimate_price(target, pool, retrieved_at=RETRIEVED_AT) is None

    def test_uses_secondary_spec_when_primary_missing(self) -> None:
        pool = [
            motor(i + 1, mfg=f"M{i}", family=None, torque=2.0 + i, price=100.0 + i)
            for i in range(3)
        ]
        target = motor(99, family=None, torque=2.0)
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.sources[0].method_version == METHOD_KNN

    def test_minmax_spec_uses_midpoint(self) -> None:
        near = motor(1, mfg="Bolt", family=None, price=100.0)
        # (100 + 300) / 2 = 200 -> distance 0 from the target's 200 W.
        near.rated_power = MinMaxUnit(min=100.0, max=300.0, unit="W")
        pool = [
            near,
            motor(2, mfg="Ceta", family=None, power=20000.0, price=110.0),
            motor(3, mfg="Dyna", family=None, power=200000.0, price=120.0),
        ]
        target = motor(99, family=None, power=200.0)
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.value.value == pytest.approx(100.0)


@pytest.mark.unit
class TestGates:
    def test_pool_below_three_returns_none(self) -> None:
        pool = power_law_ladder()[:2]
        assert (
            estimate_price(motor(99, power=300.0), pool, retrieved_at=RETRIEVED_AT)
            is None
        )

    def test_unpriced_rows_do_not_count_toward_pool(self) -> None:
        pool = power_law_ladder()[:2] + [motor(7, power=500.0)]  # third has no price
        assert (
            estimate_price(motor(99, power=300.0), pool, retrieved_at=RETRIEVED_AT)
            is None
        )

    def test_high_dispersion_returns_none(self) -> None:
        """p75/p25 = 1000 on both paths -> no estimate at all."""
        pool = [
            motor(1, power=100.0, price=1.0),
            motor(2, power=200.0, price=1.0),
            motor(3, power=400.0, price=1000.0),
            motor(4, power=800.0, price=1000.0),
            motor(5, power=1600.0, price=1000.0),
        ]
        assert (
            estimate_price(motor(99, power=300.0), pool, retrieved_at=RETRIEVED_AT)
            is None
        )

    def test_extrapolation_clamp_falls_back_to_knn(self) -> None:
        """Target spec > 2x the ladder max -> family refuses, KNN answers."""
        pool = power_law_ladder()
        target = motor(99, power=10000.0)  # ladder range is 100-1600
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.sources[0].method_version == METHOD_KNN

    def test_extrapolation_below_range_falls_back_to_knn(self) -> None:
        pool = power_law_ladder()
        target = motor(99, power=10.0)  # < 100 / 2
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.sources[0].method_version == METHOD_KNN

    def test_within_2x_of_range_still_uses_ladder(self) -> None:
        pool = power_law_ladder()
        target = motor(99, power=3000.0)  # <= 1600 * 2
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert figure.sources[0].method_version == METHOD_FAMILY

    def test_extrapolation_plus_dispersion_returns_none(self) -> None:
        """Clamp kicks the estimate to KNN; KNN's own dispersion gate
        then refuses too — the chain ends in None, not junk."""
        pool = [
            motor(1, power=100.0, price=1.0),
            motor(2, power=200.0, price=1.0),
            motor(3, power=400.0, price=1000.0),
            motor(4, power=800.0, price=1000.0),
        ]
        target = motor(99, power=100000.0)
        assert estimate_price(target, pool, retrieved_at=RETRIEVED_AT) is None

    def test_target_itself_never_used_as_comparable(self) -> None:
        target = motor(99, power=300.0, price=123.0)  # priced target
        pool = power_law_ladder() + [target]
        figure = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert str(target.product_id) not in figure.sources[0].comparable_ids

    def test_non_usd_prices_excluded(self) -> None:
        eur = motor(50, power=300.0, price=1.0)
        assert eur.msrp is not None
        eur.msrp = ValueUnit.model_construct(value=1.0, unit="EUR")
        pool = power_law_ladder() + [eur]
        figure = estimate_price(motor(99, power=300.0), pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert str(UUID(int=50)) not in figure.sources[0].comparable_ids


@pytest.mark.unit
class TestUnitMismatch:
    def test_mismatched_spec_unit_comparable_dropped(self) -> None:
        """Store is canonical per key: a stray 'hp' row is dropped, not
        converted — and its absence shows in the citation."""
        odd = motor(50, power=300.0, price=999999.0)
        odd.rated_power = ValueUnit.model_construct(value=0.4, unit="hp")
        pool = power_law_ladder() + [odd]
        figure = estimate_price(motor(99, power=300.0), pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert str(UUID(int=50)) not in figure.sources[0].comparable_ids
        # And the estimate is untouched by the junk price.
        assert figure.value.value == pytest.approx(
            round(power_law_price(300.0), 2), abs=0.01
        )

    def test_all_units_mismatched_returns_none(self) -> None:
        pool = power_law_ladder()
        for comp in pool:
            comp.rated_power = ValueUnit.model_construct(
                value=comp.rated_power.value, unit="hp"
            )
        target = motor(99, power=300.0)
        assert estimate_price(target, pool, retrieved_at=RETRIEVED_AT) is None


@pytest.mark.unit
class TestCitation:
    def test_citation_ids_sorted_and_method_stamped(self) -> None:
        pool = power_law_ladder()
        figure = estimate_price(motor(99, power=300.0), pool, retrieved_at=RETRIEVED_AT)
        assert figure is not None
        assert len(figure.sources) == 1
        citation = figure.sources[0]
        assert citation.kind == "db_inference"
        assert citation.url is None
        assert citation.retrieved_at == RETRIEVED_AT
        assert citation.method_version == METHOD_FAMILY
        assert citation.comparable_ids == sorted(citation.comparable_ids)
        assert set(citation.comparable_ids) == {str(UUID(int=i)) for i in range(1, 6)}

    def test_knn_method_stamped(self) -> None:
        pool = [
            motor(i + 1, mfg=f"M{i}", family=None, power=100.0 + i, price=100.0 + i)
            for i in range(3)
        ]
        figure = estimate_price(
            motor(99, family=None, power=101.0), pool, retrieved_at=RETRIEVED_AT
        )
        assert figure is not None
        assert figure.sources[0].method_version == METHOD_KNN


@pytest.mark.unit
class TestDeterminism:
    def test_shuffled_pool_produces_identical_figure(self) -> None:
        base_pool = (
            power_law_ladder()
            + [
                motor(20 + i, mfg=f"M{i}", family=None, power=90.0 + i, price=80.0 + i)
                for i in range(4)
            ]
            + [motor(40, power=555.0)]  # unpriced noise
        )
        target = motor(99, power=300.0)
        reference = estimate_price(target, base_pool, retrieved_at=RETRIEVED_AT)
        assert reference is not None
        for seed in range(10):
            shuffled = list(base_pool)
            random.Random(seed).shuffle(shuffled)
            figure = estimate_price(target, shuffled, retrieved_at=RETRIEVED_AT)
            assert figure is not None
            assert figure.model_dump() == reference.model_dump()

    def test_repeat_call_is_bit_identical(self) -> None:
        pool = power_law_ladder()
        target = motor(99, power=300.0)
        first = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        second = estimate_price(target, pool, retrieved_at=RETRIEVED_AT)
        assert first is not None and second is not None
        assert first.model_dump() == second.model_dump()
