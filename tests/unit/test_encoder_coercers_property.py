"""Property tests for the encoder-feedback coercers.

DOUBLE_TAP (PR #91) closed the encoder taxonomy and added three
free-text coercion paths that eat catalog-flavored vendor text:

- ``coerce_protocol_string`` — single string → ``EncoderProtocol``
  enum value or ``None``.
- ``_coerce_protocol_list`` (in ``drive.py``) — list of strings →
  list of canonical enum values, with the ``"unknown"`` sentinel
  swapped in for entries that don't match any synonym.
- ``EncoderFeedback._coerce_legacy_freetext`` — string input
  becomes a structured ``EncoderFeedback`` instance via
  ``parse_encoder_freetext``.

All three eat untrusted bytes (LLM output / DB rows from before the
schema redesign). The example-based tests cover the happy path; this
file generates adversarial input — long strings, unicode, embedded
nulls, NaN/inf-laced inputs, recursive-shaped lists — and asserts
the documented contract holds.

**Contract under test:**

1. ``coerce_protocol_string`` returns ``None`` or a valid
   ``EncoderProtocol`` member; never raises.
2. ``_coerce_protocol_list`` returns ``None``, the original input
   (when not a list), or a list whose every entry is either the
   original element (passthrough) or a canonical ``EncoderProtocol``;
   never raises.
3. ``EncoderFeedback`` constructed from a string never raises and
   always produces an instance whose ``device`` is a valid
   ``EncoderDevice`` enum value (not necessarily the canonical one
   — ``"unknown"`` is the documented fallback when nothing matches).

Per HARDENING.md Phase 3.1 spirit (test-only, no production change).
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.models.drive import _coerce_protocol_list
from specodex.models.encoder import (
    EncoderDevice,
    EncoderFeedback,
    EncoderProtocol,
    coerce_protocol_string,
)


# Resolve the closed enum sets at module import time so a future
# taxonomy widening doesn't silently bypass the assertion.
_PROTOCOL_VALUES: frozenset[str] = frozenset(get_args(EncoderProtocol))
_DEVICE_VALUES: frozenset[str] = frozenset(get_args(EncoderDevice))


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


_ADVERSARIAL_STRING = st.one_of(
    # Empty / whitespace-only
    st.just(""),
    st.just("   "),
    st.just("\n"),
    st.just("\t"),
    # Plain ASCII text
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_ ", max_size=64),
    # Unicode (full BMP + supplementary)
    st.text(min_size=0, max_size=64),
    # Vendor-flavoured strings — what the LLM actually emits
    st.sampled_from(
        [
            "EnDat 2.2",
            "EnDat-2.2",
            "endat",
            "BiSS-C",
            "biss c",
            "Hiperface",
            "Hiperface DSL",
            "Resolver",
            "1Vpp sin/cos",
            "Mitsubishi MR-J5",
            "Yaskawa Sigma-7",
            "Drive-CLiQ",
            "OCT one cable",
            "TTL line driver",
            "Hall UVW commutation",
            "incremental 2500 ppr",
            "absolute 26-bit",
            "multi-turn absolute",
            "tachogenerator",
            # Adversarial near-misses
            "ENDAT2.2",
            "EnDat 2.3",  # invalid version
            "BiSSv2",  # invalid suffix
            "completely fake protocol name",
            "<script>alert(1)</script>",
            "\x00null-prefixed",
            "very " * 50 + "long string",
        ]
    ),
)


_ADVERSARIAL_PRIMITIVE = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True, width=64),
    _ADVERSARIAL_STRING,
)


_LIST_OR_ANY = st.one_of(
    st.none(),
    _ADVERSARIAL_PRIMITIVE,
    st.lists(_ADVERSARIAL_PRIMITIVE, max_size=8),
    st.dictionaries(st.text(max_size=8), _ADVERSARIAL_PRIMITIVE, max_size=4),
)


# ---------------------------------------------------------------------------
# coerce_protocol_string
# ---------------------------------------------------------------------------


class TestCoerceProtocolString:
    @given(text=_ADVERSARIAL_STRING)
    @settings(max_examples=300, deadline=None)
    def test_returns_none_or_valid_enum(self, text: str) -> None:
        try:
            result = coerce_protocol_string(text)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"coerce_protocol_string raised {type(exc).__name__}: "
                f"{exc!r}\ninput: {text!r}"
            )
        assert result is None or result in _PROTOCOL_VALUES, (
            f"coerce_protocol_string returned {result!r} which is "
            f"neither None nor a valid EncoderProtocol enum value\n"
            f"input: {text!r}"
        )

    def test_canonical_passthrough(self) -> None:
        """Strings that ARE the canonical enum value already must
        round-trip identically — no synonym detour."""
        for canonical in sorted(_PROTOCOL_VALUES):
            result = coerce_protocol_string(canonical)
            assert result == canonical, (
                f"canonical {canonical!r} should pass through unchanged, got {result!r}"
            )

    def test_empty_returns_none(self) -> None:
        assert coerce_protocol_string("") is None
        assert (
            coerce_protocol_string("   ") is None
            or coerce_protocol_string("   ") in _PROTOCOL_VALUES
        )


# ---------------------------------------------------------------------------
# _coerce_protocol_list (drive.py)
# ---------------------------------------------------------------------------


class TestCoerceProtocolList:
    """Validates the drive-side BeforeValidator that maps free-text
    list entries to canonical enum values.

    The documented behaviour: returns input unchanged when it isn't a
    list (so Pydantic's downstream validation can complain), passes
    through non-string list elements, and runs string elements
    through ``coerce_protocol_string``. Strings that don't match any
    synonym are mapped to ``"unknown"`` (not ``None``), so the row
    still validates and the verifier can flag it later.
    """

    @given(v=_LIST_OR_ANY)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_never_raises(self, v: Any) -> None:
        try:
            _coerce_protocol_list(v)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_coerce_protocol_list raised {type(exc).__name__}: "
                f"{exc!r}\ninput: {v!r}"
            )

    @given(items=st.lists(_ADVERSARIAL_PRIMITIVE, max_size=8))
    @settings(max_examples=200, deadline=None)
    def test_string_elements_become_valid_enums_or_unknown(self, items: list) -> None:
        try:
            result = _coerce_protocol_list(items)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_coerce_protocol_list raised {type(exc).__name__}: "
                f"{exc!r}\ninput: {items!r}"
            )
        # Result is a list when the input was; every string element
        # must be a valid enum value (canonical or "unknown").
        if not isinstance(result, list):
            return
        for elt, original in zip(result, items):
            if isinstance(original, str):
                assert elt in _PROTOCOL_VALUES, (
                    f"string element {original!r} mapped to {elt!r} "
                    f"which is not in EncoderProtocol enum"
                )
            # Non-string elements pass through unchanged per docstring.

    def test_none_passthrough(self) -> None:
        assert _coerce_protocol_list(None) is None

    def test_non_list_passthrough(self) -> None:
        # Documented: non-list input returns unchanged so Pydantic
        # downstream raises a typed ValidationError instead.
        for v in ("just a string", 42, {"a": 1}):
            assert _coerce_protocol_list(v) == v


# ---------------------------------------------------------------------------
# EncoderFeedback model (string-input path via _coerce_legacy_freetext)
# ---------------------------------------------------------------------------


class TestEncoderFeedbackFreetextProperty:
    """The model_validator(mode='before') accepts strings and runs
    them through ``parse_encoder_freetext``. The contract:
    construction never raises, ``device`` always lands on a valid
    enum value (``"unknown"`` is the fallback when nothing matches),
    and ``raw`` carries the original free-text so the verifier can
    pick it up.
    """

    @given(text=_ADVERSARIAL_STRING)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_string_input_constructs_valid_instance(self, text: str) -> None:
        try:
            feedback = EncoderFeedback.model_validate(text)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"EncoderFeedback.model_validate raised "
                f"{type(exc).__name__}: {exc!r}\ninput: {text!r}"
            )
        assert isinstance(feedback, EncoderFeedback)
        assert feedback.device in _DEVICE_VALUES, (
            f"device landed on {feedback.device!r} which is not in EncoderDevice enum"
        )
        # Protocol is Optional but if set must be a valid enum value.
        assert feedback.protocol is None or feedback.protocol in _PROTOCOL_VALUES

    @given(text=_ADVERSARIAL_STRING)
    @settings(max_examples=100, deadline=None)
    def test_string_input_round_trips_through_dump(self, text: str) -> None:
        """Build → dump → reload yields the same device/protocol.

        Catches a regression where the legacy-freetext path produces
        an instance whose dumped form doesn't validate back through
        the dict path — would mean the parser silently dropped a
        field that round-trip can't reconstruct.
        """
        feedback = EncoderFeedback.model_validate(text)
        dumped = feedback.model_dump()
        reloaded = EncoderFeedback.model_validate(dumped)
        assert reloaded.device == feedback.device
        assert reloaded.protocol == feedback.protocol

    def test_dict_input_unchanged(self) -> None:
        """Dict inputs short-circuit the freetext path."""
        feedback = EncoderFeedback.model_validate(
            {"device": "absolute_optical", "protocol": "endat_2_2"}
        )
        assert feedback.device == "absolute_optical"
        assert feedback.protocol == "endat_2_2"

    def test_existing_instance_passthrough(self) -> None:
        original = EncoderFeedback(device="resolver", protocol=None)
        result = EncoderFeedback.model_validate(original)
        assert result is original or result.device == original.device
