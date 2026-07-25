"""Shared LLM call + parse helper for the PDF and web scrapers.

The PDF scraper (specodex/scraper.py) and web scraper (specodex/web_scraper.py)
each call Gemini and parse the structured response into Pydantic models. That
two-step (call → parse) is the only piece of the post-fetch pipeline that's
genuinely identical between them — everything downstream (ID strategy, enrich
mode, per-page metadata, ingest-log telemetry) is type-specific. This module
exposes just the shared two-step.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, List, Optional

from specodex.config import SCHEMA_CHOICES
from specodex.llm import generate_content
from specodex.models.product import ProductBase
from specodex.utils import parse_gemini_response

logger: logging.Logger = logging.getLogger(__name__)


def _token_counts(response: Any) -> tuple[int, int]:
    """Pull (input, output) token counts off a genai response; zeros if absent."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0

    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return (
        _as_int(getattr(usage, "prompt_token_count", 0)),
        _as_int(getattr(usage, "candidates_token_count", 0)),
    )


def call_llm_and_parse(
    doc_data: bytes | str,
    api_key: str,
    product_type: str,
    context: dict,
    content_type: str,
    tokens: Optional[dict] = None,
    prompt_prefix: Optional[str] = None,
) -> List[Any]:
    """Call Gemini and parse the response into Pydantic models.

    If ``tokens`` is a dict with 'input'/'output' keys, the per-call
    token counts are added to it in-place so the caller can roll up
    multi-call (per-page) extractions.

    ``prompt_prefix`` is forwarded to ``generate_content`` — the
    double-tap runner uses it to inject the priming block (first-pass
    output + fields the verifier flagged) before the standard
    extraction prompt.
    """
    response = generate_content(
        doc_data,
        api_key,
        product_type,
        context,
        content_type,
        prompt_prefix=prompt_prefix,
    )
    if tokens is not None:
        inp, out = _token_counts(response)
        tokens["input"] = tokens.get("input", 0) + inp
        tokens["output"] = tokens.get("output", 0) + out
    return parse_gemini_response(
        response, SCHEMA_CHOICES[product_type], product_type, context
    )


def guarded_extract(
    doc_data: bytes | str,
    api_key: str,
    product_type: str,
    context: dict,
    content_type: str,
    *,
    prompt_prefix: Optional[str] = None,
    accept: Optional[Callable[[Any], bool]] = None,
    key: Optional[Callable[[Any], Any]] = None,
    expected: Optional[int] = None,
    attempts: int = 3,
    allowed_fields: Optional[Iterable[str]] = None,
    tokens: Optional[dict] = None,
) -> List[Any]:
    """Deterministically-guarded extraction loop around ``call_llm_and_parse``.

    Prompt steering is stochastic: the same prefix that extracts a page
    cleanly can misplace or invent fields on the next run. Every batch
    driver that writes to the DB has therefore hand-rolled the same
    harness — retry the page, keep only rows that pass a guard, dedup
    across attempts, and null out fields Gemini fabricated (IP ratings,
    lubrication types, loads copied between fields). This codifies that
    harness (previously copy-pasted across the Apex / Nidec / Stober
    gearhead drivers in ``outputs/gearhead_ingest/``).

    Args:
        accept: per-row guard; rows where ``accept(model)`` is falsy are
            dropped. Defaults to requiring a non-empty ``part_number``.
        key: dedup key across rows and attempts; first accepted row per
            key wins. Defaults to the stripped ``part_number``.
        expected: stop retrying once this many unique accepted rows have
            accumulated. ``None`` means one accepted row suffices.
        attempts: max LLM calls for this document. LLM errors count as a
            spent attempt and are retried, not raised.
        allowed_fields: whitelist of *type-specific* spec fields to keep;
            every other spec field (fields the concrete model adds over
            ``ProductBase``) is set to ``None`` on each accepted row —
            the fabrication scrub. ``None`` disables scrubbing. Unknown
            names raise ``ValueError`` so a typo can't silently scrub a
            wanted field.
        tokens: forwarded to ``call_llm_and_parse`` for roll-up.

    Returns the accepted models in first-seen order (possibly fewer than
    ``expected`` when attempts run out — partial results are the caller's
    to inspect, never an exception).
    """
    model_class = SCHEMA_CHOICES[product_type]
    spec_fields = (
        set(model_class.model_fields) - set(ProductBase.model_fields) - {"product_type"}
    )
    scrub: Optional[set] = None
    if allowed_fields is not None:
        allowed = set(allowed_fields)
        unknown = allowed - spec_fields
        if unknown:
            raise ValueError(
                f"allowed_fields not on {model_class.__name__}: {sorted(unknown)}"
            )
        scrub = spec_fields - allowed

    if accept is None:

        def accept(m: Any) -> bool:
            return bool((getattr(m, "part_number", None) or "").strip())

    if key is None:

        def key(m: Any) -> Any:
            return (getattr(m, "part_number", None) or "").strip()

    accepted: dict = {}
    target = expected if expected is not None else 1
    for attempt in range(1, attempts + 1):
        try:
            out = call_llm_and_parse(
                doc_data,
                api_key,
                product_type,
                context,
                content_type,
                tokens=tokens,
                prompt_prefix=prompt_prefix,
            )
        except Exception as exc:  # noqa: BLE001 — retrying is the point
            logger.warning("guarded_extract attempt %d: LLM error %s", attempt, exc)
            continue
        for m in out:
            if not accept(m):
                continue
            if scrub is not None:
                for field in scrub:
                    setattr(m, field, None)
            accepted.setdefault(key(m), m)
        if len(accepted) >= target:
            break
        logger.info(
            "guarded_extract attempt %d: %d/%s accepted rows",
            attempt,
            len(accepted),
            expected if expected is not None else "1+",
        )
    return list(accepted.values())
