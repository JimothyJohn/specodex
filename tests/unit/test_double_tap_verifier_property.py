"""Property tests for the double-tap verifier (PR #91 surface).

The verifier's job is to scan first-pass LLM extractions and decide
which fields warrant a second pass. It eats arbitrary
``ProductBase`` instances (including ones reconstructed from
malformed DB rows) and must never crash or hand the runner a Probe
whose ``fields`` list contains invalid entries.

**Contract under test:**

1. ``_encoder_is_ambiguous(value)`` accepts ``Any`` and returns a
   ``bool``. Never raises.
2. ``verify(products)`` returns one ``Probe`` per input product
   (same length, same order). Never raises.
3. Every emitted ``Probe.fields`` entry has a non-empty ``field``
   string and a ``reason`` from the enumerated set
   (``encoder_ambiguous`` or ``missing``).
4. A product with all common fields populated and a non-ambiguous
   encoder produces an empty Probe (``probe.empty() is True``).

The example-based tests in ``test_double_tap.py`` cover the happy
path; this file generates adversarial input shapes (mixed
encoder values, partial product fields, garbage in encoder lists)
to catch crashes the typed tests miss.
"""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.double_tap.verifier import (
    _encoder_is_ambiguous,
    verify,
)
from specodex.models.drive import Drive
from specodex.models.encoder import EncoderFeedback
from specodex.models.motor import Motor


# Anything the verifier might see — None, strings, EncoderFeedback,
# lists of either, plus the typical "garbage" shapes (bool, int).
_ENCODER_VALUES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.text(min_size=0, max_size=16),
    st.lists(
        st.one_of(
            st.none(),
            st.text(max_size=16),
            st.sampled_from(["endat_2_2", "biss_c", "unknown"]),
        ),
        max_size=4,
    ),
    st.builds(
        EncoderFeedback,
        device=st.sampled_from(["incremental_optical", "absolute_optical", "unknown"]),
        protocol=st.one_of(
            st.none(),
            st.sampled_from(["endat_2_2", "biss_c", "unknown"]),
        ),
    ),
)


# Motor strategy with just-enough fields to drive the verifier.
@st.composite
def _motor_strategy(draw):
    return Motor(
        manufacturer="Acme",
        product_name="X",
        product_type="motor",
        encoder_feedback_support=draw(
            st.one_of(
                st.none(),
                st.text(max_size=20),
                st.sampled_from(
                    [
                        "EnDat 2.2 multi-turn 24-bit",
                        "Resolver",
                        "BiSS-C",
                        "unknown",
                        "",
                    ]
                ),
            )
        ),
    )


@st.composite
def _drive_strategy(draw):
    return Drive(
        manufacturer="Acme",
        product_name="X",
        product_type="drive",
        encoder_feedback_support=draw(
            st.one_of(
                st.none(),
                st.lists(
                    st.sampled_from(["endat_2_2", "biss_c", "unknown"]),
                    max_size=4,
                ),
            )
        ),
    )


class TestEncoderIsAmbiguous:
    """``_encoder_is_ambiguous`` accepts Any, returns bool, never raises."""

    @given(value=_ENCODER_VALUES)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_returns_bool_never_raises(self, value: Any) -> None:
        result = _encoder_is_ambiguous(value)
        assert isinstance(result, bool)

    def test_none_returns_false(self) -> None:
        """None is "missing", not "ambiguous" — the common-field
        rule covers it."""
        assert _encoder_is_ambiguous(None) is False

    def test_unknown_sentinel_string_is_ambiguous(self) -> None:
        """Drive-side: a protocol-list entry of 'unknown' is
        ambiguous."""
        assert _encoder_is_ambiguous("unknown") is True

    def test_canonical_protocol_string_is_not_ambiguous(self) -> None:
        assert _encoder_is_ambiguous("endat_2_2") is False

    def test_unknown_device_in_encoder_feedback(self) -> None:
        ef = EncoderFeedback(device="unknown", protocol=None)
        assert _encoder_is_ambiguous(ef) is True

    def test_resolved_encoder_feedback_is_not_ambiguous(self) -> None:
        ef = EncoderFeedback(device="absolute_optical", protocol="endat_2_2")
        assert _encoder_is_ambiguous(ef) is False

    def test_list_with_any_ambiguous_member_is_ambiguous(self) -> None:
        assert _encoder_is_ambiguous(["endat_2_2", "unknown"]) is True

    def test_empty_list_is_not_ambiguous(self) -> None:
        assert _encoder_is_ambiguous([]) is False


class TestVerifyProperty:
    """``verify(products)`` returns one Probe per product, never raises."""

    @given(motors=st.lists(_motor_strategy(), min_size=0, max_size=5))
    @settings(
        max_examples=150,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_one_probe_per_motor(self, motors: list[Motor]) -> None:
        probes = verify(motors)
        probes_list = list(probes)
        assert len(probes_list) == len(motors)
        for probe in probes_list:
            assert probe.product_type == "motor"

    @given(drives=st.lists(_drive_strategy(), min_size=0, max_size=5))
    @settings(
        max_examples=150,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_one_probe_per_drive(self, drives: list[Drive]) -> None:
        probes = verify(drives)
        probes_list = list(probes)
        assert len(probes_list) == len(drives)
        for probe in probes_list:
            assert probe.product_type == "drive"

    @given(motors=st.lists(_motor_strategy(), min_size=0, max_size=5))
    @settings(
        max_examples=150,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_probe_fields_well_formed(self, motors: list[Motor]) -> None:
        """Every FieldProbe in every emitted Probe has a non-empty
        field name and a known reason."""
        valid_reasons = {"encoder_ambiguous", "missing", "wrong_unit_dropped"}
        for probe in verify(motors):
            for fp in probe.fields:
                assert isinstance(fp.field, str) and fp.field, (
                    f"empty field name in probe: {fp}"
                )
                assert fp.reason in valid_reasons, (
                    f"unexpected reason {fp.reason!r} in {fp}"
                )

    def test_empty_input_returns_empty(self) -> None:
        assert list(verify([])) == []

    def test_fully_populated_motor_has_empty_probe(self) -> None:
        """A motor with every common field populated AND no encoder
        ambiguity produces an empty probe (nothing to re-extract)."""
        motor = Motor(
            manufacturer="Acme",
            product_name="X",
            product_type="motor",
            rated_voltage={"value": 240, "unit": "V"},
            rated_current={"value": 5, "unit": "A"},
            rated_torque={"value": 10, "unit": "Nm"},
            rated_speed={"value": 3000, "unit": "rpm"},
            rated_power={"value": 1.5, "unit": "kW"},
            rotor_inertia={"value": 0.5, "unit": "kg·cm²"},
            encoder_feedback_support={
                "device": "absolute_optical",
                "protocol": "endat_2_2",
            },
        )
        probes = list(verify([motor]))
        assert len(probes) == 1
        assert probes[0].empty(), (
            f"fully-populated motor should produce empty probe, got {probes[0].fields}"
        )
