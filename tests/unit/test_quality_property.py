"""Property tests for ``specodex.quality.score_product`` and
``specodex.quality.filter_products``.

The example-based companion (``test_quality.py`` +
``test_quality_boundary.py``) pins the happy path and the
threshold edge cases (0/total, total/total, threshold-1 fields).
This file generates *adversarial* product shapes — random
populated/unpopulated permutations, random placeholder strings
across the known set, mixed product types — and asserts the
documented contract holds for every input the strategy produces.

Quality scoring sits between Pydantic validation and DynamoDB
write; a broken score either lets unfit products into the table
(false positive on "passed") or quietly drops fit ones (false
negative). The contract has tight invariants that a property
test catches more cleanly than enumerated examples.

**Contracts under test:**

1. ``score_product`` never raises on any well-formed
   ``ProductBase`` subclass instance. ``filter_products`` calls
   it with no surrounding ``try`` — a leak takes the whole batch
   with it.
2. The returned tuple has fixed arity and types:
   ``(float, int, int, list[str])``.
3. **Score is in ``[0.0, 1.0]``** for any product. NaN and inf
   never appear in the output.
4. **filled + len(missing) == total.** Every spec field is
   either filled or missing — no double-counting, no drift.
5. **score == filled / total** when ``total > 0``; ``score ==
   1.0`` when ``total == 0`` (vacuous truth — the only product
   class with zero spec fields would be a no-op base).
6. **``missing`` is always a subset of the model's spec fields.**
   The list never contains meta fields (``product_id``,
   ``manufacturer``, etc.) and never contains duplicates.
7. **Placeholder strings count as missing.** When a string-typed
   field is set to any value in ``PLACEHOLDER_STRINGS`` (any
   case), it lands in ``missing``, not ``filled``.
8. **``filter_products`` partitions exactly.** Every input
   appears in exactly one of ``(passed, rejected)``; their union
   has the same length as the input and preserves input order
   within each bucket.
9. **Threshold contract.** A product is in ``passed`` iff its
   ``score_product`` score is ``>= min_quality``. The bucket
   choice and the score never disagree.
10. **``spec_fields_for_model`` never returns meta fields,
    never contains duplicates, and is deterministic for the
    same model class.** Pin against drift in `_META_FIELDS`.
"""

from __future__ import annotations

import logging
from typing import Any, Type

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.models.contactor import Contactor
from specodex.models.drive import Drive
from specodex.models.electric_cylinder import ElectricCylinder
from specodex.models.gearhead import Gearhead
from specodex.models.linear_actuator import LinearActuator
from specodex.models.motor import Motor
from specodex.models.product import ProductBase
from specodex.models.robot_arm import RobotArm
from specodex.placeholders import PLACEHOLDER_STRINGS
from specodex.quality import (
    DEFAULT_MIN_QUALITY,
    _META_FIELDS,
    filter_products,
    score_product,
    spec_fields_for_model,
)


# Silence the per-product PASS/FAIL log to keep adversarial runs
# readable — a 200-example filter_products test would otherwise
# emit 200+ lines of routine PASS/FAIL noise.
@pytest.fixture(autouse=True)
def _silence_quality_logs():
    logger = logging.getLogger("specodex.quality")
    prior_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(prior_level)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


# All concrete ProductBase subclasses discovered via the registry.
# These are what score_product will see in production.
PRODUCT_CLASSES: list[Type[ProductBase]] = [
    Motor,
    Drive,
    Gearhead,
    Contactor,
    ElectricCylinder,
    LinearActuator,
    RobotArm,
]


MFG = "TestMfg"


def _product_type_for(cls: Type[ProductBase]) -> str:
    """Read the literal Pydantic default for `product_type`."""
    return cls.model_fields["product_type"].default


@st.composite
def _empty_product(draw: st.DrawFn) -> ProductBase:
    """A bare product with no spec fields populated.

    score_product should return score 0.0, filled 0, missing == all
    spec fields. Used to pin the "empty → minimum score" property.
    """
    cls = draw(st.sampled_from(PRODUCT_CLASSES))
    return cls(
        product_name=draw(st.text(min_size=1, max_size=20).filter(lambda s: s.strip())),
        manufacturer=MFG,
        product_type=_product_type_for(cls),
    )


# A "partially populated" Motor strategy. We seed a random subset of
# string-typed Motor fields with valid values; ranges/scalars are
# left None to keep the field types simple. The property scopes its
# guarantees to invariants that don't depend on which fields are
# populated (filled + missing == total, score in [0, 1], etc.).
@st.composite
def _motor_with_random_spec_fields(draw: st.DrawFn) -> Motor:
    fields_to_set = draw(
        st.sets(
            st.sampled_from(
                [
                    "rated_voltage",
                    "rated_speed",
                    "max_speed",
                    "rated_torque",
                    "peak_torque",
                    "rated_power",
                    "rated_current",
                    "peak_current",
                    "resistance",
                    "inductance",
                ]
            ),
            min_size=0,
            max_size=5,
        )
    )
    kwargs: dict[str, Any] = {
        "product_name": "Test",
        "manufacturer": MFG,
        "product_type": "motor",
        "part_number": "MTR-001",
    }
    # Use a known-good value per family so the typed-alias validator
    # accepts the input without re-shaping it to None.
    _seed = {
        "rated_voltage": "200-240;V",
        "rated_speed": "6000;rpm",
        "max_speed": "8000;rpm",
        "rated_torque": "2.5;Nm",
        "peak_torque": "5;Nm",
        "rated_power": "750;W",
        "rated_current": "3;A",
        "peak_current": "6;A",
        "resistance": "1.2;Ω",
        "inductance": "5;mH",
    }
    for f in fields_to_set:
        kwargs[f] = _seed[f]
    return Motor(**kwargs)


# Placeholder-string strategy: every entry in PLACEHOLDER_STRINGS
# (minus the empty string, which Pydantic rejects on required-string
# fields like `series`). Wrapped in random case to exercise the
# strip/lower logic in is_placeholder.
_PLACEHOLDER_VALUES = sorted(s for s in PLACEHOLDER_STRINGS if s)


@st.composite
def _placeholder_string(draw: st.DrawFn) -> str:
    base = draw(st.sampled_from(_PLACEHOLDER_VALUES))
    case_fn = draw(
        st.sampled_from(
            [
                lambda s: s,
                lambda s: s.upper(),
                lambda s: s.capitalize(),
                lambda s: f"  {s}  ",
            ]
        )
    )
    return case_fn(base)


# ---------------------------------------------------------------------------
# 1. Never raises + arity/type contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreProductShape:
    @given(
        product=st.one_of(
            _empty_product(),
            _motor_with_random_spec_fields(),
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_returns_4_tuple_with_correct_types(self, product: ProductBase) -> None:
        result = score_product(product)
        assert isinstance(result, tuple)
        assert len(result) == 4
        score, filled, total, missing = result
        assert isinstance(score, float)
        assert isinstance(filled, int)
        assert isinstance(total, int)
        assert isinstance(missing, list)
        assert all(isinstance(m, str) for m in missing)


# ---------------------------------------------------------------------------
# 2. Score is bounded in [0, 1]
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreBounded:
    @given(
        product=st.one_of(
            _empty_product(),
            _motor_with_random_spec_fields(),
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_score_in_unit_interval(self, product: ProductBase) -> None:
        score, _, _, _ = score_product(product)
        assert 0.0 <= score <= 1.0
        # Pin the no-NaN/inf invariant. Python's `==` is NaN-safe
        # only via != NaN, but `0.0 <= NaN <= 1.0` is False, so the
        # check above already excludes NaN. Inf would fail too.
        assert score == score  # not NaN


# ---------------------------------------------------------------------------
# 3. filled + missing == total
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFilledMissingConsistency:
    @given(
        product=st.one_of(
            _empty_product(),
            _motor_with_random_spec_fields(),
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_filled_plus_missing_equals_total(self, product: ProductBase) -> None:
        score, filled, total, missing = score_product(product)
        assert filled + len(missing) == total

    @given(
        product=st.one_of(
            _empty_product(),
            _motor_with_random_spec_fields(),
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_score_equals_filled_over_total(self, product: ProductBase) -> None:
        score, filled, total, _ = score_product(product)
        if total == 0:
            assert score == 1.0
        else:
            assert score == filled / total


# ---------------------------------------------------------------------------
# 4. missing is a subset of spec fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMissingSubset:
    @given(
        product=st.one_of(
            _empty_product(),
            _motor_with_random_spec_fields(),
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_missing_only_contains_spec_fields(self, product: ProductBase) -> None:
        _, _, _, missing = score_product(product)
        spec_fields = set(spec_fields_for_model(type(product)))
        assert set(missing) <= spec_fields

    @given(
        product=st.one_of(
            _empty_product(),
            _motor_with_random_spec_fields(),
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_missing_has_no_duplicates(self, product: ProductBase) -> None:
        _, _, _, missing = score_product(product)
        assert len(missing) == len(set(missing))

    @given(
        product=st.one_of(
            _empty_product(),
            _motor_with_random_spec_fields(),
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_missing_never_contains_meta_fields(self, product: ProductBase) -> None:
        _, _, _, missing = score_product(product)
        assert not (set(missing) & _META_FIELDS)


# ---------------------------------------------------------------------------
# 5. Empty product → score is the minimum possible (0 filled fields)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmptyProductBaseline:
    """Pin "bare Motor / Drive has zero filled spec fields" — scoped
    to the two product classes whose Pydantic field defaults are all
    ``None``. Other classes (Gearhead, RobotArm, Contactor) ship
    with non-None defaults like ``gear_type='helical planetary'`` or
    ``degrees_of_freedom=6`` that pre-populate the spec column. That's
    a fact about those models, not a quality-bug; tighten the
    property scope rather than widen it to chase those defaults.
    """

    @given(cls=st.sampled_from([Motor, Drive]))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_bare_product_has_zero_filled(self, cls: Type[ProductBase]) -> None:
        product = cls(
            product_name="Test",
            manufacturer=MFG,
            product_type=_product_type_for(cls),
        )
        score, filled, total, missing = score_product(product)
        assert filled == 0
        assert len(missing) == total
        if total > 0:
            assert score == 0.0
        else:
            assert score == 1.0


# ---------------------------------------------------------------------------
# 6. Placeholder strings count as missing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlaceholderCountsAsMissing:
    """Pin the contract that PLACEHOLDER_STRINGS values are NOT
    counted as populated. A Motor with `series="N/A"` should have
    `series` in the missing list, not the filled count.

    `series` is one of the few Motor string fields that flows
    through ``Optional[str]`` rather than the typed-alias coercer,
    so the placeholder lands on the model verbatim and quality
    scoring is what catches it.
    """

    @given(placeholder=_placeholder_string())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_series_set_to_placeholder_is_missing(self, placeholder: str) -> None:
        motor = Motor(
            product_name="Test",
            manufacturer=MFG,
            product_type="motor",
            part_number="MTR-001",
            series=placeholder,
        )
        # The model preserves the string as-is on `series`; the
        # quality layer is what re-classifies it as missing.
        assert motor.series == placeholder
        _, _, _, missing = score_product(motor)
        assert "series" in missing, (
            f"placeholder series={placeholder!r} should count as missing"
        )


# ---------------------------------------------------------------------------
# 7. filter_products partition contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFilterProductsPartition:
    @given(
        products=st.lists(
            st.one_of(_empty_product(), _motor_with_random_spec_fields()),
            min_size=0,
            max_size=8,
        ),
        threshold=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_passed_plus_rejected_equals_input(
        self, products: list[ProductBase], threshold: float
    ) -> None:
        passed, rejected = filter_products(products, min_quality=threshold)
        # No duplicates across buckets; identity-based check so we
        # don't get confused by structurally-equal product objects.
        passed_ids = {id(p) for p in passed}
        rejected_ids = {id(p) for p in rejected}
        assert not (passed_ids & rejected_ids)
        assert passed_ids | rejected_ids == {id(p) for p in products}
        assert len(passed) + len(rejected) == len(products)

    @given(
        products=st.lists(
            st.one_of(_empty_product(), _motor_with_random_spec_fields()),
            min_size=0,
            max_size=8,
        ),
        threshold=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_partition_preserves_order_within_each_bucket(
        self, products: list[ProductBase], threshold: float
    ) -> None:
        passed, rejected = filter_products(products, min_quality=threshold)
        # The relative order of items within each bucket must match
        # the input order. (id() comparison again to avoid relying
        # on product __eq__.)
        passed_order = [id(p) for p in passed]
        rejected_order = [id(p) for p in rejected]
        input_passed_order = [id(p) for p in products if id(p) in set(passed_order)]
        input_rejected_order = [id(p) for p in products if id(p) in set(rejected_order)]
        assert passed_order == input_passed_order
        assert rejected_order == input_rejected_order

    @given(
        products=st.lists(
            st.one_of(_empty_product(), _motor_with_random_spec_fields()),
            min_size=0,
            max_size=8,
        ),
        threshold=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_bucket_matches_score_vs_threshold(
        self, products: list[ProductBase], threshold: float
    ) -> None:
        passed, rejected = filter_products(products, min_quality=threshold)
        for p in passed:
            score, _, _, _ = score_product(p)
            assert score >= threshold, (
                f"product in passed bucket has score {score} < {threshold}"
            )
        for p in rejected:
            score, _, _, _ = score_product(p)
            assert score < threshold, (
                f"product in rejected bucket has score {score} >= {threshold}"
            )


# ---------------------------------------------------------------------------
# 8. spec_fields_for_model invariants across all product types
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSpecFieldsForModel:
    @given(cls=st.sampled_from(PRODUCT_CLASSES))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_never_includes_meta_fields(self, cls: Type[ProductBase]) -> None:
        fields = spec_fields_for_model(cls)
        assert not (set(fields) & _META_FIELDS)

    @given(cls=st.sampled_from(PRODUCT_CLASSES))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_no_duplicates(self, cls: Type[ProductBase]) -> None:
        fields = spec_fields_for_model(cls)
        assert len(fields) == len(set(fields))

    @given(cls=st.sampled_from(PRODUCT_CLASSES))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_deterministic(self, cls: Type[ProductBase]) -> None:
        # Two consecutive calls must return the same list — both
        # contents and order. Drift here would let two halves of
        # the codebase iterate the spec fields in different orders
        # and quietly disagree about completeness.
        assert spec_fields_for_model(cls) == spec_fields_for_model(cls)

    @given(cls=st.sampled_from(PRODUCT_CLASSES))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_returns_subset_of_model_fields(self, cls: Type[ProductBase]) -> None:
        all_fields = set(cls.model_fields.keys())
        spec_fields = set(spec_fields_for_model(cls))
        assert spec_fields <= all_fields


# ---------------------------------------------------------------------------
# 9. Default threshold sanity — bare Motor / Drive always fail
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDefaultThreshold:
    """Pin "bare Motor fails the default 25% threshold" — the core
    quality gate the pipeline relies on to keep no-data products out
    of DynamoDB. Scoped to Motor + Drive because models like RobotArm
    ship with substantial Pydantic field defaults
    (``degrees_of_freedom=6``, ``ip_rating=54``, etc.) that
    pre-populate spec fields and lift the bare-default score above
    25%. That's a property of those models, not a quality-gate bug —
    document it here so the next reader doesn't try to generalise
    the rule.
    """

    @given(cls=st.sampled_from([Motor, Drive]))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_bare_motor_or_drive_rejected_at_default_threshold(
        self, cls: Type[ProductBase]
    ) -> None:
        product = cls(
            product_name="Test",
            manufacturer=MFG,
            product_type=_product_type_for(cls),
        )
        passed, rejected = filter_products([product], min_quality=DEFAULT_MIN_QUALITY)
        assert len(rejected) == 1
        assert len(passed) == 0
