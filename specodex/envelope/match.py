"""Pure helpers for the envelope backfill: page selection and row matching.

No I/O here — everything is deterministic and property-tested
(``tests/unit/test_envelope_match_property.py``).

Matching contract. A drawing or dimension table labels each variant with a
*designator*: a frame size, model code or part number as printed, often
with qualifier words (``"AB042 2-stage"``, ``"SGMJV-01A A6 Without
brake"``). Strings are tokenised into runs of letters and runs of digits
(separators dropped). A designator *candidate* is the designator with 0..n
trailing whitespace-separated words dropped. A candidate D matches a row key
T (tiers, best first):

3. **exact** — D == T, against part number, frame size, or (only for rows
   without a part number) product name;
2. **prefix** — T starts with D;
1. **infix** — D occurs inside T starting at a later token (``"K D6R 02"``
   in ``"637/K D6R 02.S3-7-DEV"``); needs ≥ 3 tokens.

"Starts with / occurs" compares whole tokens except the last token of D,
which may be a prefix of the row's token only when it is a letter run: so
``"SGMJV-01A"`` matches ``"SGMJV-01AAA61"`` but ``"AB042"`` never matches
``"AB0420-005"`` (a digit run is a frame size; cutting it changes the
frame). Prefix and infix need a digit and ≥ 3 characters.

Product name is a fallback key only: on many rows it holds a series or
catalog title, so matching it would stamp one envelope across a catalog.

Each row takes its best candidate by (tier, tokens matched, length). A tie
between different envelopes is resolved only when it came from dropped
qualifier words (the row can't say "with brake" vs "without") and a
``size_key`` is supplied: the largest envelope wins, because over-stating
size is the safer error for space planning. Any other conflicting tie
leaves the row unmatched — a wrong envelope is worse than none.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

_SEPARATORS = re.compile(r"[\s\-_./,()\[\]:;+#*]+")
_TOKEN = re.compile(r"[A-Z]+|[0-9]+")

_EXACT, _PREFIX, _INFIX = 3, 2, 1


def normalize_designator(value: Any) -> str:
    """Uppercase, letters and digits only. Non-strings normalise to ``""``."""
    return "".join(tokenize(value))


def tokenize(value: Any) -> List[str]:
    """``"AB042-005 S2"`` → ``["AB", "042", "005", "S", "2"]``."""
    if not isinstance(value, str):
        return []
    return _TOKEN.findall(value.upper())


def token_prefixes(value: Any) -> List[str]:
    """Normalised cumulative prefixes of ``value`` at token boundaries."""
    out: List[str] = []
    acc = ""
    for tok in tokenize(value):
        acc += tok
        out.append(acc)
    return out


def _run_matches(d: List[str], t: List[str], at: int) -> bool:
    if at + len(d) > len(t):
        return False
    for k, tok in enumerate(d):
        row_tok = t[at + k]
        if k < len(d) - 1 or tok.isdigit():
            if tok != row_tok:
                return False
        elif not row_tok.startswith(tok):
            return False
    return True


def _candidates(designator: Any) -> List[Tuple[List[str], int]]:
    """(tokens, words_kept) for the designator with trailing words dropped."""
    if not isinstance(designator, str):
        return []
    words = designator.split()
    out = []
    for n in range(len(words), 0, -1):
        toks = tokenize(" ".join(words[:n]))
        if toks and (not out or toks != out[-1][0]):
            out.append((toks, n))
    return out


def _score_tokens(d: List[str], row: Dict[str, Any]) -> Optional[int]:
    keys = ["part_number", "frame_size"]
    if not row.get("part_number"):
        keys.append("product_name")
    joined = "".join(d)
    for key in keys:
        if joined and joined == normalize_designator(row.get(key)):
            return _EXACT
    if len(joined) < 3 or not any(c.isdigit() for c in joined):
        return None
    best: Optional[int] = None
    for key in keys:
        if key == "frame_size":
            continue
        t = tokenize(row.get(key))
        if _run_matches(d, t, 0):
            return _PREFIX
        if len(d) >= 3 and any(_run_matches(d, t, i) for i in range(1, len(t))):
            best = _INFIX
    return best


def match_rows(
    extractions: Sequence[Tuple[Optional[str], Any]],
    rows: Sequence[Dict[str, Any]],
    size_key: Optional[Callable[[Any], float]] = None,
) -> Dict[int, int]:
    """Map row index → extraction index.

    ``extractions`` is ``(designator, payload)`` pairs; payloads are compared
    with ``==`` to tell a real conflict from the same envelope printed
    twice. ``size_key(payload)`` enables the largest-envelope tie-break for
    qualifier-only ties (see module docstring).
    """
    cands = [_candidates(d) for d, _ in extractions]
    full_words = [c[0][1] if c else 0 for c in cands]
    out: Dict[int, int] = {}
    for ri, row in enumerate(rows):
        best: Optional[Tuple[int, int, int]] = None
        best_idx: List[int] = []
        best_kept: Dict[int, int] = {}
        for ei, options in enumerate(cands):
            mine: Optional[Tuple[int, int, int]] = None
            kept_at_mine = 0
            for toks, kept in options:
                tier = _score_tokens(toks, row)
                if tier is None:
                    continue
                s = (tier, len(toks), len("".join(toks)))
                if mine is None or s > mine:
                    mine, kept_at_mine = s, kept
            if mine is None:
                continue
            best_kept[ei] = kept_at_mine
            if best is None or mine > best:
                best, best_idx = mine, [ei]
            elif mine == best:
                best_idx.append(ei)
        if not best_idx:
            continue
        payloads = [extractions[i][1] for i in best_idx]
        if all(p == payloads[0] for p in payloads[1:]):
            out[ri] = best_idx[0]
            continue
        truncated = all(best_kept[i] < full_words[i] for i in best_idx)
        if truncated and size_key is not None:
            try:
                out[ri] = max(best_idx, key=lambda i: size_key(extractions[i][1]))
            except (TypeError, ValueError):
                pass
    return out


DIMS_AGREEMENT_TOL = 0.10


def _axis_value(axis: Any) -> Optional[float]:
    if not isinstance(axis, dict) or axis.get("unit") != "mm":
        return None
    v = axis.get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return float(v)


def agreed_dims(
    a: Any, b: Any, tol: float = DIMS_AGREEMENT_TOL
) -> Optional[Dict[str, Dict[str, Any]]]:
    """Combine two independent readings of one variant's envelope, or None.

    ``a`` and ``b`` are canonical dimension payloads (``{axis: {"value",
    "unit": "mm"}}``). They agree when both are non-empty, carry the same
    axis set, every value is a positive finite number in mm, and on every
    axis ``|x - y| <= tol * max(x, y)``. One disagreeing or missing axis
    withholds the whole envelope: a reading that got one axis wrong (the
    pilot Ø read as the length) is not trusted on the others. On agreement
    each axis keeps the larger reading — over-stating size is the safer
    error for space planning. Never raises; anything malformed is None.
    """
    if not isinstance(a, dict) or not isinstance(b, dict) or not a:
        return None
    if a.keys() != b.keys():
        return None
    out: Dict[str, Dict[str, Any]] = {}
    for axis in a:
        x, y = _axis_value(a[axis]), _axis_value(b[axis])
        if x is None or y is None:
            return None
        hi = max(x, y)
        if abs(x - y) > tol * hi:
            return None
        out[axis] = {"value": hi, "unit": "mm"}
    return out


def designator_on_page(designator: Any, page_text: Any) -> bool:
    """True when the designator's first word is printed on the page.

    The model must copy labels from the page; a label that isn't in the
    page's text layer was invented (seen live: catalog part numbers stamped
    onto a different series' table). Compared as normalised letters+digits
    so ``"AB 090"`` vs ``"AB090"`` and line breaks don't matter. A first
    word under 3 characters or without a digit is too generic to verify,
    and a page with no text layer can't verify anything — both are False.
    """
    if not isinstance(designator, str) or not isinstance(page_text, str):
        return False
    words = designator.split()
    if not words:
        return False
    head = normalize_designator(words[0])
    if len(head) < 3 or not any(c.isdigit() for c in head):
        # "R1 IP20" → first word too short; fall back to the first two.
        head = normalize_designator(" ".join(words[:2]))
        if len(head) < 3 or not any(c.isdigit() for c in head):
            return False
    return head in normalize_designator(page_text)


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

    def in_range(p: Any) -> bool:
        return isinstance(p, int) and not isinstance(p, bool) and 0 <= p < total_pages

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
