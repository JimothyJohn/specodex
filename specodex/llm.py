"""Gemini content generation for datasheet extraction.

Calls Gemini with a JSON schema derived from the Pydantic model so the
response is structurally guaranteed. Replaces the earlier CSV-as-
interchange approach, which was prone to column misalignment when Gemini
dropped empty cells.
"""

import logging
import re
from functools import lru_cache
from typing import Any, Optional, Dict

from google import genai
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from specodex.config import GUARDRAILS, MODEL, SCHEMA_CHOICES
from specodex.models.llm_schema import to_gemini_schema


logger: logging.Logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _client_for(api_key: str) -> genai.Client:
    """Cache the genai.Client per api_key so we don't rebuild the underlying
    httpx connection pool on every call. Cached by the api_key string so
    test fixtures with different keys still get isolated clients."""
    return genai.Client(api_key=api_key)


# Gemini 429 responses carry a structured retry hint:
#     {'@type': '...RetryInfo', 'retryDelay': '35s'}
# Plain exponential backoff (4, 8, 16, …) gives up well before that 35s,
# so a hot page exhausts tenacity's 5 attempts without ever waiting long
# enough to recover. Honor the hint when present.
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"](\d+(?:\.\d+)?)s['\"]")
_EXPONENTIAL_BACKOFF = wait_exponential(multiplier=1, min=4, max=60)


def _wait_with_retry_hint(retry_state: Any) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if exc is not None:
        m = _RETRY_DELAY_RE.search(str(exc))
        if m:
            try:
                # Add a 1s cushion and cap at 90s so a misconfigured
                # retryDelay can't stall us indefinitely.
                delay = min(float(m.group(1)) + 1.0, 90.0)
                logger.info(
                    "Gemini asked us to retry in %.1fs — waiting before next attempt",
                    delay,
                )
                return delay
            except ValueError:
                pass
    return _EXPONENTIAL_BACKOFF(retry_state)


@retry(
    stop=stop_after_attempt(5),
    wait=_wait_with_retry_hint,
)
def generate_content(
    doc_data: bytes | str,
    api_key: str,
    schema: str,
    context: Optional[Dict[str, Any]] = None,
    content_type: str = "pdf",
    mime_type: Optional[str] = None,
    prompt_prefix: Optional[str] = None,
) -> Any:
    """Generate a structured JSON extraction for a datasheet.

    Args:
        doc_data: The document data (bytes for PDF/image, string for HTML).
        api_key: Gemini API key.
        schema: Product type key into ``SCHEMA_CHOICES`` (e.g. ``"drive"``).
        context: Optional known fields the caller already has (manufacturer,
            product_name, product_family, datasheet_url). These are excluded
            from the schema so the LLM doesn't re-emit them.
        content_type: ``"pdf"``, ``"image"``, or ``"html"``.
        mime_type: Required when ``content_type="image"``. One of
            ``image/png``, ``image/jpeg``, ``image/webp``.
        prompt_prefix: Optional text injected ahead of the standard
            extraction prompt. Used by the double-tap runner to prime
            a second pass with the first-pass result + the fields the
            verifier flagged. Empty / ``None`` = standard one-pass
            extraction.

    Returns the raw ``google.genai`` response object. The JSON payload is
    accessed via ``response.text`` and parsed downstream by
    ``specodex.utils.parse_gemini_response``.
    """
    client: genai.Client = _client_for(api_key)

    full_schema_type = SCHEMA_CHOICES[schema]
    response_schema = to_gemini_schema(full_schema_type, as_array=True)

    context_block = ""
    if context:
        context_block = (
            "The following fields are ALREADY KNOWN and will be filled in by "
            "the caller — DO NOT emit them in your output (they're not in the "
            "response schema):\n"
            f"- product_name: {context.get('product_name')!r}\n"
            f"- manufacturer: {context.get('manufacturer')!r}\n"
            f"- product_family: {context.get('product_family')!r}\n"
            f"- datasheet_url: {context.get('datasheet_url')!r}\n\n"
        )

    single_page_nudge = ""
    if context and context.get("single_page_mode"):
        single_page_nudge = (
            "You are analyzing a SINGLE PAGE of a larger datasheet. "
            "Extract only products whose specifications visibly appear on "
            "THIS page. If no product specs are visible, return an empty "
            "products array.\n\n"
        )

    prefix_block = f"{prompt_prefix}\n\n" if prompt_prefix else ""

    prompt = (
        f"{prefix_block}"
        "You are extracting product specifications from an industrial catalog.\n\n"
        f"{single_page_nudge}"
        f"{context_block}"
        "Emit one entry per distinct product VARIANT found in the document — "
        "a distinct part number, voltage class, or form factor is a separate "
        "entry. Leave optional fields unset (null / omitted) when the "
        "specification is genuinely absent from the document; do NOT fabricate "
        'values, and NEVER emit placeholder strings like "N/A", "TBD", '
        '"-", "None", "unknown", or "not applicable" — omit the field '
        "or set it to null instead.\n\n"
        "Numeric specs with units (rated_current, input_voltage, etc.) must be "
        "emitted as structured objects:\n"
        '- single-valued fields: {"value": <number>, "unit": <string>}\n'
        '- min/max fields:       {"min": <number>, "max": <number>, "unit": <string>}\n'
        "Emit plain numbers in the numeric fields — no '+', '~', or unit text "
        "in the value slot.\n\n"
        f"{GUARDRAILS}"
    )

    contents: list[Any] = []

    if content_type == "pdf":
        if not isinstance(doc_data, bytes):
            raise ValueError("PDF content must be bytes")
        contents = [
            genai.types.Part.from_bytes(
                data=doc_data,
                mime_type="application/pdf",
            ),
            prompt,
        ]
        logger.info(f"Analyzing PDF document ({len(doc_data)} bytes)")
    elif content_type == "image":
        if not isinstance(doc_data, bytes):
            raise ValueError("Image content must be bytes")
        if not mime_type or not mime_type.startswith("image/"):
            raise ValueError(
                "content_type='image' requires mime_type like 'image/png' "
                f"(got {mime_type!r})"
            )
        contents = [
            genai.types.Part.from_bytes(data=doc_data, mime_type=mime_type),
            prompt,
        ]
        logger.info(f"Analyzing {mime_type} image ({len(doc_data)} bytes)")
    elif content_type == "html":
        if not isinstance(doc_data, str):
            raise ValueError("HTML content must be string")
        contents = [f"HTML Content:\n\n{doc_data}\n\n{prompt}"]
        logger.info(f"Analyzing HTML content ({len(doc_data)} characters)")
    else:
        raise ValueError(f"Unsupported content_type: {content_type}")

    # Structured JSON output. The schema constrains Gemini so it can't
    # drop columns or emit wrong-type values.
    response: Any = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "response_schema": response_schema,
            "max_output_tokens": 65536,
        },
    )

    logger.debug(f"Full Gemini response: {response!r}")

    return response
