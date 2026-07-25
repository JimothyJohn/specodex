"""Tests for specodex/models/commercial.py — the provenance invariants.

The whole point of these models is replicability: no figure without a
receipt. These tests pin the invariants the validators enforce.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from specodex.models.commercial import SourceCitation, SourcedFigure


def _web_citation(**overrides) -> dict:
    base = {
        "url": "https://shop.example.com/p/x100",
        "kind": "distributor",
        "retrieved_at": "2026-07-21T12:00:00Z",
        "excerpt": "X100 — $1,234.00",
    }
    base.update(overrides)
    return base


def _inference_citation(**overrides) -> dict:
    base = {
        "kind": "db_inference",
        "retrieved_at": "2026-07-21T12:00:00Z",
        "comparable_ids": ["id-a", "id-b", "id-c"],
        "method_version": "price-comps-v1",
    }
    base.update(overrides)
    return base


@pytest.mark.unit
class TestSourceCitationInvariants:
    def test_web_citation_valid(self) -> None:
        c = SourceCitation(**_web_citation())
        assert c.kind == "distributor"
        assert c.url is not None

    def test_web_citation_without_url_rejected(self) -> None:
        for kind in (
            "oem",
            "distributor",
            "aggregator",
            "price_book",
            "shopping",
            "article",
            "vendor_doc",
        ):
            with pytest.raises(ValidationError, match="url"):
                SourceCitation(**_web_citation(kind=kind, url=None))

    def test_inference_citation_valid(self) -> None:
        c = SourceCitation(**_inference_citation())
        assert c.url is None
        assert c.method_version == "price-comps-v1"

    def test_inference_without_comparables_rejected(self) -> None:
        with pytest.raises(ValidationError, match="replicable"):
            SourceCitation(**_inference_citation(comparable_ids=None))

    def test_inference_with_empty_comparables_rejected(self) -> None:
        with pytest.raises(ValidationError, match="replicable"):
            SourceCitation(**_inference_citation(comparable_ids=[]))

    def test_inference_without_method_version_rejected(self) -> None:
        with pytest.raises(ValidationError, match="replicable"):
            SourceCitation(**_inference_citation(method_version=None))

    def test_excerpt_length_capped(self) -> None:
        with pytest.raises(ValidationError):
            SourceCitation(**_web_citation(excerpt="x" * 281))


@pytest.mark.unit
class TestSourcedFigureInvariants:
    def test_valid_price_figure(self) -> None:
        fig = SourcedFigure(
            value={"value": 1234.0, "unit": "USD"},
            confidence="high",
            sources=[_web_citation()],
        )
        assert fig.value.value == 1234.0
        assert fig.value.unit == "USD"

    def test_valid_inferred_figure_with_range(self) -> None:
        fig = SourcedFigure(
            value={"value": 950.0, "unit": "USD"},
            confidence="inferred",
            observed_range={"min": 700, "max": 1300, "unit": "USD"},
            sources=[_inference_citation()],
        )
        assert fig.observed_range is not None
        assert fig.observed_range.min == 700

    def test_no_sources_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SourcedFigure(
                value={"value": 1234.0, "unit": "USD"},
                confidence="high",
                sources=[],
            )

    def test_lead_time_shape(self) -> None:
        fig = SourcedFigure(
            value={"value": 6, "unit": "weeks"},
            confidence="medium",
            sources=[
                _web_citation(
                    kind="vendor_doc",
                    excerpt="Standard products ship within 6 weeks.",
                )
            ],
        )
        assert fig.value.unit == "weeks"

    def test_serialization_round_trip(self) -> None:
        fig = SourcedFigure(
            value={"value": 950.0, "unit": "USD"},
            confidence="inferred",
            sources=[_inference_citation()],
        )
        dumped = fig.model_dump()
        assert dumped["value"] == {"value": 950.0, "unit": "USD"}
        restored = SourcedFigure.model_validate(dumped)
        assert restored == fig


@pytest.mark.unit
class TestProductWiring:
    def test_motor_accepts_estimates(self) -> None:
        from specodex.models.motor import Motor

        m = Motor(
            product_name="M1",
            product_type="motor",
            manufacturer="Acme",
            price_estimate={
                "value": {"value": 950.0, "unit": "USD"},
                "confidence": "inferred",
                "sources": [_inference_citation()],
            },
        )
        assert m.price_estimate is not None
        assert m.price_estimate.confidence == "inferred"

    def test_estimates_excluded_from_gemini_schema(self) -> None:
        """The LLM must never be asked to invent commercial estimates —
        they are computed with citation-backed provenance."""
        from specodex.models.llm_schema import to_gemini_schema
        from specodex.models.motor import Motor

        schema = to_gemini_schema(Motor)
        # Schema may be array-of-products; walk to the item properties.
        props = schema.get("items", schema).get("properties", {})
        assert "price_estimate" not in props
        assert "lead_time_estimate" not in props
        assert "msrp" in props  # listed price still rides along in extraction

    def test_estimates_survive_db_serialization(self) -> None:
        """Round-trip through the DynamoDB serializer (floats→Decimal)."""
        from unittest.mock import MagicMock, patch

        from specodex.models.motor import Motor

        m = Motor(
            product_name="M1",
            product_type="motor",
            manufacturer="Acme",
            price_estimate={
                "value": {"value": 950.0, "unit": "USD"},
                "confidence": "inferred",
                "sources": [_inference_citation()],
            },
        )
        with patch("specodex.db.dynamo.boto3") as mock_boto3:
            mock_table = MagicMock()
            mock_resource = MagicMock()
            mock_resource.Table.return_value = mock_table
            mock_boto3.resource.return_value = mock_resource
            from specodex.db.dynamo import DynamoDBClient

            client = DynamoDBClient(table_name="products")
            item = client._serialize_item(m)
        assert item["price_estimate"]["confidence"] == "inferred"
        restored = Motor.model_validate(item)
        assert restored.price_estimate is not None
        assert restored.price_estimate.value.value == 950.0
