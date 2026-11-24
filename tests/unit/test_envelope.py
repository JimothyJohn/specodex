"""Physical envelope contract: ProductBase.dimensions / weight / max_dimension.

The envelope is approximate by design (bounding box, not a mating
dimension) but it must be *comparable*: every axis in mm, weight in kg,
garbage degraded to None rather than stored or raised.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from specodex.models.drive import Drive
from specodex.models.gearhead import Gearhead
from specodex.models.motor import Motor

MFG = "Acme"


def _motor(**kw) -> Motor:
    return Motor(product_name="M", manufacturer=MFG, **kw)


@pytest.mark.unit
class TestDimensionsShapes:
    def test_new_per_axis_shape(self) -> None:
        m = _motor(
            dimensions={
                "length": {"value": 120, "unit": "mm"},
                "width": {"value": 60, "unit": "mm"},
                "height": {"value": 60, "unit": "mm"},
            }
        )
        assert m.model_dump(mode="json")["dimensions"] == {
            "length": {"value": 120.0, "unit": "mm"},
            "width": {"value": 60.0, "unit": "mm"},
            "height": {"value": 60.0, "unit": "mm"},
        }
        assert m.max_dimension.model_dump() == {"value": 120.0, "unit": "mm"}

    @pytest.mark.parametrize(
        "unit,expected",
        [
            ("in", 522.5),
            ("inch", 522.5),
            ("inches", 522.5),
            ("in.", 522.5),
            ("mm", 20.57),
            ("cm", 205.7),
        ],
    )
    def test_legacy_shape_every_stored_unit_spelling(self, unit, expected) -> None:
        # The unit spellings actually present in products-dev on 2026-11-24.
        m = _motor(dimensions={"length": 20.57, "unit": unit})
        assert m.dimensions.length.value == expected
        assert m.dimensions.length.unit == "mm"
        assert m.max_dimension.value == expected

    def test_legacy_decimal_leaves_from_dynamodb(self) -> None:
        d = Drive(
            product_name="D",
            manufacturer=MFG,
            dimensions={
                "width": Decimal("486"),
                "length": Decimal("371"),
                "height": Decimal("1035"),
                "unit": "mm",
            },
        )
        assert d.max_dimension.value == 1035.0

    def test_legacy_numeric_strings(self) -> None:
        d = Drive(
            product_name="D",
            manufacturer=MFG,
            dimensions={"width": "56.39", "length": "469.6", "unit": "mm"},
        )
        assert d.dimensions.width.value == 56.39
        assert d.max_dimension.value == 469.6

    def test_per_axis_mixed_units(self) -> None:
        m = _motor(
            dimensions={
                "length": {"value": 5, "unit": "in"},
                "width": "60 mm",
            }
        )
        assert m.dimensions.length.value == 127.0
        assert m.dimensions.width.value == 60.0
        assert m.max_dimension.value == 127.0

    def test_shape_roundtrips_through_model_dump(self) -> None:
        m = _motor(dimensions={"length": 3.0, "width": 2.0, "unit": "in"})
        again = Motor.model_validate(m.model_dump(mode="json"))
        assert again.dimensions == m.dimensions
        assert again.max_dimension == m.max_dimension


@pytest.mark.unit
class TestDimensionsRejects:
    @pytest.mark.parametrize(
        "dims",
        [
            {"unit": "mm"},  # 653 such rows on dev
            {},
            {"length": None, "width": None, "height": None, "unit": "mm"},
            {"length": {}, "width": {"value": None, "unit": None}},
            {"length": "N/A", "unit": "mm"},
            {"length": -5, "unit": "mm"},
            {"length": 0, "unit": "mm"},
            {"length": float("nan"), "unit": "mm"},
            {"length": float("inf"), "unit": "mm"},
            {"length": 12, "unit": "kg"},  # wrong family
            {"length": 12, "unit": "furlong"},
            {"length": 30_000_000, "unit": "mm"},  # part number read as mm
            {"length": True, "unit": "mm"},
        ],
    )
    def test_garbage_degrades_to_none(self, dims) -> None:
        m = _motor(dimensions=dims)
        assert m.dimensions is None
        assert m.max_dimension is None

    def test_one_bad_axis_keeps_the_good_ones(self) -> None:
        m = _motor(
            dimensions={"length": 100, "width": -1, "height": "abc", "unit": "mm"}
        )
        assert m.dimensions.length.value == 100.0
        assert m.dimensions.width is None
        assert m.dimensions.height is None
        assert m.max_dimension.value == 100.0

    def test_non_dict_dimensions_does_not_kill_row(self) -> None:
        # A string where an object belongs is an LLM shape error; the row
        # must survive with the field dropped, not raise.
        for junk in ("120 x 60 x 60 mm", 42, ["120", "60"], True):
            m = _motor(dimensions=junk)
            assert m.dimensions is None
            assert m.max_dimension is None


@pytest.mark.unit
class TestMaxDimension:
    def test_stale_stored_value_is_recomputed(self) -> None:
        m = _motor(
            dimensions={"length": 100, "unit": "mm"},
            max_dimension={"value": 9999, "unit": "mm"},
        )
        assert m.max_dimension.value == 100.0

    def test_stored_value_without_dimensions_is_dropped(self) -> None:
        m = _motor(max_dimension={"value": 50, "unit": "mm"})
        assert m.max_dimension is None

    def test_applies_to_every_product_type(self) -> None:
        g = Gearhead(
            product_name="G",
            manufacturer=MFG,
            dimensions={"length": 205, "width": 120, "height": 120, "unit": "mm"},
        )
        assert g.max_dimension.value == 205.0


@pytest.mark.unit
class TestWeight:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ({"value": 1.6, "unit": "kg"}, 1.6),
            ({"value": 900, "unit": "g"}, 0.9),
            ({"value": 10, "unit": "lb"}, 4.536),
            ({"value": 10, "unit": "lbs"}, 4.536),
            ({"value": 16, "unit": "oz"}, 0.4536),
            ({"value": 2, "unit": "Kg"}, 2.0),
            ("1.6;kg", 1.6),
            ("3.5 lb", 1.588),
        ],
    )
    def test_normalised_to_kg(self, raw, expected) -> None:
        m = _motor(weight=raw)
        assert m.weight.unit == "kg"
        assert m.weight.value == expected

    @pytest.mark.parametrize(
        "raw",
        [
            {"value": -1, "unit": "kg"},
            {"value": 0, "unit": "kg"},
            {"value": 5, "unit": "mm"},
            {"value": 1e9, "unit": "kg"},
            "heavy",
            "N/A",
            {},
        ],
    )
    def test_garbage_degrades_to_none(self, raw) -> None:
        assert _motor(weight=raw).weight is None
