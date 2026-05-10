"""Build the primed second-pass prompt from a Probe + first-pass output.

The second pass keeps the same structured-JSON response_schema as the
first; only the system-prompt prefix changes. The shape of the priming
block, in order:

    1. "You already extracted this on the first pass:" — the JSON dump
       of the first-pass product variants. Lets the LLM correct in
       place rather than restart from scratch.
    2. "These fields need a second look:" — one bullet per FieldProbe,
       with the per-field primer and caption hunting list.
    3. "Don't regress good fields" — the merge logic only takes
       second-pass values for the probed fields anyway, but a
       belt-and-suspenders nudge keeps the second-pass output usable
       even if the merge logic later changes.

The prompt is tested in `tests/unit/test_double_tap.py` for content
and shape — fast feedback when synonym tables or caption lookups
drift out of sync.
"""

from __future__ import annotations

import json
from typing import Any, List

from specodex.double_tap.verifier import Probe


_HEADER = (
    "You already extracted this product variant on a FIRST pass — your "
    "result is shown below. Re-extract using the same response schema, "
    "but pay particular attention to the fields listed under "
    "'NEEDS A SECOND LOOK'. For unprobed fields, copy the first-pass "
    "value forward unless you find a clearly contradictory entry in the "
    "catalog. Do NOT regress fields that were correctly extracted the "
    "first time."
)


def build_priming_block(
    probe: Probe,
    first_pass_payload: dict[str, Any],
) -> str:
    """Build the priming text injected before the standard extraction prompt."""
    if probe.empty():
        return ""

    bullets: List[str] = []
    for fp in probe.fields:
        captions = fp.caption_text
        bullets.append(
            f"- `{fp.field}` ({fp.reason}): {fp.primer}"
            + (f" {captions}." if captions else "")
        )

    first_pass_json = json.dumps(first_pass_payload, indent=2, default=str)

    return (
        f"{_HEADER}\n\n"
        "FIRST-PASS RESULT:\n"
        "```json\n"
        f"{first_pass_json}\n"
        "```\n\n"
        "NEEDS A SECOND LOOK:\n" + "\n".join(bullets) + "\n\n"
    )
