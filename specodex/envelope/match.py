"""Pure helpers for the envelope backfill: page selection and row matching.

No I/O here — everything is deterministic and property-tested
(``tests/unit/test_envelope_match_property.py``).

Matching contract. A drawing or dimension table labels each variant with a
*designator* — a frame size, model code, or full part number as printed
(``"AB042"``, ``"SGM7J-01"``, ``"MR-J5-10A"``). A row matches a designator
when, after normalisation (uppercase, separators dropped):

- **exact**: the designator equals the row's part number, product name, or
  frame size; or
- **prefix**: the designator equals a *token-boundary prefix* of the row's
  part number or product name, and carries at least one digit and three
  characters. Tokens split on separators and on letter↔digit transitions,
  so ``"AB042"`` is a prefix of ``"AB042-005-S2"`` but NOT of
  ``"AB0420-005"`` (a different frame), and ``"SGM7J01"`` is a prefix of
  ``"SGM7J-01AFC6S"``.

Each row takes its best-scoring designator (exact beats prefix, longer
prefix beats shorter). If two different envelopes tie for best, the row is
ambiguous and gets nothing — a wrong envelope is worse than none.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_SEPARATORS = re.compile(r"[\s\-_./,()\[\]:;+#*]+")
_TOKEN = re.compile(r"[A-Z]+|[0-9]+|[^A-Z0-9]+")

# Score tiers: (tier, length). Larger is better.
_EXACT = 2
_PREFIX = 1


def normalize_designator(value: Any) -> str:
    """Uppercase and strip separators. Non-strings normalise to ``""``."""
    if not isinstance(value, str):
        return ""
    return _SEPARATORS.sub("", value.upper())


def token_prefixes(value: Any) -> List[str]:
    """Normalised cumulative prefixes of ``value`` at token boundaries.

    ``"AB042-005-S2"`` → ``["AB", "AB042", "AB042005", "AB042005S", "AB042005S2"]``.
    """
    if not isinstance(value, str):
        return []
    tokens: List[str] = []
    for chunk in _SEPARATORS.split(value.upper()):
        tokens.extend(t for t in _TOKEN.findall(chunk) if t)
    out: List[str] = []
    acc = ""
    for tok in tokens:
        acc += tok
        out.append(acc)
    return out


def _score(designator: str, row: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    if not designator:
        return None
    for key in ("part_number", "product_name", "frame_size"):
        if designator == normalize_designator(row.get(key)):
            return (_EXACT, len(designator))
    if len(designator) < 3 or not any(c.isdigit() for c in designator):
        return None
    for key in ("part_number", "product_name"):
        if designator in token_prefixes(row.get(key)):
            return (_PREFIX, len(designator))
    return None


def match_rows(
    extractions: Sequence[Tuple[Optional[str], Any]],
    rows: Sequence[Dict[str, Any]],
) -> Dict[int, int]:
    """Map row index → extraction index.

    ``extractions`` is ``(designator, payload)`` pairs; payloads are compared
    with ``==`` to decide whether a tie is a real ambiguity (two different
    envelopes) or the same envelope printed twice.
    """
    normalized = [normalize_designator(d) for d, _ in extractions]
    out: Dict[int, int] = {}
    for ri, row in enumerate(rows):
        best: Optional[Tuple[int, int]] = None
        best_idx: List[int] = []
        for ei, d in enumerate(normalized):
            s = _score(d, row)
            if s is None:
                continue
            if best is None or s > best:
                best, best_idx = s, [ei]
            elif s == best:
                best_idx.append(ei)
        if not best_idx:
            continue
        payloads = [extractions[i][1] for i in best_idx]
        if all(p == payloads[0] for p in payloads[1:]):
            out[ri] = best_idx[0]
    return out


def select_envelope_pages(
    page_scores: Iterable[Dict[str, Any]],
    spec_pages: Iterable[int],
    max_pages: int,
    *,
    total_pages: int,
    neighbourhood: int = 2,
    min_drawing_score: float = 0.15,
) -> List[int]:
    """Choose which 0-indexed pages to send for envelope extraction.

    Priority: (1) the rows' own spec pages, up to half the cap (weight
    usually lives in the spec table), (2) drawing pages within
    ``neighbourhood`` of a spec page (a series' outline drawing sits beside
    its table), (3) remaining spec pages, (4) the rest of the drawing pages
    by score. Capped at ``max_pages``; a cap ≤ 0 selects
    nothing (a negative slice must never expand the set — the
    mounting-extract lesson). Out-of-range pages are dropped. Returned
    sorted and unique.
    """
    cap = max(int(max_pages), 0)
    if cap == 0 or total_pages <= 0:
        return []
    in_range = lambda p: (
        isinstance(p, int) and not isinstance(p, bool) and 0 <= p < total_pages
    )  # noqa: E731
    spec = sorted({p for p in spec_pages if in_range(p)})
    drawings = []
    for info in page_scores:
        p = info.get("page")
        score = info.get("score", 0)
        if (
            in_range(p)
            and isinstance(score, (int, float))
            and score == score
            and score >= min_drawing_score
        ):
            drawings.append((float(score), p))
    drawings.sort(key=lambda t: (-t[0], t[1]))
    near = [p for _, p in drawings if any(abs(p - s) <= neighbourhood for s in spec)]
    far = [p for _, p in drawings if p not in near]

    # Spec pages may take at most half the cap so a catalog with many
    # table pages still gets its outline drawings looked at.
    spec_budget = (cap + 1) // 2
    order = [*spec[:spec_budget], *near, *spec[spec_budget:], *far]
    chosen: List[int] = []
    for p in order:
        if p not in chosen:
            chosen.append(p)
        if len(chosen) >= cap:
            break
    return sorted(chosen)
