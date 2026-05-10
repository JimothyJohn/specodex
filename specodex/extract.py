"""Shared LLM call + parse helper for the PDF and web scrapers.

The PDF scraper (specodex/scraper.py) and web scraper (specodex/web_scraper.py)
each call Gemini and parse the structured response into Pydantic models. That
two-step (call → parse) is the only piece of the post-fetch pipeline that's
genuinely identical between them — everything downstream (ID strategy, enrich
mode, per-page metadata, ingest-log telemetry) is type-specific. This module
exposes just the shared two-step.
"""

from __future__ import annotations

from typing import Any, List, Optional

from specodex.config import SCHEMA_CHOICES
from specodex.llm import generate_content
from specodex.utils import parse_gemini_response


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
