"""Gemini call that reads motor mounting-flange dimensions off a
dimensional/mechanical-drawing page.

Candidate pages come from ``specodex.page_finder.find_drawing_pages_scored``
(or the text-only ``find_drawing_pages_by_text``) — NOT the spec-table
pages already cached for a product's main extraction. A single drawing
page frequently documents several frame-size variants at once (a "L / LL
/ S / LR" style dimension table next to the drawing), so the response is
a list, one entry per variant shown.

Reuses the existing ``to_gemini_schema`` / ``parse_gemini_response``
machinery rather than routing through ``specodex.llm.generate_content``,
since that function is coupled to ``SCHEMA_CHOICES`` product-type keys
and ``MotorMountingExtraction`` isn't a registered product type.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from google import genai
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from specodex.config import MODEL
from specodex.models.common import FlangeMountingDimensions, Length, ShaftModification
from specodex.models.llm_schema import to_gemini_schema
from specodex.utils import parse_gemini_response

logger: logging.Logger = logging.getLogger(__name__)


class MotorMountingExtraction(BaseModel):
    """One motor-frame variant's mounting geometry, read off a drawing.

    ``page_number`` is 0-indexed into the ORIGINAL PDF (already remapped
    by ``extract_mounting_dimensions`` — the LLM only ever sees a
    1-indexed position within the image set it was handed).
    """

    frame_designator: Optional[str] = Field(
        None,
        description=(
            "The frame size / model designator printed next to this row "
            "or variant in the drawing (e.g. 'NEMA 23', '60mm', 'Frame 143T')."
        ),
    )
    mounting_flange: Optional[FlangeMountingDimensions] = None
    shaft_diameter: Length = None
    shaft_length: Length = None
    shaft_modification: Optional[List[ShaftModification]] = None
    page_number: int = Field(
        ...,
        description=(
            "The 1-indexed position of the image (labeled 'Page N' in "
            "this message) this entry was read from."
        ),
    )


def _should_retry(exc: BaseException) -> bool:
    """Retry transient transport errors; bail on 4xx client errors."""
    from google.genai import errors as genai_errors

    if isinstance(exc, genai_errors.ClientError):
        return False
    return True


_PROMPT = """You are reading a mechanical/dimensional drawing from an industrial \
motor catalog. The drawing shows the motor's mounting flange (the face that \
bolts to a gearbox or bracket) and its output shaft.

For EACH distinct frame size / model variant shown (a drawing often covers \
several sizes in one table, e.g. columns or rows labeled with different \
frame designators), extract:

- frame_designator: the exact text used to label this variant (frame size, \
  NEMA/IEC designator, or model number) as printed on the drawing.
- mounting_flange.bolt_circle_diameter: the bolt circle diameter (BCD/PCD) \
  — the diameter of the circle the mounting bolts sit on.
- mounting_flange.bolt_hole_diameter: the THRU-HOLE clearance diameter for \
  the mounting bolts (not the thread size of the bolt itself).
- mounting_flange.bolt_hole_count: how many mounting bolt holes.
- mounting_flange.pilot_diameter: the register/spigot/rabbet diameter — \
  the small stepped diameter that centers the motor on the mating flange.
- mounting_flange.pilot_depth: how deep that register/spigot protrudes, \
  if dimensioned.
- mounting_flange.mounting_standard: "NEMA", "IEC", "JIS", "DIN", or \
  "proprietary" — only if the drawing states or clearly implies a standard \
  (e.g. a NEMA frame designator implies "NEMA"). Leave unset if ambiguous.
- shaft_diameter: the output shaft's outer diameter.
- shaft_length: the shaft's exposed/usable length.
- shaft_modification: one or more of "none" (plain/smooth shaft), \
  "keyway", "double_keyway", "flat" (single D-cut), "double_flat", \
  "splined", "tapped_hole" (threaded center hole in the shaft end), \
  "tapered", "other" — based on what the drawing actually depicts. Do not \
  guess "keyway" just because it's common; only emit a value the drawing \
  visibly shows.
- page_number: the 1-indexed page label ("Page N") this variant's \
  dimensions came from, exactly as shown in this message.

Leave a field unset (null / omitted) when the drawing does not dimension \
it — do NOT fabricate a plausible-looking value, and never emit placeholder \
text like "N/A", "TBD", or "-". Emit plain numbers with no unit text or \
qualifiers in numeric fields; the unit goes in the paired unit field.

If a page shows no mounting/shaft dimensions at all, return an empty list \
for that page's variants.
"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=30),
    retry=retry_if_exception(_should_retry),
)
def _call_gemini(page_images: List[bytes], api_key: str, response_schema: dict) -> Any:
    client = genai.Client(api_key=api_key)

    contents: list[Any] = [_PROMPT]
    for i, img_bytes in enumerate(page_images, start=1):
        contents.append(f"Page {i}:")
        contents.append(
            genai.types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
        )

    return client.models.generate_content(
        model=MODEL,
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "response_schema": response_schema,
            "max_output_tokens": 16000,
        },
    )


def extract_mounting_dimensions(
    page_images: List[bytes],
    page_numbers: List[int],
    api_key: str,
) -> List[MotorMountingExtraction]:
    """Extract mounting-flange geometry from a set of candidate drawing pages.

    Args:
        page_images: JPEG bytes, one per candidate page (see
            ``specodex.page_finder.pdf_pages_to_images``).
        page_numbers: The TRUE 0-indexed PDF page number each entry in
            ``page_images`` came from — same length/order as
            ``page_images``. Used to remap the LLM's 1-indexed
            "Page N" labels back to real page numbers.
        api_key: Gemini API key.

    Returns one ``MotorMountingExtraction`` per frame-size/variant found
    across all pages, with ``page_number`` already remapped to the true
    PDF page number. Returns an empty list if no page yielded a usable
    extraction — this is a normal outcome (candidate pages selected by
    the drawing-page heuristic are not guaranteed to carry mounting
    dimensions), not an error.
    """
    if len(page_images) != len(page_numbers):
        raise ValueError(
            f"page_images ({len(page_images)}) and page_numbers "
            f"({len(page_numbers)}) must be the same length"
        )
    if not page_images:
        return []

    response_schema = to_gemini_schema(
        MotorMountingExtraction, as_array=True, include_excluded=True
    )
    response = _call_gemini(page_images, api_key, response_schema)

    try:
        results = parse_gemini_response(
            response, MotorMountingExtraction, product_type="motor_mounting"
        )
    except ValueError as e:
        logger.info("No mounting-dimension extractions on this page set: %s", e)
        return []

    remapped: List[MotorMountingExtraction] = []
    for r in results:
        idx = r.page_number - 1
        if 0 <= idx < len(page_numbers):
            r.page_number = page_numbers[idx]
        else:
            logger.warning(
                "Extraction reported out-of-range page_number=%d for a "
                "%d-image call; dropping (can't trust the page mapping)",
                r.page_number,
                len(page_images),
            )
            continue
        remapped.append(r)

    return remapped
