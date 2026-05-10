"""A/B comparison harness for single-pass vs double-tap extraction.

The output table is the gate for whether double-tap ships in production
(see ``todo/DOUBLE_TAP.md`` Part 4). Threshold: **+5pp recall OR +5pp
precision per +100% tokens**. Below that, the second pass is a tax we
don't want.

This module is testable in isolation — ``compare_results`` works on the
already-bench'd dicts so unit tests can build fixture inputs without
running Gemini.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


# Threshold the bench gate uses to call double-tap "worth it" per fixture.
# Encodes the rule from DOUBLE_TAP.md Part 4: +5pp recall OR +5pp
# precision per +100% tokens. Inverted: a fixture is "worth it" if
# (Δrecall_pp + Δprecision_pp) / token_growth_factor >= 5.
WORTH_IT_THRESHOLD_PP_PER_100PCT_TOKENS = 5.0


@dataclass(frozen=True)
class ABComparison:
    """One fixture's single-pass vs double-tap delta."""

    slug: str
    single_recall: Optional[float]
    double_recall: Optional[float]
    single_precision: Optional[float]
    double_precision: Optional[float]
    single_tokens: int
    double_tokens: int
    single_cost_usd: float
    double_cost_usd: float
    probes_fired: int
    fields_recovered: list[str]
    fields_corrected: list[str]

    @property
    def delta_recall_pp(self) -> Optional[float]:
        if self.single_recall is None or self.double_recall is None:
            return None
        return round((self.double_recall - self.single_recall) * 100, 2)

    @property
    def delta_precision_pp(self) -> Optional[float]:
        if self.single_precision is None or self.double_precision is None:
            return None
        return round((self.double_precision - self.single_precision) * 100, 2)

    @property
    def delta_tokens_pct(self) -> Optional[float]:
        if self.single_tokens == 0:
            return None
        return round((self.double_tokens / self.single_tokens - 1.0) * 100, 1)

    @property
    def delta_cost_usd(self) -> float:
        return round(self.double_cost_usd - self.single_cost_usd, 6)

    @property
    def worth_it(self) -> Optional[bool]:
        """True when the quality lift justifies the token cost.

        Encodes ``WORTH_IT_THRESHOLD_PP_PER_100PCT_TOKENS`` — the per-
        fixture gate from DOUBLE_TAP.md Part 4. ``None`` when we lack
        the data to judge (no ground-truth, no token diff).
        """
        if self.delta_tokens_pct is None or self.delta_tokens_pct <= 0:
            # No extra tokens spent (clean first pass) — by definition worth it.
            return True
        if self.delta_recall_pp is None and self.delta_precision_pp is None:
            return None
        d_quality = (self.delta_recall_pp or 0.0) + (self.delta_precision_pp or 0.0)
        ratio = d_quality / (self.delta_tokens_pct / 100.0)
        return ratio >= WORTH_IT_THRESHOLD_PP_PER_100PCT_TOKENS


def compare_results(single: dict[str, Any], double: dict[str, Any]) -> ABComparison:
    """Build an ABComparison from one fixture's two run results.

    ``single`` and ``double`` are the per-fixture dicts emitted by
    ``cli.bench.run`` — one with ``--double-tap`` off, one with it on.
    """
    s_extract = single.get("extraction", {})
    d_extract = double.get("extraction", {})
    s_quality = single.get("quality", {})
    d_quality = double.get("quality", {})

    return ABComparison(
        slug=single.get("slug") or double.get("slug") or "?",
        single_recall=s_quality.get("recall"),
        double_recall=d_quality.get("recall"),
        single_precision=s_quality.get("precision"),
        double_precision=d_quality.get("precision"),
        single_tokens=int(s_extract.get("input_tokens", 0))
        + int(s_extract.get("output_tokens", 0)),
        double_tokens=int(d_extract.get("input_tokens", 0))
        + int(d_extract.get("output_tokens", 0)),
        single_cost_usd=float(s_extract.get("cost_usd") or 0.0),
        double_cost_usd=float(d_extract.get("cost_usd") or 0.0),
        probes_fired=int(d_extract.get("double_tap_probes_fired", 0)),
        fields_recovered=list(d_extract.get("double_tap_fields_recovered", [])),
        fields_corrected=list(d_extract.get("double_tap_fields_corrected", [])),
    )


def format_ab_table(comparisons: list[ABComparison]) -> str:
    """Render the A/B table to a string. Wide on purpose — not for narrow terminals."""
    header = (
        f"{'Fixture':<28} {'Δ Recall':>10} {'Δ Prec':>10} "
        f"{'Δ Tokens':>10} {'Δ Cost':>10} {'Probes':>7} {'Worth?':>7}"
    )
    lines = [header, "-" * len(header)]
    for c in comparisons:
        recall = f"{c.delta_recall_pp:+.1f}pp" if c.delta_recall_pp is not None else "—"
        precision = (
            f"{c.delta_precision_pp:+.1f}pp"
            if c.delta_precision_pp is not None
            else "—"
        )
        tokens = (
            f"{c.delta_tokens_pct:+.1f}%" if c.delta_tokens_pct is not None else "—"
        )
        cost = f"${c.delta_cost_usd:+.4f}"
        worth = "✓" if c.worth_it else ("✗" if c.worth_it is False else "—")
        lines.append(
            f"{c.slug[:27]:<28} {recall:>10} {precision:>10} "
            f"{tokens:>10} {cost:>10} {c.probes_fired:>7d} {worth:>7}"
        )
    return "\n".join(lines)
