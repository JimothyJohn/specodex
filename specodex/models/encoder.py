"""Structured encoder-feedback model.

Replaces the free-text ``Optional[str]`` / ``Optional[List[str]]``
``encoder_feedback_support`` fields on Motor / Drive / ElectricCylinder
/ LinearActuator. The compatibility layer (``integration/compat.py``)
needs typed comparison — vendor-flavored strings ("EnDat 2.2",
"Smart Absolute", "Mitsubishi serial encoder") can't be reliably
matched across the catalog.

The closed enums below cover the protocols and devices we've seen in
real catalogs (see ``todo/DOUBLE_TAP_encoder_taxonomy.md`` for the
research with source citations). Two escape valves keep the schema
from being brittle:

1. Each enum has an ``"unknown"`` sentinel — the LLM emits this when
   the catalog text doesn't map cleanly. The verifier
   (``specodex/double_tap/verifier.py``) flags rows with ``unknown``
   for a primed second-pass extraction.
2. ``raw`` carries the original catalog text so the second-pass
   prompt can show the LLM what it punted on.

Schema axes mirror Heidenhain's published catalog organisation:
``device`` (the physical sensing principle) × ``protocol`` (the wire
format). A motor declares both; a drive declares only a list of
supported protocols (the wire is what has to line up — the device
behind it is the motor's problem).
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal, Optional, get_args

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums — closed taxonomies. Add new entries when real catalogs surface
# them; flagging "unknown" rate in production via godmode is the early
# warning that an enum is too narrow.
# ---------------------------------------------------------------------------


EncoderDevice = Literal[
    "incremental_optical",
    "absolute_optical",
    "absolute_optical_multiturn",
    "incremental_magnetic",
    "absolute_magnetic",
    "sin_cos_analog",
    "resolver",
    "inductive",
    "capacitive",
    "tachometer_dc",
    "hall_only",
    "none",  # sensorless / open-loop steppers
    "unknown",
]


EncoderProtocol = Literal[
    "quadrature_ttl",
    "open_collector",
    "hall_uvw",
    "sin_cos_1vpp",
    "ssi",
    "biss_c",
    "endat_2_1",
    "endat_2_2",
    "hiperface",
    "hiperface_dsl",
    "tamagawa_t_format",
    "mitsubishi_j3",
    "mitsubishi_j4",
    "mitsubishi_j5",
    "panasonic_a6",
    "yaskawa_sigma",
    "fanuc_serial",
    "drive_cliq",
    "oct_beckhoff",
    "resolver_analog",
    "proprietary_other",
    "unknown",
]


EncoderMode = Literal["incremental", "absolute"]


# Frozenset of canonical enum values, used by ``coerce_protocol_string``
# to identity-passthrough a string that's already a valid enum member.
# (No symmetric ``_CANONICAL_DEVICES`` — the device-side coercion is
# inline in ``_coerce_legacy_freetext`` and doesn't need passthrough.)
_CANONICAL_PROTOCOLS: frozenset[str] = frozenset(get_args(EncoderProtocol))


# ---------------------------------------------------------------------------
# Synonym tables — used by the legacy free-text coercer and by the
# verifier's primed re-prompt. Kept here (not in a CSV) so changes are
# diffable and tests can import them directly.
# ---------------------------------------------------------------------------


# Lowercase substring → enum value. Order matters: more-specific
# substrings come first so "biss-c" matches before plain "biss",
# "hiperface dsl" before "hiperface".
_PROTOCOL_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("biss-c", "biss_c"),
    ("biss c", "biss_c"),
    ("bissc", "biss_c"),
    ("endat 2.2", "endat_2_2"),
    ("endat2.2", "endat_2_2"),
    ("endat-2.2", "endat_2_2"),
    ("endat 2.1", "endat_2_1"),
    ("endat2.1", "endat_2_1"),
    ("endat-2.1", "endat_2_1"),
    ("endat", "endat_2_2"),  # bare "EnDat" → assume 2.2 per taxonomy doc rule 5
    ("hiperface dsl", "hiperface_dsl"),
    ("hiperface-dsl", "hiperface_dsl"),
    ("hiperface", "hiperface"),
    ("ssi", "ssi"),
    ("tamagawa", "tamagawa_t_format"),
    ("t-format", "tamagawa_t_format"),
    ("t format", "tamagawa_t_format"),
    ("smart-abs", "tamagawa_t_format"),
    ("smart abs", "tamagawa_t_format"),
    ("mr-j5", "mitsubishi_j5"),
    ("mrj5", "mitsubishi_j5"),
    ("mr-j4", "mitsubishi_j4"),
    ("mrj4", "mitsubishi_j4"),
    ("mr-j3", "mitsubishi_j3"),
    ("mrj3", "mitsubishi_j3"),
    ("minas a6", "panasonic_a6"),
    ("a6n", "panasonic_a6"),
    ("fanuc", "fanuc_serial"),
    ("alpha-i", "fanuc_serial"),
    ("αi", "fanuc_serial"),
    ("sigma-7", "yaskawa_sigma"),
    ("sigma7", "yaskawa_sigma"),
    ("sigma-v", "yaskawa_sigma"),
    ("sigmav", "yaskawa_sigma"),
    ("drive-cliq", "drive_cliq"),
    ("drivecliq", "drive_cliq"),
    ("dq cliq", "drive_cliq"),
    ("oct", "oct_beckhoff"),
    ("one cable", "oct_beckhoff"),
    ("rs-422", "quadrature_ttl"),
    ("rs422", "quadrature_ttl"),
    ("ttl", "quadrature_ttl"),
    ("line driver", "quadrature_ttl"),
    ("line-driver", "quadrature_ttl"),
    ("open collector", "open_collector"),
    ("open-collector", "open_collector"),
    ("1vpp", "sin_cos_1vpp"),
    ("1 vpp", "sin_cos_1vpp"),
    ("sin/cos", "sin_cos_1vpp"),
    ("sincos", "sin_cos_1vpp"),
    ("hall uvw", "hall_uvw"),
    ("uvw commutation", "hall_uvw"),
    ("resolver", "resolver_analog"),  # bare resolver → treated as analog wire
)


_DEVICE_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("multi-turn absolute", "absolute_optical_multiturn"),
    ("multiturn absolute", "absolute_optical_multiturn"),
    ("absolute multi-turn", "absolute_optical_multiturn"),
    ("optical incremental", "incremental_optical"),
    ("incremental optical", "incremental_optical"),
    ("optical absolute", "absolute_optical"),
    ("absolute optical", "absolute_optical"),
    ("magnetic incremental", "incremental_magnetic"),
    ("incremental magnetic", "incremental_magnetic"),
    ("magnetic absolute", "absolute_magnetic"),
    ("absolute magnetic", "absolute_magnetic"),
    ("incremental", "incremental_optical"),  # default to optical when unspecified
    ("absolute", "absolute_optical"),
    ("sin/cos", "sin_cos_analog"),
    ("sincos", "sin_cos_analog"),
    ("resolver", "resolver"),
    ("hall sensor", "hall_only"),
    ("hall switches", "hall_only"),
    ("uvw commutation", "hall_only"),
    ("tachogenerator", "tachometer_dc"),
    ("tach", "tachometer_dc"),
    ("inductive", "inductive"),
    ("renishaw resolute", "absolute_optical"),  # RESOLUTE is optical
    ("renishaw astria", "inductive"),
    ("amo lia", "inductive"),
    ("netzer", "capacitive"),
    ("capacitive", "capacitive"),
    ("sensorless", "none"),
    ("open loop", "none"),
    ("open-loop", "none"),
)


# Protocol → (default_device, default_mode) used when the catalog only
# names the protocol. Comes from vendor docs — see the taxonomy doc.
_PROTOCOL_DEFAULTS: dict[str, tuple[str, str]] = {
    "biss_c": ("absolute_optical", "absolute"),
    "endat_2_1": ("absolute_optical", "absolute"),
    "endat_2_2": ("absolute_optical", "absolute"),
    "hiperface": ("absolute_optical", "absolute"),
    "hiperface_dsl": ("absolute_optical_multiturn", "absolute"),
    "ssi": ("absolute_optical", "absolute"),
    "tamagawa_t_format": ("absolute_optical_multiturn", "absolute"),
    "mitsubishi_j3": ("absolute_optical_multiturn", "absolute"),
    "mitsubishi_j4": ("absolute_optical_multiturn", "absolute"),
    "mitsubishi_j5": ("absolute_optical_multiturn", "absolute"),
    "panasonic_a6": ("absolute_optical_multiturn", "absolute"),
    "fanuc_serial": ("absolute_optical_multiturn", "absolute"),
    "yaskawa_sigma": ("absolute_optical_multiturn", "absolute"),
    "drive_cliq": ("absolute_optical_multiturn", "absolute"),
    "oct_beckhoff": ("absolute_optical_multiturn", "absolute"),
    "quadrature_ttl": ("incremental_optical", "incremental"),
    "open_collector": ("incremental_optical", "incremental"),
    "sin_cos_1vpp": ("sin_cos_analog", "incremental"),
    "hall_uvw": ("hall_only", "incremental"),
    "resolver_analog": ("resolver", "absolute"),
    "proprietary_other": ("unknown", "absolute"),
}


# Subsumption matrix for the compat layer. ``SUBSUMES[A] = {B, C, ...}``
# means "a drive that accepts A also accepts B and C". Identity is
# implicit (every protocol accepts itself).
#
# Conservative on purpose — see DOUBLE_TAP.md Part 6: under-match is
# safer than over-match. EnDat 2.2 is documented backwards-compatible
# with 2.1 (Heidenhain "EnDat 2.2 is fully downward compatible"). Other
# entries widened only when a vendor catalog shows the cross-acceptance.
SUBSUMES: dict[str, frozenset[str]] = {
    "endat_2_2": frozenset({"endat_2_1"}),
    # Hiperface DSL is electrically distinct (digital one-cable) from
    # Hiperface (analog sin/cos + RS-485). Sick docs say drives need
    # explicit DSL support; we do NOT collapse them.
    # OCT is "Hiperface-DSL-like" but Beckhoff doesn't license DSL —
    # keep distinct per taxonomy doc Open Question 2.
}


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


class EncoderFeedback(BaseModel):
    """One encoder-feedback specification (motor / actuator side).

    All fields except ``device`` are optional because real catalogs
    publish wildly varying levels of detail. The verifier flags rows
    with ``device="unknown"`` (or with a populated ``raw`` indicating
    legacy free-text shim) for a primed second-pass extraction.

    Drives don't use this full model — they use ``Optional[List[
    EncoderProtocol]]`` directly because the wire format is what has
    to line up for compatibility.
    """

    model_config = {"populate_by_name": True}

    device: EncoderDevice = Field(
        "unknown",
        description=(
            "Physical sensor type. Use 'incremental_optical' for plain "
            "quadrature optical encoders; 'absolute_optical' for single-"
            "turn absolute optical (BiSS-C, EnDat, etc.); "
            "'absolute_optical_multiturn' when the spec mentions multi-"
            "turn or 'MT'; 'resolver' for resolvers; 'none' for "
            "sensorless / open-loop; 'unknown' only when the catalog "
            "text doesn't fit any enum."
        ),
    )
    protocol: Optional[EncoderProtocol] = Field(
        None,
        description=(
            "Wire / digital interface protocol. Map vendor names to enums: "
            "'EnDat 2.2'→'endat_2_2', 'BiSS-C'→'biss_c', 'Hiperface DSL'"
            "→'hiperface_dsl' (vs bare 'Hiperface'→'hiperface'), "
            "'Mitsubishi MR-J5'→'mitsubishi_j5'. Bare 'EnDat' with no "
            "version → 'endat_2_2'. Bare 'N-bit absolute' with no "
            "vendor name → leave null and set bits_per_turn=N — DO NOT "
            "guess the protocol from the bit count alone."
        ),
    )
    mode: Optional[EncoderMode] = Field(
        None, description="'incremental' (relative position) or 'absolute'."
    )
    multiturn: Optional[bool] = Field(
        None,
        description=(
            "True for multi-turn encoders (track revolution count), "
            "False for single-turn (reset every revolution). Only "
            "meaningful for absolute encoders."
        ),
    )
    multiturn_bits: Optional[int] = Field(
        None,
        description=(
            "Number of revolution-counting bits, when multiturn=true. "
            "Common values: 12, 16, 20. Leave null if not stated — "
            "industry default varies by vendor (don't guess)."
        ),
    )
    multiturn_battery_backed: Optional[bool] = Field(
        None,
        description=(
            "True for battery-backed multi-turn encoders, False for true "
            "(mechanical / Wiegand) batteryless multi-turn (Panasonic A6, "
            "Mitsubishi MR-J5). Omit when unknown."
        ),
    )
    bits_per_turn: Optional[int] = Field(
        None,
        description=(
            "Single-turn resolution in bits, for absolute encoders. "
            "Examples: 17, 20, 22, 23, 24, 26."
        ),
    )
    pulses_per_rev: Optional[int] = Field(
        None,
        description=(
            "Pulses per revolution, for incremental encoders (PPR). "
            "Quote the catalog value before edge multiplication — "
            "2,500 PPR not 10,000 CPR."
        ),
    )
    lines_per_rev: Optional[int] = Field(
        None,
        description=(
            "Lines per revolution, for sin/cos analog encoders. "
            "Common values: 1,024 / 2,048 / 4,096."
        ),
    )
    resolver_pole_pairs: Optional[int] = Field(
        None,
        description=(
            "Resolver pole-pair count (1, 2, 4...). Only meaningful for "
            "device='resolver'. If unstated for a 'resolver' entry, "
            "industry default is 1 (1X)."
        ),
    )
    raw: Optional[str] = Field(
        None,
        description=(
            "Original catalog text. Populated by the back-compat shim "
            "when legacy free-text payloads are coerced; the verifier "
            "uses it to drive the primed second-pass extraction."
        ),
    )

    # Class-level lookup tables exposed for tests + the verifier prompt.
    PROTOCOL_SYNONYMS: ClassVar[tuple[tuple[str, str], ...]] = _PROTOCOL_SYNONYMS
    DEVICE_SYNONYMS: ClassVar[tuple[tuple[str, str], ...]] = _DEVICE_SYNONYMS
    PROTOCOL_DEFAULTS: ClassVar[dict[str, tuple[str, str]]] = _PROTOCOL_DEFAULTS
    SUBSUMES: ClassVar[dict[str, frozenset[str]]] = SUBSUMES

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_freetext(cls, data: Any) -> Any:
        """Best-effort parse of legacy free-text payloads.

        A string input ('EnDat 2.2', 'Resolver', 'Incremental 2500 ppr')
        is parsed via the synonym tables; on success the structured dict
        replaces the string. On failure ``device='unknown'`` + ``raw=<input>``
        so the verifier can pick it up.
        """
        if data is None or isinstance(data, EncoderFeedback):
            return data
        if isinstance(data, dict):
            return data
        if not isinstance(data, str):
            return data
        return parse_encoder_freetext(data)


def _match_first(table: tuple[tuple[str, str], ...], lower: str) -> Optional[str]:
    """Return the value for the first synonym substring that hits ``lower``."""
    for needle, value in table:
        if needle in lower:
            return value
    return None


def parse_encoder_freetext(text: str) -> dict:
    """Parse a free-text encoder description into the structured dict.

    Always returns a dict (never raises). On total parse failure returns
    ``{"device": "unknown", "raw": <input>}`` so downstream Pydantic
    validation produces a row the verifier can flag.
    """
    raw = text.strip()
    if not raw:
        return {"device": "unknown"}
    lower = raw.lower()

    out: dict[str, Any] = {"raw": raw}

    # Pass 1: protocol.
    proto = _match_first(_PROTOCOL_SYNONYMS, lower)
    if proto:
        out["protocol"] = proto

    # Pass 2: device. Take the first device-synonym hit; if none and we
    # have a protocol, fall back to the protocol's default device.
    device = _match_first(_DEVICE_SYNONYMS, lower)
    if device:
        out["device"] = device
    elif proto:
        default = _PROTOCOL_DEFAULTS.get(proto)
        if default:
            out["device"] = default[0]
            out.setdefault("mode", default[1])
        else:
            out["device"] = "unknown"
    else:
        out["device"] = "unknown"

    # Mode inference for incremental / absolute keywords if not set yet.
    if "mode" not in out:
        if "absolute" in lower:
            out["mode"] = "absolute"
        elif "incremental" in lower or "quadrature" in lower:
            out["mode"] = "incremental"

    # Multi-turn / single-turn.
    if "multi-turn" in lower or "multiturn" in lower or "multi turn" in lower:
        out["multiturn"] = True
    elif "single-turn" in lower or "singleturn" in lower or "single turn" in lower:
        out["multiturn"] = False

    if "batteryless" in lower or "battery-less" in lower or "battery free" in lower:
        out["multiturn_battery_backed"] = False
    elif "battery backup" in lower or "battery-backed" in lower:
        out["multiturn_battery_backed"] = True

    # Resolution: parse "26-bit" / "20 bit", "2500 ppr", "1024 lines".
    m = re.search(r"(\d+)\s*[- ]?bit", lower)
    if m:
        out["bits_per_turn"] = int(m.group(1))
    m = re.search(r"(\d{2,7})\s*ppr", lower)
    if m:
        out["pulses_per_rev"] = int(m.group(1))
        # Disambiguation rule 1 (taxonomy doc): "Incremental N ppr" with
        # no protocol named → quadrature_ttl. Apply only when no other
        # protocol matched so we don't clobber a vendor-specific entry.
        out.setdefault("protocol", "quadrature_ttl")
    m = re.search(r"(\d{2,6})\s*(?:lines|lpr|line)\b", lower)
    if m:
        out["lines_per_rev"] = int(m.group(1))

    # Resolver pole pairs: "2-pole resolver", "4-pole pair resolver", etc.
    if out.get("device") == "resolver":
        m = re.search(r"(\d+)\s*(?:-?\s*pole\s*pair|x\s*resolver)", lower)
        if m:
            out["resolver_pole_pairs"] = int(m.group(1))

    return out


def coerce_protocol_string(text: str) -> Optional[str]:
    """Map a free-text protocol description to an EncoderProtocol enum value.

    Returns ``None`` when no synonym hits — callers fall back to the
    ``"unknown"`` enum sentinel rather than dropping the row. A string
    that's already a canonical enum value passes straight through.
    """
    if not text:
        return None
    s = text.strip()
    if s in _CANONICAL_PROTOCOLS:
        return s
    return _match_first(_PROTOCOL_SYNONYMS, s.lower())


def feedback_subsumes(supported_proto: str, provided: "EncoderFeedback") -> bool:
    """True when a drive supporting ``supported_proto`` accepts ``provided``.

    The match is on protocol identity plus the ``SUBSUMES`` widening
    (EnDat 2.2 → 2.1 etc.). When the motor side has no protocol set,
    we can't safely conclude compatibility — return False (the compat
    layer will surface this as a "missing data" partial, not a fail).
    """
    if provided.protocol is None:
        return False
    if supported_proto == provided.protocol:
        return True
    return provided.protocol in SUBSUMES.get(supported_proto, frozenset())
