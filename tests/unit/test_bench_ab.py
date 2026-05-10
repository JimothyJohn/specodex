"""Tests for the A/B comparison harness in cli/bench_ab.py."""

from __future__ import annotations

import pytest

from cli.bench_ab import (
    ABComparison,
    WORTH_IT_THRESHOLD_PP_PER_100PCT_TOKENS,
    compare_results,
    format_ab_table,
)


def _fixture_result(
    slug: str,
    *,
    recall: float | None,
    precision: float | None,
    input_tokens: int,
    output_tokens: int,
    cost: float,
    extra_telemetry: dict | None = None,
) -> dict:
    extraction = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
    }
    if extra_telemetry:
        extraction.update(extra_telemetry)
    return {
        "slug": slug,
        "extraction": extraction,
        "quality": {"recall": recall, "precision": precision},
    }


@pytest.mark.unit
class TestCompareResults:
    def test_clean_first_pass_zero_token_growth(self) -> None:
        # Single and double-tap identical → token delta 0%, worth_it=True.
        single = _fixture_result(
            "x",
            recall=0.5,
            precision=0.7,
            input_tokens=1000,
            output_tokens=200,
            cost=0.01,
        )
        double = _fixture_result(
            "x",
            recall=0.5,
            precision=0.7,
            input_tokens=1000,
            output_tokens=200,
            cost=0.01,
            extra_telemetry={"double_tap_probes_fired": 0},
        )
        c = compare_results(single, double)
        assert c.delta_recall_pp == 0.0
        assert c.delta_tokens_pct == 0.0
        assert c.worth_it is True

    def test_big_quality_lift_modest_token_growth_is_worth_it(self) -> None:
        # +29pp recall for +80% tokens — well above the 5pp/100% threshold.
        single = _fixture_result(
            "y",
            recall=0.42,
            precision=0.80,
            input_tokens=1000,
            output_tokens=500,
            cost=0.01,
        )
        double = _fixture_result(
            "y",
            recall=0.71,
            precision=0.82,
            input_tokens=1500,  # +50%
            output_tokens=1200,  # +140%
            cost=0.018,
            extra_telemetry={
                "double_tap_probes_fired": 3,
                "double_tap_fields_recovered": ["rated_torque", "rotor_inertia"],
            },
        )
        c = compare_results(single, double)
        assert c.delta_recall_pp == pytest.approx(29.0, abs=0.1)
        assert c.delta_tokens_pct == pytest.approx(80.0, abs=0.1)
        assert c.worth_it is True

    def test_minimal_lift_high_token_growth_is_not_worth_it(self) -> None:
        # +1pp recall for +110% tokens — below the threshold.
        single = _fixture_result(
            "z",
            recall=0.81,
            precision=0.90,
            input_tokens=1000,
            output_tokens=200,
            cost=0.005,
        )
        double = _fixture_result(
            "z",
            recall=0.82,
            precision=0.90,
            input_tokens=1500,
            output_tokens=1020,  # roughly +110% on tokens combined
            cost=0.01,
            extra_telemetry={"double_tap_probes_fired": 1},
        )
        c = compare_results(single, double)
        assert c.delta_recall_pp == pytest.approx(1.0, abs=0.1)
        assert c.delta_tokens_pct > 100
        assert c.worth_it is False

    def test_threshold_is_documented_constant(self) -> None:
        # Sanity-check the constant the test math assumes.
        assert WORTH_IT_THRESHOLD_PP_PER_100PCT_TOKENS == 5.0

    def test_missing_quality_returns_none_for_worth_it(self) -> None:
        single = _fixture_result(
            "no-truth",
            recall=None,
            precision=None,
            input_tokens=1000,
            output_tokens=200,
            cost=0.005,
        )
        double = _fixture_result(
            "no-truth",
            recall=None,
            precision=None,
            input_tokens=2000,
            output_tokens=400,
            cost=0.01,
        )
        c = compare_results(single, double)
        assert c.worth_it is None

    def test_format_table_includes_each_slug(self) -> None:
        comps = [
            ABComparison(
                slug="alpha",
                single_recall=0.4,
                double_recall=0.7,
                single_precision=0.8,
                double_precision=0.85,
                single_tokens=1000,
                double_tokens=1800,
                single_cost_usd=0.005,
                double_cost_usd=0.009,
                probes_fired=2,
                fields_recovered=["x"],
                fields_corrected=[],
            ),
            ABComparison(
                slug="beta",
                single_recall=0.9,
                double_recall=0.91,
                single_precision=0.95,
                double_precision=0.95,
                single_tokens=500,
                double_tokens=1100,
                single_cost_usd=0.002,
                double_cost_usd=0.005,
                probes_fired=1,
                fields_recovered=[],
                fields_corrected=[],
            ),
        ]
        out = format_ab_table(comps)
        assert "alpha" in out
        assert "beta" in out
        assert "Δ Recall" in out
        assert "Worth?" in out
