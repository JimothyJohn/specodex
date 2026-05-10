"""Double-tap extraction — verifier-loop on top of the LLM pipeline.

The first LLM pass extracts via the existing path
(``specodex.extract.call_llm_and_parse``). The verifier
(``specodex.double_tap.verifier``) inspects the structured output and
identifies fields that are missing, ambiguous, or unit-dropped. If
anything fires, the runner (``specodex.double_tap.runner``) re-prompts
Gemini with the first-pass output + a list of fields to revisit + the
catalog captions to look for.

See ``todo/DOUBLE_TAP.md`` for the design rationale and Phase plan.
"""

from specodex.double_tap.runner import (
    DoubleTapResult,
    extract_with_recovery,
)
from specodex.double_tap.verifier import (
    FieldProbe,
    Probe,
    verify,
)


__all__ = [
    "DoubleTapResult",
    "FieldProbe",
    "Probe",
    "extract_with_recovery",
    "verify",
]
