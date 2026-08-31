"""
Page Finder — identifies which pages in a PDF contain specification tables.

Converts each page to a low-res JPEG thumbnail, sends to a fast/cheap model
(Gemini Flash) asking "does this page contain a specification table?",
and returns the page numbers that do.

Usage:
    uv run python -m specodex.page_finder --url "https://example.com/catalog.pdf"
    uv run python -m specodex.page_finder --url "https://example.com/catalog.pdf" --update-db
    uv run python -m specodex.page_finder --scan-all
"""

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai

from specodex.utils import get_document

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Use a cheap fast model for page classification
PAGE_FINDER_MODEL = "gemini-2.0-flash"


# Keywords that indicate a spec table. A page must match at least
# SPEC_KEYWORD_THRESHOLD distinct GROUPS to qualify. Each inner list is
# an OR group — matching any one string in a group counts as 1.
#
# Scope: industrial product specs across electronics, mechanics, and
# mechatronics — motors, drives, gearheads, contactors, relays, starters,
# linear actuators, robot arms, sensors, PLCs, power supplies, etc.
# Motor-shaped vocabulary is one slice, not the whole target.
SPEC_KEYWORDS: list[list[str]] = [
    # --- Electrical: voltage ---
    [
        "rated voltage",
        "supply voltage",
        "voltage range",
        "input voltage",
        "operational voltage",
        "coil voltage",
        "control voltage",
        "line voltage",
    ],
    # --- Electrical: current ---
    [
        "rated current",
        "continuous current",
        "operating current",
        "breaking capacity",
        "making current",
        "inrush current",
        "surge current",
        "short-circuit current",
        "thermal current",
    ],
    # --- Electrical: power / output ---
    [
        "rated power",
        "rated output",
        "output power",
        "power consumption",
        "apparent power",
        "motor capacity",
    ],
    # --- Electrical: frequency ---
    [
        "rated frequency",
        "switching frequency",
        "line frequency",
        "pwm frequency",
        "carrier frequency",
    ],
    # --- Electrical: insulation / dielectric ---
    [
        "rated insulation voltage",
        "withstand voltage",
        "dielectric strength",
        "impulse withstand",
        "insulation resistance",
        "pollution degree",
    ],
    # --- Motor: torque ---
    [
        "rated torque",
        "peak torque",
        "stall torque",
        "holding torque",
        "starting torque",
        "cogging torque",
    ],
    # --- Motor: speed ---
    [
        "rated speed",
        "max speed",
        "maximum speed",
        "no-load speed",
        "rated rotational speed",
    ],
    # --- Motor: constants / inertia ---
    [
        "rotor inertia",
        "moment of inertia",
        "torque constant",
        "voltage constant",
        "back emf",
        "thermal resistance",
    ],
    # --- Feedback / sensing elements ---
    ["encoder", "resolver", "feedback", "hall sensor", "tachometer"],
    # --- Signal I/O / communication ---
    [
        "digital input",
        "digital output",
        "analog input",
        "analog output",
        "communication protocol",
        "fieldbus",
    ],
    # --- Switching devices (contactors / relays / starters) ---
    [
        "pole configuration",
        "auxiliary contact",
        "number of poles",
        "utilization category",
        "pick-up voltage",
        "drop-out voltage",
        "mechanical durability",
        "electrical durability",
        "main contact",
        "zero voltage trigger",
    ],
    # --- Linear actuation ---
    [
        "stroke",
        "push force",
        "pull force",
        "thrust force",
        "linear speed",
        "positioning repeatability",
        "lead screw",
    ],
    # --- Rotary / gearing ---
    [
        "gear ratio",
        "backlash",
        "torsional rigidity",
        "transmission error",
        "allowable input speed",
    ],
    # --- Robotics ---
    [
        "payload",
        "reach",
        "degrees of freedom",
        "joint speed",
        "pose repeatability",
        "tcp speed",
    ],
    # --- Sensor / measurement ---
    [
        "accuracy class",
        "sensing range",
        "measurement range",
        "sensing distance",
        "response time",
        "hysteresis",
        "detection range",
        "sampling rate",
    ],
    # --- Environmental ---
    [
        "ip rating",
        "ingress protection",
        "operating temperature",
        "ambient temperature",
        "storage temperature",
        "humidity range",
        "vibration resistance",
        "shock resistance",
    ],
    # --- Physical / mechanical identity ---
    [
        "frame size",
        "flange size",
        "mounting type",
        "mounting hole",
        "shaft diameter",
    ],
    # --- Certifications / standards ---
    [
        "applicable standards",
        "standards compliance",
        "certifications",
        "ce marking",
        "rohs compliant",
    ],
]
SPEC_KEYWORD_THRESHOLD = 3  # Must match at least 3 distinct groups

# Scored page finder constants
_MIN_LINES_FOR_DENSITY = (
    15  # Pages shorter than this get density capped to avoid false positives
)
_MAX_PAGES_SMALL_DOC = 15  # Docs ≤ 20 pages: generous cap
_MAX_PAGES_LARGE_DOC = 20  # Docs > 20 pages: hard cap
_MIN_SCORE = 0.15  # Minimum composite score to be considered


def find_spec_pages_by_text(pdf_bytes: bytes) -> list[int]:
    """Find spec-table pages using text search — free, no API calls.

    Returns 0-indexed page numbers matching spec keyword heuristics.
    Falls back to empty list if PyMuPDF is unavailable or PDF has no text.
    """
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF not installed, skipping text-based page detection")
        return []

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    spec_pages: list[int] = []

    for i in range(total_pages):
        text = doc[i].get_text().lower()
        if not text.strip():
            continue
        matches = sum(1 for group in SPEC_KEYWORDS if any(kw in text for kw in group))
        if matches >= SPEC_KEYWORD_THRESHOLD:
            spec_pages.append(i)  # 0-indexed

    doc.close()
    logger.info(
        f"Text-based page detection: {len(spec_pages)}/{total_pages} pages "
        f"matched spec keywords"
    )
    return spec_pages


def _score_page(text: str, tables: list) -> dict:
    """Score a single page for spec-table likelihood.

    Returns a dict with the composite score and component signals.
    """
    lines = [line for line in text.split("\n") if line.strip()]
    n_lines = max(len(lines), 1)
    text_lower = text.lower()

    groups_matched = sum(
        1 for group in SPEC_KEYWORDS if any(kw in text_lower for kw in group)
    )
    keyword_hits = sum(
        sum(1 for kw in group if kw in text_lower) for group in SPEC_KEYWORDS
    )

    total_cells = sum(t.row_count * t.col_count for t in tables)
    n_tables = len(tables)

    raw_density = keyword_hits / n_lines
    # Penalize tiny pages that inflate density with a single keyword mention
    if n_lines < _MIN_LINES_FOR_DENSITY:
        raw_density *= n_lines / _MIN_LINES_FOR_DENSITY

    group_coverage = groups_matched / len(SPEC_KEYWORDS)
    table_signal = min(total_cells / 200.0, 1.0)

    composite = (
        min(raw_density / 0.08, 1.0) * 0.25
        + group_coverage * 0.35
        + table_signal * 0.40
    )

    return {
        "groups_matched": groups_matched,
        "keyword_hits": keyword_hits,
        "n_lines": n_lines,
        "keyword_density": round(raw_density, 4),
        "n_tables": n_tables,
        "table_cells": total_cells,
        "score": round(composite, 4),
    }


def find_spec_pages_scored(
    pdf_bytes: bytes,
    max_pages: int | None = None,
    min_score: float = _MIN_SCORE,
) -> tuple[list[int], list[dict]]:
    """Find spec pages using density scoring + table detection.

    Scores every page by keyword density, keyword group breadth, and
    table cell count. Returns the top pages above the minimum score
    threshold, capped at max_pages.

    Args:
        pdf_bytes: Raw PDF bytes.
        max_pages: Override the adaptive page cap. None = auto.
        min_score: Minimum composite score to include a page.

    Returns:
        Tuple of (page_numbers 0-indexed sorted, page_details for all pages).
    """
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF not installed, falling back to text-only heuristic")
        pages = find_spec_pages_by_text(pdf_bytes)
        return pages, []

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)

    if max_pages is None:
        max_pages = _MAX_PAGES_SMALL_DOC if total_pages <= 20 else _MAX_PAGES_LARGE_DOC

    page_scores: list[dict] = []
    for i in range(total_pages):
        text = doc[i].get_text()
        if not text.strip():
            page_scores.append({"page": i, "score": 0.0, "empty": True})
            continue
        try:
            tables = doc[i].find_tables().tables
        except Exception:
            tables = []
        info = _score_page(text, tables)
        info["page"] = i
        page_scores.append(info)

    doc.close()

    # Select pages above threshold, ranked by score, capped
    candidates = [p for p in page_scores if p.get("score", 0) >= min_score]
    candidates.sort(key=lambda p: -p["score"])
    selected = candidates[:max_pages]
    # Return in document order
    selected_pages = sorted(p["page"] for p in selected)

    old_count = sum(
        1 for p in page_scores if p.get("groups_matched", 0) >= SPEC_KEYWORD_THRESHOLD
    )

    logger.info(
        f"Scored page detection: {len(selected_pages)}/{total_pages} pages selected "
        f"(was {old_count} with binary heuristic, cap={max_pages})"
    )

    return selected_pages, page_scores


# ---------------------------------------------------------------------------
# Dimensional-drawing page detection — mounting-flange geometry (bolt
# circle diameter, pilot diameter, shaft length/modification, ...) lives
# on mechanical/dimensional-drawing pages, not the spec-table pages
# SPEC_KEYWORDS targets above. Free/no-API-call, same text-heuristic +
# composite-score pattern as the spec-table finder. These are frequently
# DIFFERENT pages than a product's already-cached spec pages, so callers
# should re-scan the full PDF rather than filtering a prior page list.
# See specodex/mounting/extract.py for the extraction pass that consumes
# the page numbers these functions return.
# ---------------------------------------------------------------------------

DRAWING_KEYWORDS: list[list[str]] = [
    [
        "dimensional drawing",
        "outline drawing",
        "installation dimensions",
        "external dimensions",
        "mounting dimensions",
    ],
    [
        "bolt circle",
        "b.c.d",
        "bcd",
        "pcd",
        "bolt hole circle",
    ],
    [
        "keyway",
        "shaft key",
        "d-cut",
        "shaft end",
        "shaft extension",
    ],
    [
        "pilot diameter",
        "spigot",
        "register diameter",
        "rabbet",
        "flange dimensions",
    ],
]
DRAWING_KEYWORD_THRESHOLD = 2  # Must match at least 2 distinct groups

_MIN_DRAWING_SCORE = 0.15


def find_drawing_pages_by_text(pdf_bytes: bytes) -> list[int]:
    """Find dimensional/mechanical-drawing pages using text search — free,
    no API calls.

    Mirrors `find_spec_pages_by_text` but targets the drawing-page
    vocabulary in `DRAWING_KEYWORDS` instead of `SPEC_KEYWORDS`.

    Returns 0-indexed page numbers. Falls back to empty list if
    PyMuPDF is unavailable or the PDF has no extractable text.
    """
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF not installed, skipping text-based drawing detection")
        return []

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    drawing_pages: list[int] = []

    for i in range(total_pages):
        text = doc[i].get_text().lower()
        if not text.strip():
            continue
        matches = sum(
            1 for group in DRAWING_KEYWORDS if any(kw in text for kw in group)
        )
        if matches >= DRAWING_KEYWORD_THRESHOLD:
            drawing_pages.append(i)  # 0-indexed

    doc.close()
    logger.info(
        f"Text-based drawing detection: {len(drawing_pages)}/{total_pages} pages "
        f"matched drawing keywords"
    )
    return drawing_pages


def _score_drawing_page(text: str, drawings_count: int) -> dict:
    """Score a single page for dimensional-drawing likelihood.

    Mirrors `_score_page`'s composite-score shape but substitutes a
    vector-path count (line art) for the table-cell signal: a
    dimensional drawing is the near-opposite of a spec table — sparse
    text, dense line art.
    """
    lines = [line for line in text.split("\n") if line.strip()]
    n_lines = max(len(lines), 1)
    text_lower = text.lower()

    groups_matched = sum(
        1 for group in DRAWING_KEYWORDS if any(kw in text_lower for kw in group)
    )
    keyword_hits = sum(
        sum(1 for kw in group if kw in text_lower) for group in DRAWING_KEYWORDS
    )

    raw_density = keyword_hits / n_lines
    if n_lines < _MIN_LINES_FOR_DENSITY:
        raw_density *= n_lines / _MIN_LINES_FOR_DENSITY

    group_coverage = groups_matched / len(DRAWING_KEYWORDS)
    # Drawing-heavy pages carry many vector paths (line art) with very
    # little text — the inverse of the table-cell signal used for
    # spec pages.
    vector_signal = min(drawings_count / 150.0, 1.0)

    composite = (
        min(raw_density / 0.08, 1.0) * 0.20
        + group_coverage * 0.35
        + vector_signal * 0.45
    )

    return {
        "groups_matched": groups_matched,
        "keyword_hits": keyword_hits,
        "n_lines": n_lines,
        "keyword_density": round(raw_density, 4),
        "drawings_count": drawings_count,
        "score": round(composite, 4),
    }


def find_drawing_pages_scored(
    pdf_bytes: bytes,
    max_pages: int | None = None,
    min_score: float = _MIN_DRAWING_SCORE,
) -> tuple[list[int], list[dict]]:
    """Find dimensional-drawing pages using keyword + vector-path scoring.

    Mirrors `find_spec_pages_scored`, substituting PyMuPDF's
    `page.get_drawings()` path count for the table-cell signal. Unlike
    the spec-table finder, a page with no extractable text is still
    scored (on vector-path density alone) rather than zero-scored — a
    drawing page can be little more than a title block and dimension
    callouts rendered as vector paths.

    Args:
        pdf_bytes: Raw PDF bytes.
        max_pages: Override the adaptive page cap. None = auto.
        min_score: Minimum composite score to include a page.

    Returns:
        Tuple of (page_numbers 0-indexed sorted, page_details for all pages).
    """
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF not installed, falling back to text-only heuristic")
        pages = find_drawing_pages_by_text(pdf_bytes)
        return pages, []

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)

    if max_pages is None:
        max_pages = _MAX_PAGES_SMALL_DOC if total_pages <= 20 else _MAX_PAGES_LARGE_DOC

    page_scores: list[dict] = []
    for i in range(total_pages):
        text = doc[i].get_text()
        try:
            drawings_count = len(doc[i].get_drawings())
        except Exception:
            drawings_count = 0
        info = _score_drawing_page(text, drawings_count)
        info["page"] = i
        if not text.strip():
            info["empty_text"] = True
        page_scores.append(info)

    doc.close()

    candidates = [p for p in page_scores if p.get("score", 0) >= min_score]
    candidates.sort(key=lambda p: -p["score"])
    selected = candidates[:max_pages]
    selected_pages = sorted(p["page"] for p in selected)

    logger.info(
        f"Scored drawing detection: {len(selected_pages)}/{total_pages} pages selected "
        f"(cap={max_pages})"
    )

    return selected_pages, page_scores


def pdf_pages_to_images(pdf_bytes: bytes, dpi: int = 100) -> List[bytes]:
    """Convert each page of a PDF to a JPEG image.

    Args:
        pdf_bytes: Raw PDF file bytes
        dpi: Resolution for rendering (lower = faster, 100 is fine for table detection)

    Returns:
        List of JPEG bytes, one per page
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF (fitz) is required. Install with: pip install PyMuPDF")
        raise

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images: List[bytes] = []

    zoom = dpi / 72  # 72 is the default PDF DPI
    matrix = fitz.Matrix(zoom, zoom)

    for page_num in range(len(doc)):
        page = doc[page_num]
        pix = page.get_pixmap(matrix=matrix)

        # Convert to JPEG bytes
        img_bytes = pix.tobytes(output="jpeg", jpg_quality=60)
        images.append(img_bytes)

    doc.close()
    return images


def classify_pages(
    images: List[bytes],
    api_key: str,
    product_type: str = "",
    batch_size: int = 5,
) -> List[Dict[str, Any]]:
    """Send page images to Gemini Flash to classify which contain spec tables.

    Args:
        images: List of JPEG bytes per page
        api_key: Gemini API key
        product_type: Hint about what kind of specs to look for
        batch_size: Number of pages to send per API call

    Returns:
        List of dicts with page_number (0-indexed), has_specs (bool), description
    """
    client = genai.Client(api_key=api_key)
    results: List[Dict[str, Any]] = []

    type_hint = f" for {product_type} products" if product_type else ""

    for batch_start in range(0, len(images), batch_size):
        batch_end = min(batch_start + batch_size, len(images))
        batch = images[batch_start:batch_end]

        page_numbers = list(range(batch_start, batch_end))
        page_labels = ", ".join(str(p + 1) for p in page_numbers)

        prompt = f"""You are looking at {len(batch)} pages from a product catalog/datasheet{type_hint}.
These are pages {page_labels} (1-indexed).

For EACH page, determine:
1. Does it contain a specification table, data table, or detailed technical parameters?
   - YES if: spec sheets, parameter tables, performance curves with data, comparison tables
   - NO if: cover pages, table of contents, marketing text, photos without specs, ordering info, blank pages, legal text

2. Brief description of what the page contains (5-10 words max).

Respond as a JSON array with one object per page:
[{{"page": 1, "has_specs": true, "description": "Motor rated torque table"}}]
"""

        # Build content parts
        contents: list[Any] = [prompt]
        for i, img_bytes in enumerate(batch):
            contents.append(
                genai.types.Part.from_bytes(
                    data=img_bytes,
                    mime_type="image/jpeg",
                )
            )

        try:
            response = client.models.generate_content(
                model=PAGE_FINDER_MODEL,
                contents=contents,
                config={
                    "response_mime_type": "application/json",
                },
            )

            if response.text:
                batch_results = json.loads(response.text)
                if isinstance(batch_results, list):
                    for item in batch_results:
                        # Convert 1-indexed page from LLM to 0-indexed
                        page_1idx = item.get("page", 0)
                        page_0idx = page_1idx - 1 + batch_start
                        results.append(
                            {
                                "page_number": page_0idx,
                                "page_display": page_1idx + batch_start,
                                "has_specs": item.get("has_specs", False),
                                "description": item.get("description", ""),
                            }
                        )
        except Exception as e:
            logger.error(f"Error classifying pages {page_labels}: {e}")
            # Mark failed pages as unknown
            for p in page_numbers:
                results.append(
                    {
                        "page_number": p,
                        "page_display": p + 1,
                        "has_specs": False,
                        "description": f"classification failed: {e}",
                    }
                )

    return results


def find_spec_pages(
    url: str,
    api_key: str,
    product_type: str = "",
    pages: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Find which pages in a PDF contain specification tables.

    Args:
        url: URL or local path to PDF
        api_key: Gemini API key
        product_type: Type of product (motor, drive, gearhead, robot_arm)
        pages: Optional subset of pages to check (0-indexed)

    Returns:
        Dict with spec_pages (0-indexed list), all_pages (full classification)
    """
    logger.info(f"Downloading PDF from {url}")
    pdf_bytes = get_document(url)

    if not pdf_bytes:
        raise ValueError(f"Could not download PDF from {url}")

    logger.info(f"PDF downloaded: {len(pdf_bytes)} bytes")

    logger.info("Converting pages to images...")
    all_images = pdf_pages_to_images(pdf_bytes)
    logger.info(f"Converted {len(all_images)} pages to images")

    # If specific pages requested, only classify those
    if pages:
        images_to_check = [all_images[p] for p in pages if p < len(all_images)]
        page_mapping = [p for p in pages if p < len(all_images)]
    else:
        images_to_check = all_images
        page_mapping = list(range(len(all_images)))

    logger.info(f"Classifying {len(images_to_check)} pages...")
    classifications = classify_pages(images_to_check, api_key, product_type)

    # Map classifications back to actual page numbers
    for i, cls in enumerate(classifications):
        if i < len(page_mapping):
            cls["page_number"] = page_mapping[i]
            cls["page_display"] = page_mapping[i] + 1

    spec_pages = [c["page_number"] for c in classifications if c.get("has_specs")]

    return {
        "url": url,
        "total_pages": len(all_images),
        "spec_pages": spec_pages,
        "spec_page_count": len(spec_pages),
        "all_pages": classifications,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find which pages in a PDF contain specification tables.",
    )
    parser.add_argument("--url", help="PDF URL or local path")
    parser.add_argument(
        "--type", help="Product type hint (motor, drive, gearhead, robot_arm)"
    )
    parser.add_argument(
        "--x-api-key",
        help="Gemini API key (or set GEMINI_API_KEY env var)",
    )
    parser.add_argument(
        "--update-db",
        action="store_true",
        help="Update the datasheet entry in DynamoDB with found pages",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan all datasheets in the DB that have no pages set",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Save results to JSON file",
    )

    args = parser.parse_args()
    api_key = args.x_api_key or os.environ.get("GEMINI_API_KEY")

    if not api_key:
        parser.error("Gemini API key required (--x-api-key or GEMINI_API_KEY env var)")

    if args.scan_all:
        _scan_all_datasheets(api_key, args.type, args.update_db)
        return

    if not args.url:
        parser.error("--url is required (or use --scan-all)")

    result = find_spec_pages(args.url, api_key, args.type or "")

    # Print results
    print(f"\n{'=' * 60}")
    print(f"PDF: {result['url']}")
    print(f"Total pages: {result['total_pages']}")
    print(f"Pages with specs: {result['spec_page_count']}")
    print(f"Spec pages (0-indexed): {result['spec_pages']}")
    print(f"{'=' * 60}")

    for page in result["all_pages"]:
        marker = "[SPEC]" if page["has_specs"] else "      "
        print(f"  {marker} Page {page['page_display']:3d}: {page['description']}")

    if args.output:
        args.output.write_text(json.dumps(result, indent=2))
        print(f"\nResults saved to {args.output}")

    if args.update_db and result["spec_pages"]:
        _update_datasheet_pages(args.url, result["spec_pages"])


def _update_datasheet_pages(url: str, pages: List[int]) -> None:
    """Update a datasheet's pages field in DynamoDB."""
    from specodex.db.dynamo import DynamoDBClient

    client = DynamoDBClient()

    # Find the datasheet by URL
    datasheets = client.get_all_datasheets()
    matching = [ds for ds in datasheets if ds.url == url]

    if not matching:
        logger.warning(f"No datasheet found in DB for URL: {url}")
        return

    for ds in matching:
        ds.pages = pages
        if client.create(ds):  # PutItem overwrites
            logger.info(f"Updated datasheet {ds.datasheet_id} with pages {pages}")
        else:
            logger.error(f"Failed to update datasheet {ds.datasheet_id}")


def _scan_all_datasheets(
    api_key: str, product_type: Optional[str], update_db: bool
) -> None:
    """Scan all datasheets and find spec pages for PDFs missing page info."""
    from specodex.db.dynamo import DynamoDBClient
    from specodex.utils import is_pdf_url

    client = DynamoDBClient()
    datasheets = client.get_all_datasheets()

    # Filter to PDFs that need page finding
    candidates = []
    for ds in datasheets:
        if not ds.url:
            continue
        if not is_pdf_url(ds.url):
            continue
        # Only process if pages are empty or just [0,1]
        if ds.pages and len(ds.pages) > 2:
            continue
        if product_type and ds.product_type != product_type:
            continue
        candidates.append(ds)

    logger.info(f"Found {len(candidates)} datasheets needing page discovery")

    for i, ds in enumerate(candidates):
        logger.info(
            f"[{i + 1}/{len(candidates)}] Processing: {ds.product_name} ({ds.url[:60]}...)"
        )
        try:
            result = find_spec_pages(ds.url, api_key, ds.product_type)

            spec_pages = result["spec_pages"]
            print(f"  Found {len(spec_pages)} spec pages: {spec_pages}")

            if update_db and spec_pages:
                _update_datasheet_pages(ds.url, spec_pages)

        except Exception as e:
            logger.error(f"  Failed: {e}")
            continue


if __name__ == "__main__":
    main()
