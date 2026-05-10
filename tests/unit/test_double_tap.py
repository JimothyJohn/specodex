"""Tests for the double-tap verifier loop."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from specodex.double_tap import (
    DoubleTapResult,
    FieldProbe,
    Probe,
    extract_with_recovery,
    verify,
)
from specodex.double_tap.captions import (
    captions_for,
    common_fields_for,
)
from specodex.double_tap.prompt import build_priming_block
from specodex.double_tap.verifier import _encoder_is_ambiguous
from specodex.models.drive import Drive
from specodex.models.encoder import EncoderFeedback
from specodex.models.motor import Motor


# ---------------------------------------------------------------------------
# Captions table
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCaptions:
    def test_motor_rotor_inertia_has_synonyms(self) -> None:
        caps = captions_for("motor", "rotor_inertia")
        assert "Rotor inertia" in caps
        assert "kg·cm²" in caps

    def test_unknown_field_returns_empty(self) -> None:
        assert captions_for("motor", "no_such_field") == []

    def test_unknown_product_type_returns_empty(self) -> None:
        assert captions_for("widget", "rated_torque") == []

    def test_drive_common_fields_subset_of_known(self) -> None:
        common = common_fields_for("drive")
        assert "rated_current" in common
        assert "input_voltage" in common


# ---------------------------------------------------------------------------
# Verifier rules
# ---------------------------------------------------------------------------


def _motor(**over) -> Motor:
    """Minimal motor with all common fields populated by default — opt
    individual fields out by passing ``rated_torque=None`` etc."""
    defaults = dict(
        product_name="TM",
        manufacturer="X",
        part_number="P",
        rated_voltage="200-240;V",
        rated_current="5;A",
        rated_torque="3;Nm",
        rated_speed="3000;rpm",
        rated_power="1000;W",
        rotor_inertia="2;kg·cm²",
        encoder_feedback_support="EnDat 2.2",
        type="ac servo",
    )
    defaults.update(over)
    return Motor(**defaults)


def _drive(**over) -> Drive:
    defaults = dict(
        product_name="TD",
        manufacturer="X",
        part_number="P",
        input_voltage="200-240;V",
        rated_current="10;A",
        rated_power="2000;W",
        encoder_feedback_support=["endat_2_2"],
    )
    defaults.update(over)
    return Drive(**defaults)


@pytest.mark.unit
class TestEncoderAmbiguityDetection:
    def test_clean_encoder_not_ambiguous(self) -> None:
        e = EncoderFeedback(device="absolute_optical", protocol="endat_2_2")
        assert _encoder_is_ambiguous(e) is False

    def test_unknown_device_is_ambiguous(self) -> None:
        e = EncoderFeedback(device="unknown", raw="unparseable garbage")
        assert _encoder_is_ambiguous(e) is True

    def test_drive_protocol_list_with_unknown_is_ambiguous(self) -> None:
        assert _encoder_is_ambiguous(["endat_2_2", "unknown"]) is True

    def test_drive_protocol_list_clean_is_not_ambiguous(self) -> None:
        assert _encoder_is_ambiguous(["endat_2_2", "biss_c"]) is False

    def test_none_value_is_not_ambiguous(self) -> None:
        # Missing-encoder is a separate "missing" probe handled by the
        # common-fields rule, not the ambiguity rule.
        assert _encoder_is_ambiguous(None) is False


@pytest.mark.unit
class TestVerifyMotor:
    def test_clean_motor_no_probe(self) -> None:
        probes = verify([_motor()])
        assert len(probes) == 1
        assert probes[0].empty()

    def test_motor_missing_rated_torque_fires(self) -> None:
        probes = verify([_motor(rated_torque=None)])
        assert probes[0].fires()
        names = probes[0].field_names()
        assert "rated_torque" in names

    def test_motor_with_unknown_encoder_fires_on_encoder(self) -> None:
        m = _motor(encoder_feedback_support="some weird vendor lingo")
        probes = verify([m])
        # The shim couldn't fully resolve → encoder probe should fire.
        assert probes[0].fires()
        assert "encoder_feedback_support" in probes[0].field_names()
        # And the FieldProbe must carry encoder captions for the prompt.
        encoder_probe = next(
            f for f in probes[0].fields if f.field == "encoder_feedback_support"
        )
        assert encoder_probe.reason == "encoder_ambiguous"
        assert "EnDat" in encoder_probe.captions

    def test_motor_with_clean_encoder_no_encoder_probe(self) -> None:
        m = _motor(encoder_feedback_support="EnDat 2.2 multi-turn 26-bit")
        probes = verify([m])
        assert probes[0].empty()


@pytest.mark.unit
class TestVerifyDrive:
    def test_drive_with_unknown_protocol_in_list_fires(self) -> None:
        d = _drive(encoder_feedback_support=["xyzzy", "biss_c"])
        # Shim maps "xyzzy" → "unknown" sentinel. Verifier should fire.
        probes = verify([d])
        assert probes[0].fires()
        assert "encoder_feedback_support" in probes[0].field_names()

    def test_drive_missing_rated_current_fires(self) -> None:
        probes = verify([_drive(rated_current=None)])
        names = probes[0].field_names()
        assert "rated_current" in names


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPromptBuilder:
    def test_empty_probe_returns_empty_string(self) -> None:
        probe = Probe(product_type="motor", part_number="P")
        assert build_priming_block(probe, {}) == ""

    def test_priming_block_contains_field_names_and_captions(self) -> None:
        probe = Probe(
            product_type="motor",
            part_number="P",
            fields=[
                FieldProbe(
                    field="rated_torque",
                    reason="missing",
                    captions=("Rated torque", "T_N", "Nm"),
                    primer="rated_torque was not extracted on the first pass.",
                )
            ],
        )
        out = build_priming_block(probe, {"part_number": "P"})
        assert "rated_torque" in out
        assert "Rated torque" in out
        assert "T_N" in out
        assert '"part_number": "P"' in out  # first-pass JSON included
        assert "NEEDS A SECOND LOOK" in out


# ---------------------------------------------------------------------------
# Runner — LLM call mocked
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunner:
    def _patch_llm(self, return_values: list[list[Motor]]):
        """Patch call_llm_and_parse to return canned values, in order.

        Each call pops the next list from ``return_values`` and (if the
        caller passed a tokens dict) bumps it by 100/50 so token math
        is non-trivial in assertions.
        """
        calls: list[dict] = []

        def fake_call(*args, **kwargs):
            tokens = kwargs.get("tokens")
            if tokens is not None:
                tokens["input"] = tokens.get("input", 0) + 100
                tokens["output"] = tokens.get("output", 0) + 50
            calls.append({"prompt_prefix": kwargs.get("prompt_prefix")})
            return return_values.pop(0)

        return patch(
            "specodex.double_tap.runner.call_llm_and_parse", side_effect=fake_call
        ), calls

    def test_clean_first_pass_skips_second(self) -> None:
        """No probes fire → no second LLM call."""
        clean = _motor()
        patcher, calls = self._patch_llm([[clean]])
        with patcher:
            result = extract_with_recovery(b"pdf", "key", "motor", {}, "pdf")
        assert isinstance(result, DoubleTapResult)
        assert len(result.products) == 1
        assert result.did_second_pass is False
        assert result.probes_fired == 0
        assert len(calls) == 1
        assert result.first_pass_tokens == (100, 50)
        assert result.second_pass_tokens == (0, 0)

    def test_dirty_first_pass_triggers_second(self) -> None:
        dirty_first = _motor(rated_torque=None)
        recovered = _motor(rated_torque="3;Nm")
        patcher, calls = self._patch_llm([[dirty_first], [recovered]])
        with patcher:
            result = extract_with_recovery(b"pdf", "key", "motor", {}, "pdf")
        # Two LLM calls (first + primed second).
        assert len(calls) == 2
        # Second call must carry the priming block.
        assert calls[1]["prompt_prefix"] is not None
        assert "rated_torque" in calls[1]["prompt_prefix"]
        # The merged product has the second-pass torque.
        assert result.products[0].rated_torque is not None
        assert "rated_torque" in result.fields_recovered
        assert result.did_second_pass is True

    def test_second_pass_does_not_regress_unprobed_fields(self) -> None:
        """If second-pass returns a different value for an UNprobed field,
        we keep the first-pass value. The merge is conservative on purpose."""
        first = _motor(rated_torque=None, rated_speed="3000;rpm")
        # Second pass returns DIFFERENT rated_speed — must be ignored
        # because the speed field wasn't probed.
        second = _motor(rated_torque="3;Nm", rated_speed="6000;rpm")
        patcher, calls = self._patch_llm([[first], [second]])
        with patcher:
            result = extract_with_recovery(b"pdf", "key", "motor", {}, "pdf")
        merged = result.products[0]
        assert merged.rated_torque is not None  # recovered
        # First-pass speed must be preserved despite second-pass diff.
        assert merged.rated_speed.value == 3000

    def test_second_pass_failure_falls_back_to_first(self) -> None:
        first = _motor(rated_torque=None)

        def fake_call(*args, **kwargs):
            tokens = kwargs.get("tokens")
            if tokens is not None:
                tokens["input"] = tokens.get("input", 0) + 100
                tokens["output"] = tokens.get("output", 0) + 50
            if kwargs.get("prompt_prefix"):
                raise RuntimeError("Gemini meltdown")
            return [first]

        with patch(
            "specodex.double_tap.runner.call_llm_and_parse", side_effect=fake_call
        ):
            result = extract_with_recovery(b"pdf", "key", "motor", {}, "pdf")
        # Got back the first-pass product unchanged.
        assert result.products == [first]
        # And the unresolved list records what we failed to recover.
        assert any("rated_torque" in u for u in result.unresolved)

    def test_empty_first_pass_short_circuits(self) -> None:
        patcher, calls = self._patch_llm([[]])
        with patcher:
            result = extract_with_recovery(b"pdf", "key", "motor", {}, "pdf")
        assert result.products == []
        assert len(calls) == 1
        assert result.did_second_pass is False
