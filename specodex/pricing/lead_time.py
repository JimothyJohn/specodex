"""Lead-time estimates from published vendor statements.

todo/COMMERCIAL.md Phase 2/4, lead-time half. The ground rule is
absolute: **we never invent a lead time.** There is no honest public
per-part lead-time signal, so the only rung available is a vendor's own
published statement — already captured (with citations) as
``lead_time_statement`` facts in the vendor registry
(``data/vendors/<slug>.json``, loader in ``specodex.vendors.registry``).

This module turns those statements into ``lead_time_estimate``
:class:`~specodex.models.commercial.SourcedFigure` values on product
rows:

- :func:`parse_lead_statement` extracts a numeric duration from the
  published text ("3 business days", "ships within 2 weeks",
  "5-7 days", "same day"). Unparseable statements yield ``None`` and
  the fact stays registry-only — we never guess.
- :func:`estimate_lead_time` applies a vendor's parseable statement to
  one of that vendor's products, carrying the fact's own citations
  through **verbatim** and tagging the figure ``confidence="medium"``:
  it is a vendor-level published statement applied to a part, not a
  part-specific listed term, so it must never claim ``"listed"``.

Determinism (COMMERCIAL.md ground rule 4): pure text parsing, no clock
read, no randomness — the same registry snapshot and product row always
produce the same figure.

**Same-day convention (pinned):** a published "same day" shipment
statement is recorded as ``{"value": 1, "unit": "days"}`` — the
coarsest honest bucket that contains the statement ("arrives within a
day"), chosen over 0 because a zero-day lead time would read as "no
lead time at all" rather than "very short".
"""

from __future__ import annotations

import math
import re
from typing import Dict, Optional, Tuple

from specodex.models.commercial import SourcedFigure
from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.product import ProductBase
from specodex.vendors.registry import VendorProfile, slug_for_manufacturer

# Vendor-level published statement applied to a part: never "listed".
LEAD_STATEMENT_CONFIDENCE = "medium"

# Pinned convention for "same day" statements (see module docstring).
SAME_DAY_LEAD: Tuple[float, str] = (1.0, "days")

_NUM = r"(\d+(?:\.\d+)?)"
# Optional qualifier between the number and the unit.
_QUAL = r"(?:(?:business|working|calendar)\s+)?"
_UNIT = r"(day|week)s?\b"

# "5-7 days", "5 – 7 weeks", "5 to 7 business days" — checked before the
# single-value pattern so the range's low bound isn't dropped.
_RANGE_RE = re.compile(
    rf"\b{_NUM}\s*(?:-|–|—|to)\s*{_NUM}\s*{_QUAL}{_UNIT}",
    re.IGNORECASE,
)
# "3 days", "3 business days", "2 weeks", "1.5 weeks"
_SINGLE_RE = re.compile(rf"\b{_NUM}\s*{_QUAL}{_UNIT}", re.IGNORECASE)
_SAME_DAY_RE = re.compile(r"\bsame[-\s]?day\b", re.IGNORECASE)

_UNIT_CANON = {"day": "days", "week": "weeks"}


def _to_duration(raw: str) -> Optional[float]:
    """Parse one regex-captured number; positive and finite or None."""
    try:
        value = float(raw)
    except (TypeError, ValueError):  # pragma: no cover - regex guarantees digits
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def parse_lead_statement_range(text: str) -> Optional[Tuple[float, float, str]]:
    """Extract ``(min, max, unit)`` from a published lead-time statement.

    Single-value statements return ``min == max``. Unit is canonicalised
    to ``"days"`` or ``"weeks"``. Returns ``None`` for anything that
    doesn't contain an explicit numeric duration (or "same day") —
    including malformed ranges (``min > max``), zero durations, and
    absurd magnitudes. A ``None`` here means the statement stays
    registry-only; we never guess a number that isn't printed.
    """
    if not isinstance(text, str) or not text:
        return None

    match = _RANGE_RE.search(text)
    if match:
        lo = _to_duration(match.group(1))
        hi = _to_duration(match.group(2))
        unit = _UNIT_CANON[match.group(3).lower()]
        if lo is None or hi is None or lo > hi:
            return None
        return lo, hi, unit

    match = _SINGLE_RE.search(text)
    if match:
        value = _to_duration(match.group(1))
        if value is None:
            return None
        return value, value, _UNIT_CANON[match.group(2).lower()]

    if _SAME_DAY_RE.search(text):
        value, unit = SAME_DAY_LEAD
        return value, value, unit

    return None


def parse_lead_statement(text: str) -> Optional[Tuple[float, str]]:
    """Extract ``(value, unit)`` from a published lead-time statement.

    Range statements ("5-7 days") report the **max** — the honest
    single number for planning; the caller records the full spread via
    :func:`parse_lead_statement_range` / ``observed_range``. Unit is
    one of ``"days"`` / ``"weeks"``. Unparseable → ``None``.
    """
    parsed = parse_lead_statement_range(text)
    if parsed is None:
        return None
    _, hi, unit = parsed
    return hi, unit


def estimate_lead_time(
    product: ProductBase,
    vendor_profiles: Dict[str, VendorProfile],
    *,
    retrieved_at: str,
) -> Optional[SourcedFigure]:
    """Lead-time figure for a product from its vendor's published statement.

    Looks up the product's manufacturer in the registry (via
    :func:`slug_for_manufacturer`); if the vendor carries at least one
    ``lead_time_statement`` fact whose value parses, returns a
    ``SourcedFigure`` with:

    - ``value``: the parsed duration (max of a range),
    - ``confidence="medium"`` — a vendor-level published statement
      applied to a part is never part-specific, so never ``"listed"``,
    - ``observed_range``: the min–max when the statement is a range,
    - ``sources``: the fact's own citations carried through verbatim —
      they already satisfy the citation invariants (URL + excerpt +
      retrieval time). ``retrieved_at`` is used only as a fallback for
      a citation that somehow lacks its own timestamp.

    Returns ``None`` (no estimate, ever a guess) when the product
    already carries a listed ``lead_time``, the manufacturer has no
    registry profile, the profile has no ``lead_time_statement`` fact,
    or no statement parses. When several statements parse, the first in
    registry order wins — deterministic for a given registry snapshot.
    """
    if getattr(product, "lead_time", None) is not None:
        return None
    profile = vendor_profiles.get(slug_for_manufacturer(product.manufacturer))
    if profile is None:
        return None

    for fact in profile.facts:
        if fact.kind != "lead_time_statement":
            continue
        parsed = parse_lead_statement_range(fact.value)
        if parsed is None:
            continue
        lo, hi, unit = parsed
        sources = [
            src
            if src.retrieved_at
            else src.model_copy(update={"retrieved_at": retrieved_at})
            for src in fact.sources
        ]
        observed = MinMaxUnit(min=lo, max=hi, unit=unit) if lo != hi else None
        return SourcedFigure(
            value=ValueUnit(value=hi, unit=unit),
            confidence=LEAD_STATEMENT_CONFIDENCE,
            observed_range=observed,
            sources=sources,
        )
    return None
