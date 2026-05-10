"""Double-tap runner — wraps call_llm_and_parse with a verifier loop.

Drop-in replacement for ``specodex.extract.call_llm_and_parse``. The
runner first extracts using the standard prompt, then runs the
verifier; if any product probe fires, it re-extracts the entire batch
with a primed second-pass prompt and merges the second-pass values
back into the first-pass result for the probed fields only.

The merge logic is deliberately conservative — second-pass values
overwrite first-pass values *only* for fields the probe flagged. A
contributor "improving" the merge to prefer second-pass everywhere
would let a hallucinating second pass regress good first-pass data.
The benchmark catches that regression direction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from specodex.double_tap.prompt import build_priming_block
from specodex.double_tap.verifier import Probe, verify
from specodex.extract import call_llm_and_parse
from specodex.models.product import ProductBase


logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class DoubleTapResult:
    """Returned by ``extract_with_recovery`` — products + telemetry."""

    products: List[ProductBase]
    first_pass_tokens: tuple[int, int] = (0, 0)
    second_pass_tokens: tuple[int, int] = (0, 0)
    probes_fired: int = 0
    fields_recovered: List[str] = field(default_factory=list)
    fields_corrected: List[str] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)

    @property
    def did_second_pass(self) -> bool:
        return self.probes_fired > 0


def _index_by_part_number(products: Iterable[ProductBase]) -> dict[str, ProductBase]:
    """Best-effort part_number → product mapping; products without part_numbers
    are keyed by index so the merger can still reach them."""
    out: dict[str, ProductBase] = {}
    for i, p in enumerate(products):
        pn = (getattr(p, "part_number", None) or "").strip()
        key = pn.lower() if pn else f"__idx_{i}"
        out[key] = p
    return out


def _merge_probed_fields(
    first: ProductBase,
    second: Optional[ProductBase],
    probe: Probe,
    *,
    fields_recovered: List[str],
    fields_corrected: List[str],
) -> ProductBase:
    """Take second-pass values for probed fields, leave first-pass otherwise.

    Mutates and returns ``first``. ``second=None`` means the second pass
    didn't surface a matching variant — first-pass values stay unchanged.
    """
    if second is None:
        return first
    for fp in probe.fields:
        first_val = getattr(first, fp.field, None)
        second_val = getattr(second, fp.field, None)
        if second_val is None:
            continue
        if first_val is None:
            setattr(first, fp.field, second_val)
            fields_recovered.append(fp.field)
        elif first_val != second_val:
            setattr(first, fp.field, second_val)
            fields_corrected.append(fp.field)
    return first


def _build_combined_priming(
    products: List[ProductBase],
    probes: List[Probe],
) -> str:
    """Build one priming block covering every probe-fired variant.

    Cheaper than per-variant calls — N variants needing re-look still
    cost one second-pass LLM call, not N. The builder concatenates the
    per-product priming blocks with variant headers so the model can
    emit a single re-extracted array.
    """
    blocks: List[str] = []
    for product, probe in zip(products, probes):
        if probe.empty():
            continue
        payload = product.model_dump(mode="json")
        block = build_priming_block(probe, payload)
        if block:
            pn = getattr(product, "part_number", "?") or "?"
            blocks.append(f"--- VARIANT {pn} ---\n{block}")
    return "\n".join(blocks)


def extract_with_recovery(
    doc_data: bytes | str,
    api_key: str,
    product_type: str,
    context: dict,
    content_type: str,
    tokens: Optional[dict] = None,
) -> DoubleTapResult:
    """Drop-in for call_llm_and_parse — runs first pass, verifies, optionally re-extracts.

    The shape mirrors call_llm_and_parse so the scraper can swap the
    call site without touching anything else. ``tokens`` is updated
    in-place across both passes so existing ingest-log telemetry sees
    the full token cost.
    """
    first_tokens: dict = {"input": 0, "output": 0}
    first_pass = call_llm_and_parse(
        doc_data,
        api_key,
        product_type,
        context,
        content_type,
        tokens=first_tokens,
    )
    if tokens is not None:
        tokens["input"] = tokens.get("input", 0) + first_tokens["input"]
        tokens["output"] = tokens.get("output", 0) + first_tokens["output"]

    if not first_pass:
        return DoubleTapResult(
            products=[],
            first_pass_tokens=(first_tokens["input"], first_tokens["output"]),
        )

    probes = verify(first_pass)
    fired_probes = [p for p in probes if p.fires()]

    result = DoubleTapResult(
        products=list(first_pass),
        first_pass_tokens=(first_tokens["input"], first_tokens["output"]),
        probes_fired=len(fired_probes),
    )

    if not fired_probes:
        return result

    primer = _build_combined_priming(first_pass, probes)
    if not primer:
        return result

    logger.info(
        "double-tap: %d/%d variants probed; running primed second pass",
        len(fired_probes),
        len(first_pass),
    )

    second_tokens: dict = {"input": 0, "output": 0}
    try:
        second_pass = call_llm_and_parse(
            doc_data,
            api_key,
            product_type,
            context,
            content_type,
            tokens=second_tokens,
            prompt_prefix=primer,
        )
    except Exception as e:
        # If the second pass blows up for any reason, fall back to the
        # first-pass result rather than poisoning the entire batch. The
        # scraper still gets a usable list of products to write.
        logger.warning(
            "double-tap: second-pass extraction failed (%s); keeping first-pass", e
        )
        result.unresolved = [
            f"{p.part_number or '?'}:{','.join(probe.field_names())}"
            for p, probe in zip(first_pass, probes)
            if probe.fires()
        ]
        return result

    if tokens is not None:
        tokens["input"] = tokens.get("input", 0) + second_tokens["input"]
        tokens["output"] = tokens.get("output", 0) + second_tokens["output"]
    result.second_pass_tokens = (second_tokens["input"], second_tokens["output"])

    second_index = _index_by_part_number(second_pass)
    fields_recovered: List[str] = []
    fields_corrected: List[str] = []

    for i, (product, probe) in enumerate(zip(first_pass, probes)):
        if probe.empty():
            continue
        pn = (getattr(product, "part_number", None) or "").strip().lower()
        match = second_index.get(pn) if pn else second_index.get(f"__idx_{i}")
        _merge_probed_fields(
            product,
            match,
            probe,
            fields_recovered=fields_recovered,
            fields_corrected=fields_corrected,
        )

    result.fields_recovered = fields_recovered
    result.fields_corrected = fields_corrected

    # Re-verify the merged products and surface anything that's still
    # ambiguous — useful for the godmode panel + outreach pipeline.
    post_probes = verify(result.products)
    result.unresolved = [
        f"{p.part_number or '?'}:{','.join(pp.field_names())}"
        for p, pp in zip(result.products, post_probes)
        if pp.fires()
    ]

    if result.unresolved:
        logger.info(
            "double-tap: %d variants still have unresolved probes after pass 2",
            len(result.unresolved),
        )

    return result


def extract_with_recovery_telemetry(result: DoubleTapResult) -> dict:
    """Flat dict for ingest-log persistence + ``./Quickstart godmode`` panel."""
    return {
        "double_tap_fired": result.did_second_pass,
        "double_tap_first_pass_input_tokens": result.first_pass_tokens[0],
        "double_tap_first_pass_output_tokens": result.first_pass_tokens[1],
        "double_tap_second_pass_input_tokens": result.second_pass_tokens[0],
        "double_tap_second_pass_output_tokens": result.second_pass_tokens[1],
        "double_tap_probes_fired": result.probes_fired,
        "double_tap_fields_recovered": list(result.fields_recovered),
        "double_tap_fields_corrected": list(result.fields_corrected),
        "double_tap_unresolved": list(result.unresolved),
    }


__all__ = [
    "DoubleTapResult",
    "extract_with_recovery",
    "extract_with_recovery_telemetry",
]
