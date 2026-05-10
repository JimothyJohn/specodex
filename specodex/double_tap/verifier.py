"""Verifier — inspects first-pass extraction output and emits a Probe.

The Probe is the runner's contract: it lists the fields that need
re-extraction on the second pass, with per-field captions and a
human-readable primer the prompt builder injects.

Three rule classes for v1 (see DOUBLE_TAP.md Part 2 "What the verifier
checks"):

1. **encoder_ambiguous** — the structured EncoderFeedback came back
   with ``device="unknown"`` or ``protocol="unknown"`` (or a populated
   ``raw`` field, which means the back-compat shim couldn't fully
   resolve the legacy free-text).
2. **common_field_missing** — a field listed in ``captions.COMMON_FIELDS``
   for this product type came back ``None``.
3. **wrong_unit_dropped** — placeholder for the v2 rule that needs the
   parser to carry forward raw payloads when ``common.py``'s
   ``BeforeValidator`` returns ``None`` for a wrong unit family. Stubbed
   to ``False`` for now; will hook in when the parser carries the
   pre-coercion dict alongside the validated model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Literal, Optional

from specodex.double_tap.captions import captions_for, common_fields_for
from specodex.models.encoder import EncoderFeedback
from specodex.models.product import ProductBase


ProbeReason = Literal[
    "missing",
    "encoder_ambiguous",
    "wrong_unit_dropped",
]


@dataclass(frozen=True)
class FieldProbe:
    """One field's worth of "the LLM needs to look at this again."""

    field: str
    reason: ProbeReason
    captions: tuple[str, ...]
    primer: str

    @property
    def caption_text(self) -> str:
        if not self.captions:
            return ""
        return "look for: " + ", ".join(self.captions)


@dataclass
class Probe:
    """All re-extract requests for one product."""

    product_type: str
    part_number: Optional[str] = None
    fields: List[FieldProbe] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.fields

    def fires(self) -> bool:
        return bool(self.fields)

    def field_names(self) -> tuple[str, ...]:
        return tuple(f.field for f in self.fields)


def _encoder_is_ambiguous(value: Any) -> bool:
    """True when the structured EncoderFeedback didn't fully resolve.

    Handles single-encoder fields (motor, electric_cylinder), list
    fields (linear_actuator), and protocol-list fields (drive). The
    drive's protocol list is ambiguous when it contains the ``"unknown"``
    sentinel.
    """
    if value is None:
        # Encoder absent — that's a "missing" probe (handled by the
        # common-field rule), not an ambiguity probe.
        return False

    # List of EncoderFeedback (LinearActuator) or list of EncoderProtocol
    # strings (Drive).
    if isinstance(value, list):
        return any(_encoder_is_ambiguous(item) for item in value)

    if isinstance(value, str):
        # Drive protocol list element.
        return value == "unknown"

    if isinstance(value, EncoderFeedback):
        if value.device == "unknown":
            return True
        if value.protocol == "unknown":
            return True
        # raw populated alongside an enum match means the shim parsed
        # *something* but kept the original around — usually a partial
        # match worth a second look.
        if value.raw and value.protocol is None and value.device == "unknown":
            return True

    return False


def _verify_one(product: ProductBase) -> Probe:
    product_type = getattr(product, "product_type", "unknown")
    probe = Probe(
        product_type=product_type,
        part_number=getattr(product, "part_number", None),
    )

    # Rule 1: encoder ambiguity.
    encoder_value = getattr(product, "encoder_feedback_support", None)
    if _encoder_is_ambiguous(encoder_value):
        captions = captions_for(product_type, "encoder_feedback_support")
        probe.fields.append(
            FieldProbe(
                field="encoder_feedback_support",
                reason="encoder_ambiguous",
                captions=tuple(captions),
                primer=(
                    "Encoder feedback came back ambiguous on the first "
                    "pass — the structured value either resolved to "
                    "'unknown' or carried unresolved legacy text. Map "
                    "the catalog text to the EncoderFeedback enum: "
                    "device (e.g. absolute_optical_multiturn), protocol "
                    "(e.g. mitsubishi_j5, endat_2_2, hiperface_dsl), "
                    "and resolution (bits_per_turn / pulses_per_rev / "
                    "lines_per_rev). Bare 'EnDat' → endat_2_2; bare "
                    "'Hiperface' → hiperface (NOT DSL). Don't guess the "
                    "protocol from a bit count alone — leave protocol "
                    "null if only 'N-bit absolute' is stated."
                ),
            )
        )

    # Rule 2: common-field missing.
    for field_name in sorted(common_fields_for(product_type)):
        value = getattr(product, field_name, None)
        if value is None:
            captions = captions_for(product_type, field_name)
            probe.fields.append(
                FieldProbe(
                    field=field_name,
                    reason="missing",
                    captions=tuple(captions),
                    primer=(
                        f"`{field_name}` was not extracted on the first "
                        f"pass but is almost always present in catalogs "
                        f"of this type. Re-scan the page for the labels "
                        f"listed."
                    ),
                )
            )

    # Rule 3: wrong-unit dropped — stubbed for v2 (needs the parser to
    # surface the pre-coercion raw payload alongside the validated model).

    return probe


def verify(products: Iterable[ProductBase]) -> List[Probe]:
    """Inspect a batch of first-pass extractions and emit one Probe each.

    Returns the same number of Probes as input products, in order.
    Empty Probes (probe.empty() is True) mean the first pass was clean
    for that product — the runner skips the second LLM call for it.
    """
    return [_verify_one(p) for p in products]
