"""Single source of truth for "this value is a placeholder, not real data."

LLMs frequently emit literal placeholder strings like "N/A", "TBD", "-", or
the word "None" when they can't find a value. Treating these as populated
fields pollutes the product database and inflates quality scores.

Use `is_placeholder` everywhere we ask "is this field meaningfully populated?"
— scoring, frontend rendering, and validator coercion all route through here.
"""

from __future__ import annotations


# Case-normalized. `is_placeholder` lowercases before comparing so we don't
# have to enumerate every casing. Keep the set small and obvious; do not add
# domain-specific tokens ("NotApplicable" etc.) unless we see them in real
# LLM output.
PLACEHOLDER_STRINGS = frozenset(
    {
        "",
        "n/a",
        "na",
        "tbd",
        "tba",
        "-",
        "--",
        "none",
        "null",
        "?",
        "unknown",
        "not available",
        "not applicable",
        "not specified",
        # Seen in real Gemini output on the Nidec ABLE VR gearbox run
        # (2026-07-24): a prompt asking it to "leave fields null" came
        # back with the literal string "unset" in lubrication_type.
        "unset",
    }
)


def is_placeholder(value: object) -> bool:
    """True if `value` is None or a known placeholder string.

    Strings are stripped and lowercased before comparison. Non-None,
    non-string values (ints, floats, dicts, lists) always return False —
    we only police text fields. An empty list or dict is NOT considered
    a placeholder here; callers that want to treat those as missing
    should do so explicitly.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in PLACEHOLDER_STRINGS
    return False
