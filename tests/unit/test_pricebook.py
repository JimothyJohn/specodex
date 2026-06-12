"""Example-based tests for specodex.pricing.pricebook.

The property companion is ``test_pricebook_property.py`` — this file
pins the happy path and the specific shapes seen in real price books
(the ABB/Baldor 501 Index layout, sparse rows, duplicate PNs).
"""

from __future__ import annotations

import zipfile
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

import pytest

from specodex.models.drive import Drive
from specodex.pricing.pricebook import (
    PricePair,
    _column_index,
    _find_header,
    join_pairs,
    pairs_from_xlsx,
    parse_xlsx_rows,
)

_SHEET_HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    "<sheetData>"
)
_SHEET_FOOTER = "</sheetData></worksheet>"


def make_xlsx(rows: list[list[object]], shared: list[str] | None = None) -> bytes:
    """Build a minimal XLSX. Strings become inline strings; ints/floats
    become numeric cells — close enough to what Excel emits."""
    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(ord('A') + c)}{r}"
            if value is None:
                continue
            if isinstance(value, str):
                cells.append(
                    f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
                )
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    sheet = _SHEET_HEADER + "".join(body) + _SHEET_FOOTER

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types '
            'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
        if shared:
            items = "".join(f"<si><t>{s}</t></si>" for s in shared)
            zf.writestr(
                "xl/sharedStrings.xml",
                '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/'
                f'spreadsheetml/2006/main">{items}</sst>',
            )
    return buf.getvalue()


# The Baldor 501 Index layout: junk rows above the header, numeric prices.
BALDOR_SHAPED = [
    ["—"],
    ["Index"],
    [],
    ["Catalog Number", "List Price", "Disc. Sym.", "Ship Wgt. (1)", "Type Code"],
    ["09-1309", 478, "SVC", 21, "–"],
    ["EM3546T", 1031, "A1", 66, "0532M"],
    ["BAD-ROW", "n/a", "", "", ""],
    ["L1322T", 5, "A1", 30, ""],  # below $10 band → rejected
]


class TestColumnIndex:
    def test_single_letter(self):
        assert _column_index("A5") == 0
        assert _column_index("C12") == 2

    def test_double_letter(self):
        assert _column_index("AB12") == 27

    def test_no_letters(self):
        assert _column_index("12") is None


class TestParseXlsxRows:
    def test_baldor_shaped_layout(self):
        rows = parse_xlsx_rows(make_xlsx(BALDOR_SHAPED))
        assert rows[3][0] == "Catalog Number"
        assert rows[4] == ["09-1309", "478", "SVC", "21", "–"]

    def test_sparse_row_stays_column_aligned(self):
        # Cell only in column C — A and B must pad to empty.
        xlsx = make_xlsx([[None, None, "only-c"]])
        rows = parse_xlsx_rows(xlsx)
        assert rows[0] == ["", "", "only-c"]

    def test_shared_strings_resolve(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "xl/worksheets/sheet1.xml",
                _SHEET_HEADER
                + '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
                + _SHEET_FOOTER,
            )
            zf.writestr(
                "xl/sharedStrings.xml",
                '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main"><si><t>hello</t></si></sst>',
            )
        assert parse_xlsx_rows(buf.getvalue()) == [["hello"]]

    def test_not_a_zip_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_xlsx_rows(b"%PDF-1.4 not an xlsx")

    def test_zip_without_sheet_raises_value_error(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("unrelated.txt", "x")
        with pytest.raises(ValueError):
            parse_xlsx_rows(buf.getvalue())


class TestFindHeader:
    def test_auto_detect_baldor_header(self):
        rows = parse_xlsx_rows(make_xlsx(BALDOR_SHAPED))
        idx, pn, price = _find_header(rows, None, None)
        assert (idx, pn, price) == (3, 0, 1)

    def test_explicit_headers_win(self):
        rows = [["Model", "List", "Price Notes"]]
        idx, pn, price = _find_header(rows, "Model", "List")
        assert (idx, pn, price) == (0, 0, 1)

    def test_no_header_raises(self):
        with pytest.raises(ValueError, match="no header row"):
            _find_header([["a", "b"], ["1", "2"]], None, None)


class TestPairsFromXlsx:
    def test_extracts_in_band_pairs_only(self):
        pairs = pairs_from_xlsx(make_xlsx(BALDOR_SHAPED))
        assert pairs == [
            PricePair("09-1309", Decimal("478")),
            PricePair("EM3546T", Decimal("1031")),
        ]


def _drive(pn: str | None, msrp: str | None = None) -> Drive:
    return Drive(
        product_type="drive",
        product_name=f"drive {pn}",
        manufacturer="TestCo",
        part_number=pn,
        msrp=msrp,
    )


class TestJoinPairs:
    def test_matches_normalized_part_number(self):
        pairs = [PricePair("EM-3546T", Decimal("1031"))]
        products = [_drive("em3546t")]
        matches = join_pairs(pairs, products)
        assert len(matches) == 1
        assert matches[0].pair.price_usd == Decimal("1031")

    def test_never_matches_already_priced(self):
        pairs = [PricePair("X100", Decimal("100"))]
        assert join_pairs(pairs, [_drive("X100", msrp="50;USD")]) == []

    def test_skips_products_without_part_number(self):
        pairs = [PricePair("X100", Decimal("100"))]
        assert join_pairs(pairs, [_drive(None)]) == []

    def test_templated_part_number_never_matches(self):
        # Allen-Bradley-style option template: the book carries concrete
        # PNs; the DB row carries a pattern. No match is the contract.
        pairs = [PricePair("21G11AF960JN0NNNNN", Decimal("4000"))]
        assert join_pairs(pairs, [_drive("21G11*F960JNONNNNN")]) == []

    def test_duplicate_pn_first_price_wins(self):
        pairs = [
            PricePair("X100", Decimal("100")),
            PricePair("X100", Decimal("999")),
        ]
        matches = join_pairs(pairs, [_drive("X100")])
        assert matches[0].pair.price_usd == Decimal("100")

    def test_short_part_numbers_never_join(self):
        # Regression (2026-06-11): ABB ACQ580 rows carry frame-size codes
        # ("R1", "R2") in part_number; the Baldor 501 book has short
        # catalog numbers that collided with them. Keys shorter than
        # MIN_JOIN_KEY_LEN are too ambiguous to join on either side.
        pairs = [PricePair("R2", Decimal("57")), PricePair("ABC", Decimal("99"))]
        assert join_pairs(pairs, [_drive("R2"), _drive("abc")]) == []
