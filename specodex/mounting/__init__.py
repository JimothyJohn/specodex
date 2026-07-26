"""Dimensional-drawing mounting-dimension extraction.

Separate from the main ``specodex.llm`` / ``specodex.scraper`` pipeline
because the source pages are different: mounting-flange geometry (bolt
circle diameter, pilot diameter, shaft length, shaft modification, ...)
lives on mechanical/dimensional-drawing pages, which
``specodex.page_finder``'s spec-table heuristics deliberately skip. See
``specodex.page_finder.find_drawing_pages_scored`` for the page-finder
half and ``specodex.mounting.extract`` for the extraction call.
"""

from specodex.mounting.extract import (
    MotorMountingExtraction,
    extract_mounting_dimensions,
)

__all__ = [
    "MotorMountingExtraction",
    "extract_mounting_dimensions",
]
