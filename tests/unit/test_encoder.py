"""Tests for the structured EncoderFeedback model and free-text shim."""

from __future__ import annotations

import pytest

from specodex.models.drive import Drive
from specodex.models.encoder import (
    EncoderFeedback,
    coerce_protocol_string,
    feedback_subsumes,
    parse_encoder_freetext,
)
from specodex.models.motor import Motor


@pytest.mark.unit
class TestParseFreetext:
    def test_endat_2_2_with_resolution_and_multiturn(self) -> None:
        out = parse_encoder_freetext("EnDat 2.2 multi-turn 26-bit")
        assert out["protocol"] == "endat_2_2"
        assert out["device"] == "absolute_optical"
        assert out["mode"] == "absolute"
        assert out["multiturn"] is True
        assert out["bits_per_turn"] == 26
        assert out["raw"] == "EnDat 2.2 multi-turn 26-bit"

    def test_bare_endat_defaults_to_2_2(self) -> None:
        # Disambiguation rule 5 from the taxonomy doc.
        out = parse_encoder_freetext("EnDat")
        assert out["protocol"] == "endat_2_2"

    def test_endat_2_1_keeps_version(self) -> None:
        # The "endat 2.1" synonym must hit before the bare "endat" entry.
        out = parse_encoder_freetext("Heidenhain EnDat 2.1")
        assert out["protocol"] == "endat_2_1"

    def test_hiperface_dsl_distinct_from_classic(self) -> None:
        out = parse_encoder_freetext("Hiperface DSL one-cable")
        assert out["protocol"] == "hiperface_dsl"
        out2 = parse_encoder_freetext("Hiperface 8-wire")
        assert out2["protocol"] == "hiperface"

    def test_resolver_default_device(self) -> None:
        out = parse_encoder_freetext("Resolver")
        assert out["device"] == "resolver"
        assert out["protocol"] == "resolver_analog"

    def test_resolver_pole_pair_capture(self) -> None:
        out = parse_encoder_freetext("4-pole pair resolver")
        assert out["device"] == "resolver"
        assert out["resolver_pole_pairs"] == 4

    def test_incremental_ppr_defaults_to_quadrature_ttl(self) -> None:
        # Disambiguation rule 1 from the taxonomy doc.
        out = parse_encoder_freetext("Incremental 2500 ppr")
        assert out["device"] == "incremental_optical"
        assert out["mode"] == "incremental"
        assert out["pulses_per_rev"] == 2500
        assert out["protocol"] == "quadrature_ttl"

    def test_n_bit_absolute_does_not_guess_protocol(self) -> None:
        # Disambiguation rule 2 — "20-bit absolute" with no vendor must
        # not infer the protocol from bit count alone.
        out = parse_encoder_freetext("20-bit absolute encoder")
        assert out["device"] == "absolute_optical"
        assert out["bits_per_turn"] == 20
        assert "protocol" not in out

    def test_mitsubishi_j5_brand_shortcut(self) -> None:
        out = parse_encoder_freetext("MR-J5 26-bit batteryless multi-turn")
        assert out["protocol"] == "mitsubishi_j5"
        assert out["multiturn"] is True
        assert out["multiturn_battery_backed"] is False
        assert out["bits_per_turn"] == 26

    def test_yaskawa_sigma7_brand_shortcut(self) -> None:
        out = parse_encoder_freetext("Yaskawa Sigma-7 24-bit absolute")
        assert out["protocol"] == "yaskawa_sigma"
        assert out["bits_per_turn"] == 24

    def test_sensorless_maps_to_none_device(self) -> None:
        out = parse_encoder_freetext("sensorless control")
        assert out["device"] == "none"

    def test_garbage_falls_back_to_unknown_with_raw(self) -> None:
        out = parse_encoder_freetext("xyzzy plugh")
        assert out["device"] == "unknown"
        assert out["raw"] == "xyzzy plugh"

    def test_empty_string_returns_unknown(self) -> None:
        out = parse_encoder_freetext("   ")
        assert out["device"] == "unknown"
        assert "raw" not in out


@pytest.mark.unit
class TestEncoderFeedbackCoercion:
    def test_string_input_goes_through_shim(self) -> None:
        e = EncoderFeedback.model_validate("EnDat 2.2 multi-turn")
        assert e.protocol == "endat_2_2"
        assert e.multiturn is True
        assert e.raw == "EnDat 2.2 multi-turn"

    def test_dict_input_passes_through(self) -> None:
        e = EncoderFeedback.model_validate(
            {"device": "resolver", "resolver_pole_pairs": 2}
        )
        assert e.device == "resolver"
        assert e.resolver_pole_pairs == 2

    def test_unknown_device_for_unparseable_input(self) -> None:
        e = EncoderFeedback.model_validate("xyzzy")
        assert e.device == "unknown"
        assert e.raw == "xyzzy"


@pytest.mark.unit
class TestSubsumption:
    def test_endat_2_2_subsumes_endat_2_1(self) -> None:
        # SUBSUMES rule from the model — Heidenhain documents EnDat 2.2
        # as fully downward compatible with 2.1.
        provided = EncoderFeedback(protocol="endat_2_1")
        assert feedback_subsumes("endat_2_2", provided) is True
        # The other direction does NOT hold.
        provided2 = EncoderFeedback(protocol="endat_2_2")
        assert feedback_subsumes("endat_2_1", provided2) is False

    def test_identity_match(self) -> None:
        provided = EncoderFeedback(protocol="biss_c")
        assert feedback_subsumes("biss_c", provided) is True

    def test_hiperface_does_not_subsume_dsl(self) -> None:
        # Distinct cable architectures — must NOT auto-collapse.
        provided = EncoderFeedback(protocol="hiperface_dsl")
        assert feedback_subsumes("hiperface", provided) is False

    def test_no_protocol_on_motor_side_returns_false(self) -> None:
        provided = EncoderFeedback(device="resolver")  # no protocol
        assert feedback_subsumes("biss_c", provided) is False


@pytest.mark.unit
class TestProtocolStringCoercer:
    def test_canonical_passthrough(self) -> None:
        # Already-canonical enum values pass through unchanged so the
        # Drive coercer doesn't downgrade ['endat_2_2', 'biss_c'] to
        # ['endat_2_2', 'unknown'].
        assert coerce_protocol_string("endat_2_2") == "endat_2_2"
        assert coerce_protocol_string("biss_c") == "biss_c"

    def test_vendor_synonym(self) -> None:
        assert coerce_protocol_string("EnDat 2.2") == "endat_2_2"
        assert coerce_protocol_string("BiSS-C") == "biss_c"
        assert coerce_protocol_string("MR-J5") == "mitsubishi_j5"

    def test_no_match_returns_none(self) -> None:
        assert coerce_protocol_string("xyzzy") is None


@pytest.mark.unit
class TestModelIntegration:
    """End-to-end: legacy free-text payload survives Motor / Drive validation."""

    def test_motor_accepts_legacy_string(self) -> None:
        m = Motor(
            product_name="TM",
            manufacturer="X",
            part_number="P",
            encoder_feedback_support="EnDat 2.2",
        )
        assert isinstance(m.encoder_feedback_support, EncoderFeedback)
        assert m.encoder_feedback_support.protocol == "endat_2_2"

    def test_motor_accepts_structured_dict(self) -> None:
        m = Motor(
            product_name="TM",
            manufacturer="X",
            part_number="P",
            encoder_feedback_support={
                "device": "absolute_optical_multiturn",
                "protocol": "biss_c",
                "bits_per_turn": 24,
            },
        )
        assert m.encoder_feedback_support is not None
        assert m.encoder_feedback_support.bits_per_turn == 24

    def test_drive_accepts_legacy_string_list(self) -> None:
        d = Drive(
            product_name="TD",
            manufacturer="X",
            part_number="P",
            encoder_feedback_support=["EnDat 2.2", "Resolver"],
        )
        assert d.encoder_feedback_support == ["endat_2_2", "resolver_analog"]

    def test_drive_unrecognized_string_becomes_unknown(self) -> None:
        d = Drive(
            product_name="TD",
            manufacturer="X",
            part_number="P",
            encoder_feedback_support=["xyzzy"],
        )
        assert d.encoder_feedback_support == ["unknown"]

    def test_drive_canonical_enum_values_pass_straight_through(self) -> None:
        d = Drive(
            product_name="TD",
            manufacturer="X",
            part_number="P",
            encoder_feedback_support=["endat_2_2", "biss_c"],
        )
        assert d.encoder_feedback_support == ["endat_2_2", "biss_c"]
