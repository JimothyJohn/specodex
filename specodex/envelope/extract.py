"""Gemini call that reads overall envelope (L×W×H) and weight per variant.

Input is a small PDF built from the selected pages of the source datasheet
(``build_page_subset``), not rendered JPEGs: Gemini reads the PDF's text
layer *and* the drawing, which matters for dimension tables where the
numbers are text and the meaning of "L" is in the picture.

Calls are chunked (``PAGES_PER_CALL``) so a dense catalog page set can't
overflow the response and truncate the JSON mid-string, and so each
reported ``page_number`` maps back to a real page unambiguously.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from google import genai
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from specodex.config import MODEL
from specodex.models.common import Length, Mass
from specodex.models.llm_schema import to_gemini_schema
from specodex.utils import parse_gemini_response

logger: logging.Logger = logging.getLogger(__name__)

PAGES_PER_CALL = 4


class EnvelopeExtraction(BaseModel):
    """One variant's outline envelope as printed on a datasheet page."""

    designator: Optional[str] = Field(
        None,
        description=(
            "The model code, frame size, or part number that labels this "
            "row/column of the dimension or spec table, copied exactly as "
            "printed (e.g. 'AB042', 'SGM7J-01', 'MR-J5-10A', '60')."
        ),
    )
    length: Length = Field(
        None,
        description=(
            "Overall length along the product's main axis (motor/gearhead: "
            "along the shaft, INCLUDING the output shaft; actuator: overall "
            "length; drive/cabinet unit: depth front-to-back)."
        ),
    )
    width: Length = Field(None, description="Overall width across the main axis.")
    height: Length = Field(None, description="Overall height across the main axis.")
    weight: Mass = Field(None, description="Product mass/weight for this variant.")
    page_number: int = Field(
        ...,
        description="The 1-indexed page of THIS document the values were read from.",
    )


_PROMPT = """You are reading pages from an industrial product datasheet or \
catalog (motors, servo drives, gearheads, actuators, robot arms, contactors, \
sensors). Extract the OVERALL PHYSICAL ENVELOPE of each product variant: its \
bounding-box length, width and height, and its weight.

Where to look: outline / dimensional drawings and the dimension tables next \
to them (columns like L, LL, LR, W, H, D, □, ØD, "A", "B"), and spec tables \
for weight / mass.

For EACH variant the pages dimension (a table row or column, or a single \
drawing for one model), emit one entry:

- designator: the model code / frame size / part number labelling that row \
  or column, copied exactly as printed. If one drawing covers a whole \
  series with a single set of numbers, use the series/model name printed in \
  the drawing title.
- length: overall length along the main axis. For motors and gearheads that \
  is the full length INCLUDING the output shaft (e.g. LL + LR if the table \
  splits body and shaft; use the overall "L" when printed). For drives and \
  panel-mounted units it is the depth (front to back).
- width, height: the overall outer extents across the main axis (for a \
  square flange both are the flange edge; for a round body both are the \
  outer diameter; for a drive the mounted width and height).
- weight: the product's mass for that variant (kg, g, lb...).
- page_number: the 1-indexed page within THIS document where you read it.

Rules:
- Overall envelope only. Do NOT report bolt circles, pilot/spigot \
  diameters, shaft diameters, keyways, or hole sizes as width/height.
- Use the unit printed on the drawing or table header (usually mm; inches \
  for many US catalogs). Put the plain number in value and the unit in unit.
- When a table gives several lengths for options (with/without brake, \
  1-stage/2-stage), emit one entry per option with its own designator text \
  as printed (e.g. 'AB042 1-stage').
- Leave a field null when the pages do not give it. Never estimate, never \
  scale off the drawing, never emit placeholders like "N/A" or "-".
- If no page has envelope dimensions or weights, return an empty list.
"""


def _should_retry(exc: BaseException) -> bool:
    from google.genai import errors as genai_errors

    return not isinstance(exc, genai_errors.ClientError)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=30),
    retry=retry_if_exception(_should_retry),
)
def _call_gemini(pdf_bytes: bytes, api_key: str, response_schema: dict) -> Any:
    client = genai.Client(api_key=api_key)
    return client.models.generate_content(
        model=MODEL,
        contents=[
            _PROMPT,
            genai.types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        ],
        config={
            "response_mime_type": "application/json",
            "response_schema": response_schema,
            "max_output_tokens": 16000,
        },
    )


def build_page_subset(pdf_bytes: bytes, pages: List[int]) -> bytes:
    """Return a new PDF holding ``pages`` (0-indexed, in order) of ``pdf_bytes``."""
    import fitz

    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        out = fitz.open()
        try:
            for p in pages:
                if 0 <= p < len(src):
                    out.insert_pdf(src, from_page=p, to_page=p)
            return out.tobytes(garbage=3, deflate=True)
        finally:
            out.close()
    finally:
        src.close()


def remap_pages(
    extractions: List[EnvelopeExtraction], page_numbers: List[int]
) -> List[EnvelopeExtraction]:
    """Rewrite each 1-indexed in-call ``page_number`` to the true 0-indexed
    source page; drop entries pointing outside the call's page set."""
    kept: List[EnvelopeExtraction] = []
    for e in extractions:
        idx = e.page_number - 1
        if 0 <= idx < len(page_numbers):
            e.page_number = page_numbers[idx]
            kept.append(e)
        else:
            logger.warning(
                "dropping extraction with out-of-range page_number=%d (call had %d pages)",
                e.page_number,
                len(page_numbers),
            )
    return kept


def extract_envelopes(
    pdf_bytes: bytes, pages: List[int], api_key: str
) -> List[EnvelopeExtraction]:
    """Extract envelopes from ``pages`` (0-indexed) of ``pdf_bytes``.

    One Gemini call per ``PAGES_PER_CALL`` chunk. A chunk that yields
    nothing usable is a normal outcome, not an error.
    """
    schema = to_gemini_schema(EnvelopeExtraction, as_array=True, include_excluded=True)
    results: List[EnvelopeExtraction] = []
    for start in range(0, len(pages), PAGES_PER_CALL):
        chunk = pages[start : start + PAGES_PER_CALL]
        subset = build_page_subset(pdf_bytes, chunk)
        response = _call_gemini(subset, api_key, schema)
        try:
            parsed = parse_gemini_response(
                response, EnvelopeExtraction, product_type="envelope"
            )
        except ValueError as e:
            logger.info("no envelope extractions on pages %s: %s", chunk, e)
            continue
        results.extend(remap_pages(parsed, chunk))
    return results
