"""Property tests for specodex.models.drive._coerce_fieldbus_list.

Contract (docstring-pinned):
1. Never raises on arbitrary input; non-list inputs pass through.
2. Output is None or a list whose string entries are all canonical
   CommunicationProtocol literals.
3. Spacing/case variants of a canonical name map to that name — and
   never to a different one (Modbus RTU must never become Modbus TCP:
   distinct buses, RS-485 serial vs Ethernet).
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from specodex.models.communication_protocol import COMMUNICATION_PROTOCOLS
from specodex.models.drive import _coerce_fieldbus_list, _fieldbus_key

_adversarial = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=30),
    st.sampled_from(list(COMMUNICATION_PROTOCOLS)),
    st.sampled_from(
        [
            "ModbusRTU",
            "ModbusTCP",
            "modbus_rtu",
            "MODBUS-TCP",
            "Profinet",
            "ethercat",
            "Ethernet/IP",
            "CClink IE",
            "sercosiii",
            "  EtherCAT  ",
            "",
            "   ",
            "{'value': 1}",
        ]
    ),
)


@settings(max_examples=300)
@given(st.one_of(_adversarial, st.lists(_adversarial, max_size=8)))
def test_never_raises_and_output_shape(v):
    out = _coerce_fieldbus_list(v)
    if not isinstance(v, list):
        # Pass-through is by identity — == would fail on NaN.
        assert out is v
        return
    if v == []:
        assert out == []  # empty-in/empty-out API contract
        return
    assert out is None or (isinstance(out, list) and len(out) > 0)
    if isinstance(out, list):
        for item in out:
            if isinstance(item, str):
                assert item in COMMUNICATION_PROTOCOLS


@settings(max_examples=200)
@given(st.sampled_from(list(COMMUNICATION_PROTOCOLS)), st.randoms())
def test_variants_map_to_their_own_canonical_never_a_sibling(canon, rng):
    # Build a variant: random case + random separator churn.
    variant = "".join(ch.upper() if rng.random() < 0.5 else ch.lower() for ch in canon)
    variant = variant.replace(" ", rng.choice([" ", "", "_", "-"]))
    out = _coerce_fieldbus_list([variant])
    assert out == [canon], f"{variant!r} mapped to {out}, expected [{canon!r}]"


def test_key_space_has_no_collisions():
    # Two canonical names collapsing to one key would make the mapping
    # ambiguous — pin that the literal set stays collision-free.
    keys = [_fieldbus_key(p) for p in COMMUNICATION_PROTOCOLS]
    assert len(keys) == len(set(keys))
