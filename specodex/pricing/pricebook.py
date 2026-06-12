"""Price-book ingestion: bulk (part number → USD list price) extraction.

Industrial OEMs publish list-price books publicly — ABB/Baldor's "501
Stock Product Price File" is an XLSX of catalog number → list price;
WEG, KB Electronics, and Dart Controls publish price PDFs. One book
covers hundreds-to-thousands of part numbers in a single run, which is
why catalog-printed prices are the only source that has ever populated
``msrp`` at scale (see todo/PRICING.md).

Two parse paths, one join:

- **XLSX** — minimal stdlib reader (zipfile + ElementTree, first
  worksheet only). Header row is auto-detected by looking for a
  part-number-ish column and a price-ish column; both are overridable.
  No spreadsheet dependency: the format is a zip of XML and we only
  need two columns out of it.
- **PDF** — PyMuPDF page text → price-page heuristic → Gemini with a
  ``response_schema`` of (part_number, price_usd) pairs, per page.
- **Join** — ``normalize_string(part_number)`` equality against DB
  rows missing ``msrp``. Templated part numbers (``21G11*F9...``)
  simply never match, which is the correct outcome.

Prices outside the ``[$10, $100K]`` band are rejected — same guard as
the live-crawl extractor (see ``specodex.pricing.extract``).
"""

from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from typing import List, Optional, Sequence, Tuple
from xml.etree import ElementTree

from pydantic import BaseModel, Field

from specodex.ids import normalize_string
from specodex.models.product import ProductBase
from specodex.pricing.extract import PRICE_MAX, PRICE_MIN, _parse_bare_decimal

logger = logging.getLogger(__name__)

_SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Header tokens for auto-detection, lowercase, checked with `in`.
_PN_HEADER_TOKENS = ("catalog number", "catalog no", "part number", "part no", "model")
_PRICE_HEADER_TOKENS = ("list price", "price")


@dataclass(frozen=True)
class PricePair:
    """One (part number, USD list price) row from a price book."""

    part_number: str
    price_usd: Decimal


# ── XLSX path ───────────────────────────────────────────────────────


def _column_index(cell_ref: str) -> Optional[int]:
    """``"A5"`` → 0, ``"AB12"`` → 27. None when the ref has no letters."""
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    if not letters:
        return None
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _shared_strings(zf: zipfile.ZipFile) -> List[str]:
    try:
        root = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out: List[str] = []
    for si in root.findall(f"{_SHEET_NS}si"):
        out.append("".join(t.text or "" for t in si.iter(f"{_SHEET_NS}t")))
    return out


def _first_sheet_name(zf: zipfile.ZipFile) -> str:
    names = [
        n for n in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
    ]
    if not names:
        raise ValueError("XLSX contains no worksheets")
    return sorted(names)[0]


def parse_xlsx_rows(data: bytes) -> List[List[str]]:
    """Parse the first worksheet of an XLSX into rows of cell strings.

    Minimal by design: shared strings and inline strings are resolved,
    cell positions honour the ``r=`` reference so sparse rows stay
    column-aligned, everything else (formats, formulas' cached values)
    is taken as the raw ``<v>`` text.

    Raises ``ValueError`` on anything that isn't a readable XLSX.
    """
    try:
        zf = zipfile.ZipFile(BytesIO(data))
        shared = _shared_strings(zf)
        sheet = ElementTree.fromstring(zf.read(_first_sheet_name(zf)))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise ValueError(f"not a readable XLSX: {e}") from e

    rows: List[List[str]] = []
    for row_el in sheet.iter(f"{_SHEET_NS}row"):
        cells: List[str] = []
        next_col = 0
        for c in row_el.findall(f"{_SHEET_NS}c"):
            col = _column_index(c.get("r") or "")
            if col is None:
                col = next_col
            while len(cells) < col:
                cells.append("")
            ctype = c.get("t")
            if ctype == "inlineStr":
                is_el = c.find(f"{_SHEET_NS}is")
                value = (
                    "".join(t.text or "" for t in is_el.iter(f"{_SHEET_NS}t"))
                    if is_el is not None
                    else ""
                )
            else:
                v = c.find(f"{_SHEET_NS}v")
                value = v.text or "" if v is not None else ""
                if ctype == "s" and value:
                    try:
                        value = shared[int(value)]
                    except (ValueError, IndexError):
                        value = ""
            cells.append(value)
            next_col = col + 1
        rows.append(cells)
    return rows


def _find_header(
    rows: Sequence[Sequence[str]],
    pn_header: Optional[str],
    price_header: Optional[str],
) -> Tuple[int, int, int]:
    """Locate (header_row_idx, pn_col, price_col).

    Explicit header names (case-insensitive equality) win; otherwise
    the first row containing both a PN-ish and a price-ish token is
    the header. Raises ``ValueError`` when no header is found.
    """
    for i, row in enumerate(rows):
        lowered = [cell.strip().lower() for cell in row]
        pn_col = price_col = None
        for j, cell in enumerate(lowered):
            if not cell:
                continue
            if pn_header is not None:
                if cell == pn_header.strip().lower():
                    pn_col = j
            elif pn_col is None and any(tok in cell for tok in _PN_HEADER_TOKENS):
                pn_col = j
            if price_header is not None:
                if cell == price_header.strip().lower():
                    price_col = j
            elif price_col is None and any(tok in cell for tok in _PRICE_HEADER_TOKENS):
                price_col = j
        if pn_col is not None and price_col is not None and pn_col != price_col:
            return i, pn_col, price_col
    raise ValueError(
        "no header row found — pass --pn-column / --price-column to name "
        "the columns explicitly"
    )


def pairs_from_xlsx(
    data: bytes,
    pn_header: Optional[str] = None,
    price_header: Optional[str] = None,
) -> List[PricePair]:
    """Extract (part number, price) pairs from an XLSX price book."""
    rows = parse_xlsx_rows(data)
    header_idx, pn_col, price_col = _find_header(rows, pn_header, price_header)
    pairs: List[PricePair] = []
    for row in rows[header_idx + 1 :]:
        pn = row[pn_col].strip() if pn_col < len(row) else ""
        raw_price = row[price_col].strip() if price_col < len(row) else ""
        if not pn or not raw_price:
            continue
        price = _parse_bare_decimal(raw_price)
        if price is None or not (PRICE_MIN <= price <= PRICE_MAX):
            continue
        pairs.append(PricePair(part_number=pn, price_usd=price))
    logger.info(
        "xlsx: %d data rows → %d in-band price pairs",
        len(rows) - header_idx - 1,
        len(pairs),
    )
    return pairs


# ── PDF path ────────────────────────────────────────────────────────

# A price page mentions a price-list word AND is dense in $ amounts.
_PRICE_PAGE_WORDS = ("list price", "price list", "price schedule", "msrp")
_DOLLAR_RE = re.compile(r"\$\s?[0-9][0-9,]*(?:\.[0-9]{1,2})?")
_MIN_DOLLAR_HITS = 5


class _LLMPricePair(BaseModel):
    part_number: str = Field(description="Exact catalog / part number as printed")
    price_usd: float = Field(description="USD list price as printed, no symbols")


class _LLMPricePage(BaseModel):
    pairs: List[_LLMPricePair] = Field(
        description="Every (part number, USD list price) row on the page"
    )


def find_price_pages(pdf_bytes: bytes) -> List[int]:
    """0-indexed pages that look like price tables (free, text heuristic)."""
    try:
        import fitz
    except ImportError:  # pragma: no cover - dep is in project requirements
        logger.warning("PyMuPDF not installed — cannot scan PDF price book")
        return []

    pages: List[int] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i in range(len(doc)):
            text = doc[i].get_text()
            lowered = text.lower()
            if not any(w in lowered for w in _PRICE_PAGE_WORDS):
                continue
            if len(_DOLLAR_RE.findall(text)) < _MIN_DOLLAR_HITS:
                continue
            pages.append(i)
    return pages


def _page_texts(pdf_bytes: bytes, pages: Sequence[int]) -> List[str]:
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [doc[i].get_text() for i in pages]


def pairs_from_pdf(
    pdf_bytes: bytes,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    max_pages: int = 40,
) -> List[PricePair]:
    """Extract price pairs from a PDF price book via per-page Gemini calls.

    Pages are selected by ``find_price_pages`` first — never the whole
    book (CLAUDE.md rule: no raw multi-hundred-page PDFs to the LLM).
    Scanned (text-free) books are out of scope for v1.
    """
    from google import genai
    from google.genai import types as genai_types

    from specodex.config import MODEL

    pages = find_price_pages(pdf_bytes)
    if not pages:
        logger.warning("no price-table pages detected in PDF — nothing to extract")
        return []
    if len(pages) > max_pages:
        logger.warning(
            "price book has %d candidate pages; capping at %d (--max-llm-pages)",
            len(pages),
            max_pages,
        )
        pages = pages[:max_pages]

    client = genai.Client(api_key=api_key) if api_key else genai.Client()
    pairs: List[PricePair] = []
    for page_no, text in zip(pages, _page_texts(pdf_bytes, pages)):
        prompt = (
            "This is one page of a manufacturer price book. Extract EVERY "
            "(part number, USD list price) row printed on the page. Use the "
            "exact part/catalog number as printed. Skip discount symbols, "
            "weights, and non-price columns.\n\nPage text:\n" + text[:20000]
        )
        try:
            resp = client.models.generate_content(
                model=model or MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_LLMPricePage,
                ),
            )
            parsed = _LLMPricePage.model_validate_json(resp.text or "{}")
        except Exception as e:
            logger.warning("page %d: Gemini extraction failed: %s", page_no + 1, e)
            continue
        kept = 0
        for p in parsed.pairs:
            price = _parse_bare_decimal(str(p.price_usd))
            pn = (p.part_number or "").strip()
            if not pn or price is None or not (PRICE_MIN <= price <= PRICE_MAX):
                continue
            pairs.append(PricePair(part_number=pn, price_usd=price))
            kept += 1
        logger.info("page %d: %d price pairs", page_no + 1, kept)
    return pairs


# ── Join ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JoinMatch:
    product: ProductBase
    pair: PricePair


# Normalized keys shorter than this are too ambiguous to join: frame-size
# codes ("R1", "R2") extracted into part_number collide with short catalog
# numbers in the book (seen live on ABB ACQ580 vs the Baldor 501 Index).
MIN_JOIN_KEY_LEN = 4


def join_pairs(
    pairs: Sequence[PricePair], products: Sequence[ProductBase]
) -> List[JoinMatch]:
    """Match price pairs onto products missing ``msrp``.

    Equality on ``normalize_string(part_number)``. Products already
    carrying an ``msrp`` are never matched (enrich-only, no overwrite).
    When a part number appears more than once in the book with
    different prices, the first occurrence wins and the conflict is
    logged.
    """
    by_pn: dict[str, PricePair] = {}
    for pair in pairs:
        key = normalize_string(pair.part_number)
        if not key or len(key) < MIN_JOIN_KEY_LEN:
            continue
        existing = by_pn.get(key)
        if existing is not None:
            if existing.price_usd != pair.price_usd:
                logger.warning(
                    "duplicate part number %s in book ($%s vs $%s) — keeping first",
                    pair.part_number,
                    existing.price_usd,
                    pair.price_usd,
                )
            continue
        by_pn[key] = pair

    matches: List[JoinMatch] = []
    for product in products:
        if product.msrp is not None or not product.part_number:
            continue
        key = normalize_string(product.part_number)
        if not key or len(key) < MIN_JOIN_KEY_LEN:
            continue
        pair = by_pn.get(key)
        if pair is not None:
            matches.append(JoinMatch(product=product, pair=pair))
    return matches
