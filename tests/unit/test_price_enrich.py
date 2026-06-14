"""Unit tests for cli.price_enrich glue logic.

The crawl/extract internals are covered by test_pricing.py; this file
pins the run-level contracts added by the 2026-06-11 salvage pass
(todo/PRICING.md Phase 3): full product-type coverage, the templated
part-number skip, and the SERP budget counter.
"""

from __future__ import annotations

import pytest

from cli.price_enrich import (
    PRODUCT_CLASSES,
    RunStats,
    _iter_candidates,
    is_templated_part_number,
)
from specodex.config import SCHEMA_CHOICES


class TestProductClasses:
    def test_every_schema_type_is_enrichable(self):
        # Regression: this was a hand-list of {drive, motor}, leaving
        # gearhead / robot_arm / electric_cylinder / linear_actuator /
        # contactor permanently at 0% msrp coverage.
        assert PRODUCT_CLASSES == dict(SCHEMA_CHOICES)
        assert {"gearhead", "robot_arm", "electric_cylinder"} <= set(PRODUCT_CLASSES)


class TestTemplatedPartNumber:
    @pytest.mark.parametrize(
        "pn",
        [
            "21G11*F960JNONNNNN",  # Allen-Bradley option template, seen live
            "ACS580-01-#-4",
            "SGMJV-?A?A??",
        ],
    )
    def test_templates_detected(self, pn):
        assert is_templated_part_number(pn)

    @pytest.mark.parametrize("pn", ["HG-KR43", "EM3546T", "VFD185C43C-HD", "R88M-1M"])
    def test_real_part_numbers_pass(self, pn):
        assert not is_templated_part_number(pn)


class TestSerpBudgetCounter:
    def test_no_key_means_no_serp_count(self, monkeypatch):
        # Regression: serp_calls incremented before serp_candidates()
        # checked for the key, so a key-less run reported serp=7 while
        # issuing zero queries.
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        stats = RunStats()
        cands = _iter_candidates(
            manufacturer="Acme",
            part_number="X100",
            use_serp=True,
            serp_budget_remaining=10,
            stats=stats,
        )
        assert stats.serp_calls == 0
        assert cands == _iter_candidates(
            manufacturer="Acme",
            part_number="X100",
            use_serp=False,
            serp_budget_remaining=0,
            stats=stats,
        )

    def test_budget_exhausted_skips_serp(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "test-key-not-real")
        stats = RunStats()
        _iter_candidates(
            manufacturer="Acme",
            part_number="X100",
            use_serp=True,
            serp_budget_remaining=0,
            stats=stats,
        )
        assert stats.serp_calls == 0


class TestTiersRestriction:
    def test_oem_only_drops_distributor_candidates(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "test-key-not-real")
        stats = RunStats()
        cands = _iter_candidates(
            manufacturer="Mitsubishi Electric",
            part_number="HG-KR43",
            use_serp=True,
            serp_budget_remaining=10,
            stats=stats,
            tiers=frozenset({"oem"}),
        )
        assert cands, "Mitsubishi OEM store candidates expected"
        assert all(c.source_type == "oem" for c in cands)
        # serp excluded by the tier filter → no budget burned
        assert stats.serp_calls == 0

    def test_default_none_means_all_tiers(self):
        stats = RunStats()
        cands = _iter_candidates(
            manufacturer="Mitsubishi Electric",
            part_number="HG-KR43",
            use_serp=False,
            serp_budget_remaining=0,
            stats=stats,
            tiers=None,
        )
        assert {c.source_type for c in cands} >= {"oem", "distributor"}
