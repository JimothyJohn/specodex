# AI-generated comment:
# This module defines the Pydantic models for representing drive data.
# The models are derived from the structure of the `schema/drive.json` file.
# By defining a strict data model, we can leverage Pydantic's data validation,
# serialization, and documentation capabilities. This approach ensures data consistency
# and provides a clear, maintainable structure for working with drive specifications.

from __future__ import annotations

from typing import Annotated, Any, List, Literal, Optional

from pydantic import BeforeValidator

from specodex.models.common import (
    Current,
    Frequency,
    FrequencyRange,
    IpRating,
    Power,
    TemperatureRange,
    VoltageRange,
)
from specodex.models.communication_protocol import (
    COMMUNICATION_PROTOCOLS,
    CommunicationProtocol,
)
from specodex.models.encoder import EncoderProtocol, coerce_protocol_string
from specodex.models.product import ProductBase


def _fieldbus_key(s: str) -> str:
    """Spacing/case-insensitive comparison key for fieldbus names."""
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == "/")


_FIELDBUS_BY_KEY: dict[str, str] = {
    _fieldbus_key(p): p for p in COMMUNICATION_PROTOCOLS
}


def _coerce_fieldbus_list(v: Any) -> Any:
    """Normalize fieldbus spelling variants to canonical literals.

    Legacy DB rows and LLM payloads emit spacing/case variants —
    ``"ModbusRTU"``, ``"Profinet"``, ``"modbus_rtu"`` — and the bare
    ``Literal`` rejected the whole Drive row over one of them
    (surfaced by a 2026-07-25 prod readback failure).

    Normalization is spacing/case ONLY: Modbus RTU (serial, RS-485)
    and Modbus TCP (Ethernet) are distinct fieldbuses and are never
    merged. Entries matching no canonical name are dropped (there is
    no "unknown" sentinel in CommunicationProtocol). A list whose
    entries ALL dropped becomes None; an empty input list stays []
    (pinned API contract — see test_resilience.py).

    Supersedes the earlier strict-rejection stance in test_models.py
    ("canonical enum values plus a data rewrite"): the data rewrite
    never reached prod, and a rejected field makes the WHOLE row
    unreadable — invisible to listings and product_exists. Coerce on
    read, store canonical, like every other legacy-variant surface
    (encoder protocols, ambient_temp, gearhead string fields).
    """
    if v is None or not isinstance(v, list):
        return v
    if not v:
        return v
    out: list[Any] = []
    for item in v:
        if isinstance(item, str):
            mapped = _FIELDBUS_BY_KEY.get(
                _fieldbus_key(item.replace("_", " ").replace("-", " "))
            )
            if mapped is not None:
                out.append(mapped)
        else:
            out.append(item)
    return out or None


def _coerce_protocol_list(v: Any) -> Any:
    """Map free-text encoder protocol strings to canonical enum values.

    Handles legacy DB rows / LLM payloads that say ``"EnDat 2.2"`` or
    ``"Resolver"`` rather than ``"endat_2_2"`` / ``"resolver_analog"``.
    Strings that don't match any synonym (including empty /
    whitespace-only strings) become ``"unknown"`` (the enum
    sentinel) so the row still validates and the verifier can flag it.
    """
    if v is None:
        return None
    if not isinstance(v, list):
        return v
    out: list[Any] = []
    for item in v:
        if isinstance(item, str):
            mapped = coerce_protocol_string(item)
            # No-match strings (including empty/whitespace) collapse to
            # "unknown" so the downstream Literal[EncoderProtocol]
            # validator doesn't reject the whole row.
            out.append(mapped if mapped else "unknown")
        else:
            out.append(item)
    return out


# AI-generated comment:
# The main `Drive` model definition. This class acts as a factory for creating
# validated drive data objects. It can be instantiated with data that conforms
# to the defined structure. Pydantic's `BaseModel` handles the parsing and
# validation. The `Field` function with an `alias` is used to map the JSON's `_id`
# field to a more Python-friendly `id` attribute.


class Drive(ProductBase):
    """A Pydantic model representing the specifications of a servo drive."""

    product_type: Literal["drive"] = "drive"
    type: Optional[Literal["servo", "variable frequency"]] = None
    series: Optional[str] = None
    input_voltage: VoltageRange = None
    input_voltage_frequency: Optional[List[FrequencyRange]] = None
    input_voltage_phases: Optional[List[int]] = None
    rated_current: Current = None
    peak_current: Current = None
    rated_power: Power = None
    switching_frequency: Optional[List[Frequency]] = None
    fieldbus: Annotated[
        Optional[List[CommunicationProtocol]], BeforeValidator(_coerce_fieldbus_list)
    ] = None
    # control_modes: Optional[List[str]] = None
    # Drives accept a list of wire protocols. The compat layer
    # (`integration/compat.py:_compare_feedback`) checks membership
    # against the motor side's `EncoderFeedback.protocol`. Drives don't
    # need the full `EncoderFeedback` model because the device behind
    # the wire is the motor's problem. The BeforeValidator coerces
    # legacy free-text payloads ("EnDat 2.2", "Resolver") to canonical
    # enum values so back-compat with old DB rows is preserved.
    encoder_feedback_support: Annotated[
        Optional[List[EncoderProtocol]], BeforeValidator(_coerce_protocol_list)
    ] = None
    ethernet_ports: Optional[int] = None
    digital_inputs: Optional[int] = None
    digital_outputs: Optional[int] = None
    analog_inputs: Optional[int] = None
    analog_outputs: Optional[int] = None
    # safety_features: Optional[List[str]] = None
    safety_rating: Optional[List[str]] = None
    approvals: Optional[List[str]] = None
    max_humidity: Optional[float] = None
    ip_rating: IpRating = None
    operating_temp: TemperatureRange = None
