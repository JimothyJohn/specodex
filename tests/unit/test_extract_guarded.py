"""Tests for specodex.extract.guarded_extract.

The LLM call itself is monkeypatched — these tests pin the harness
contract (retry, guard, dedup, fabrication scrub, early stop), not
Gemini behaviour. The models flowing through are real Gearhead
instances so the scrub exercises actual Pydantic fields.
"""

from __future__ import annotations

import pytest

import specodex.extract as extract
from specodex.extract import guarded_extract
from specodex.models.common import ValueUnit
from specodex.models.gearhead import Gearhead


def _gh(pn: str, **kw) -> Gearhead:
    return Gearhead(
        part_number=pn,
        product_name="Test Series",
        manufacturer="TestCo",
        **kw,
    )


def _patch_llm(monkeypatch, batches):
    """call_llm_and_parse returns successive entries of ``batches``.

    An entry that is an Exception instance is raised instead. Returns
    the list of recorded call counts for assertions.
    """
    calls = []

    def fake(doc_data, api_key, product_type, context, content_type, **kw):
        calls.append(1)
        batch = batches[min(len(calls) - 1, len(batches) - 1)]
        if isinstance(batch, Exception):
            raise batch
        return list(batch)

    monkeypatch.setattr(extract, "call_llm_and_parse", fake)
    return calls


ARGS = (b"%PDF", "key", "gearhead", {"product_name": "Test Series"}, "pdf")


@pytest.mark.unit
class TestGuardedExtract:
    def test_dedups_across_attempts_and_stops_at_expected(self, monkeypatch):
        calls = _patch_llm(
            monkeypatch,
            [
                [_gh("A1"), _gh("A1"), _gh("A2")],  # dup within attempt
                [_gh("A2"), _gh("A3")],  # dup across attempts
            ],
        )
        out = guarded_extract(*ARGS, expected=3, attempts=5)
        assert [m.part_number for m in out] == ["A1", "A2", "A3"]
        assert len(calls) == 2  # stopped as soon as expected was met

    def test_default_accept_drops_blank_part_numbers(self, monkeypatch):
        _patch_llm(monkeypatch, [[_gh("  "), _gh(None), _gh("A1")]])
        out = guarded_extract(*ARGS)
        assert [m.part_number for m in out] == ["A1"]

    def test_custom_accept_guard(self, monkeypatch):
        good = _gh("A1", max_continuous_torque=ValueUnit(value=10, unit="Nm"))
        _patch_llm(monkeypatch, [[good, _gh("A2")]])
        out = guarded_extract(
            *ARGS,
            accept=lambda m: m.max_continuous_torque is not None,
        )
        assert [m.part_number for m in out] == ["A1"]

    def test_allowed_fields_scrubs_fabrications_keeps_whitelist(self, monkeypatch):
        row = _gh(
            "A1",
            gear_ratio=10.0,
            max_continuous_torque=ValueUnit(value=10, unit="Nm"),
            ip_rating=54,  # classic fabrication
            max_axial_load=ValueUnit(value=121, unit="N"),  # copied from OHL
        )
        _patch_llm(monkeypatch, [[row]])
        out = guarded_extract(
            *ARGS,
            allowed_fields=["gear_ratio", "max_continuous_torque", "max_radial_load"],
        )
        m = out[0]
        assert m.gear_ratio == 10.0
        assert m.max_continuous_torque.value == 10
        assert m.ip_rating is None
        assert m.max_axial_load is None
        assert m.lubrication_type is None  # model default scrubbed too
        # base-model identity fields are never scrubbed
        assert m.manufacturer == "TestCo"
        assert m.part_number == "A1"

    def test_allowed_fields_typo_raises(self, monkeypatch):
        _patch_llm(monkeypatch, [[_gh("A1")]])
        with pytest.raises(ValueError, match="gear_ration"):
            guarded_extract(*ARGS, allowed_fields=["gear_ration"])

    def test_llm_error_spends_attempt_and_retries(self, monkeypatch):
        calls = _patch_llm(monkeypatch, [RuntimeError("503"), [_gh("A1")]])
        out = guarded_extract(*ARGS, attempts=3)
        assert [m.part_number for m in out] == ["A1"]
        assert len(calls) == 2

    def test_attempts_exhausted_returns_partial(self, monkeypatch):
        calls = _patch_llm(monkeypatch, [[_gh("A1")]])
        out = guarded_extract(*ARGS, expected=5, attempts=3)
        assert [m.part_number for m in out] == ["A1"]  # partial, no raise
        assert len(calls) == 3

    def test_all_attempts_error_returns_empty(self, monkeypatch):
        calls = _patch_llm(monkeypatch, [RuntimeError("boom")])
        out = guarded_extract(*ARGS, attempts=2)
        assert out == []
        assert len(calls) == 2

    def test_custom_key_dedup(self, monkeypatch):
        # Same code with two torque variants (adapter rows): key on
        # (pn, torque) keeps both, mirroring the Stober merge input.
        r1 = _gh("A1", max_continuous_torque=ValueUnit(value=10, unit="Nm"))
        r2 = _gh("A1", max_continuous_torque=ValueUnit(value=20, unit="Nm"))
        _patch_llm(monkeypatch, [[r1, r2, r1]])
        out = guarded_extract(
            *ARGS,
            key=lambda m: (m.part_number, m.max_continuous_torque.value),
            accept=lambda m: m.max_continuous_torque is not None,
            expected=2,
        )
        assert len(out) == 2
