"""Regression: proximity-sensor spec pages must clear the keyword gate.

Before the proximity keyword groups were added, the page finder matched
1/36 pages of the Balluff Global inductive brochure and 0/4 of the
Keyence EZ datasheet — sensor tables use a vocabulary (PNP/NPN, flush /
shielded, detecting distance) none of the motion-control groups carry.
The page text below is the wording those two documents print.
"""

import fitz
import pytest

from specodex.page_finder import find_spec_pages_by_text

BALLUFF_SELECTION_PAGE = [
    "DC 3-wire - M8",
    "Housing size M8x1",
    "Mounting Flush",
    "Rated operating distance Sn 1.5 mm",
    "Assured operating distance Sa 0...1.2 mm",
    "Cable length and material 3m PVC",
    "PNP Normally open BES0038",
    "NPN Normally closed BES03PJ",
]

KEYENCE_SPEC_PAGE = [
    "DC 3-wire type",
    "Type Shielded",
    "Model NPN EZ-8M EZ-12M",
    "Detecting distance 1.5 mm",
    "Response frequency 800 Hz",
    "Control output NPN open collector 100 mA max.",
    "Power supply 12 to 24 VDC",
    "Enclosure rating IP67",
]

MARKETING_PAGE = [
    "More than 45 years of know-how integrated into one sensor",
    "Clamps hold the workpiece in place during machining.",
    "The sensor housing has a non-stick coating.",
]


def _pdf(*pages: list[str]) -> bytes:
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        page.insert_text((40, 60), "\n".join(lines), fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.unit
class TestProximitySensorPages:
    def test_selection_table_page_matches(self):
        assert find_spec_pages_by_text(_pdf(BALLUFF_SELECTION_PAGE)) == [0]

    def test_spec_table_page_matches(self):
        assert find_spec_pages_by_text(_pdf(KEYENCE_SPEC_PAGE)) == [0]

    def test_marketing_page_still_skipped(self):
        pdf = _pdf(MARKETING_PAGE, KEYENCE_SPEC_PAGE, MARKETING_PAGE)
        assert find_spec_pages_by_text(pdf) == [1]
