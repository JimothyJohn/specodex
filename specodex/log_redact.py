"""Scrub known secrets out of text that is about to be logged.

The cloud-security rule is "logs are an attack surface AND a forensic
tool": credentials never appear at any log level. Most of our log lines
are parameterised and never touch a secret, but a handful of ``except``
sites log the exception text of an SDK call — and an SDK error can echo
request details (a URL with a ``?key=`` query, an auth header, a
redirected request). ``redact_secrets`` is the guard for those sites.

The secret *values* come from the environment at call time, so the
helper needs no configuration: whatever ``GEMINI_API_KEY`` etc. hold in
the current process is what gets replaced. Values shorter than
``_MIN_SECRET_LEN`` are ignored so a placeholder like ``"test"`` can't
turn the redactor into a text shredder.

HARDENING 4.3 (Python half, 2026-09-13). Tests:
``tests/unit/test_log_redact.py`` (contract) and
``tests/integration/test_log_leaks.py`` (end-to-end through the real
log sites).
"""

from __future__ import annotations

import logging
import os

__all__ = [
    "SECRET_ENV_VARS",
    "REDACTED",
    "SDK_LOGGERS",
    "quiet_sdk_debug_logging",
    "redact_secrets",
    "secret_values",
]

# Every environment variable whose value must never reach a log line.
# Extend when a new credential joins the stack; the integration test
# seeds each of these with a sentinel and asserts it stays out of caplog.
SECRET_ENV_VARS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "SERPER_API_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
)

REDACTED = "[REDACTED]"

# Below this length a value is too short to be a real credential and too
# likely to be a common substring ("test", "1234"); replacing it would
# mangle unrelated log text.
_MIN_SECRET_LEN = 8


def secret_values() -> list[str]:
    """Current secret values worth scrubbing, longest first.

    Longest-first matters when one secret is a prefix of another (an
    access key id inside a longer session token): replacing the longer
    one first keeps the shorter replacement from splitting it. Equal
    lengths tie-break lexicographically so the order is a function of
    the values alone — a bare ``key=len`` over a set leaves ties in
    ``PYTHONHASHSEED`` order, which makes the output of a multi-secret
    redaction differ run to run.
    """
    vals = {
        v
        for name in SECRET_ENV_VARS
        if (v := os.environ.get(name)) and len(v) >= _MIN_SECRET_LEN
    }
    return sorted(vals, key=lambda v: (-len(v), v))


# One replacement pass is not enough to establish "no secret survives".
# Substituting one secret splices REDACTED into the text, and that splice
# can complete a *fresh* occurrence of another secret across the
# substitution boundary — a secret whose own value carries part of the
# marker, e.g. AWS_SESSION_TOKEN="AA[REDACTED]" against the text
# "AA" + GEMINI_API_KEY. The synthesised secret is the longer of the two,
# so the longest-first loop is already past it when the splice creates
# it, and it rode out into the log line. That also broke the documented
# idempotence: a second call *did* catch it, so
# ``redact_secrets(redact_secrets(s)) != redact_secrets(s)``.
#
# Re-running the pass until the text stops moving restores both. The cap
# bounds a loop whose inputs are attacker-adjacent (the values come from
# the environment, the text from an SDK error); in practice the second
# pass is already a fixed point, and the property test pins that no
# reachable input needs more. Exhausting it is handled by failing closed
# in `redact_secrets` rather than by returning a half-scrubbed line.
_MAX_PASSES = 5


def _replacement_for(value: str) -> str:
    """The marker to substitute for ``value``.

    Normally ``REDACTED``. A value that is itself a substring of the
    marker gets the empty string instead, because substituting the
    marker for it *re-creates* it: ``"[REDACTE"`` → ``"[REDACTED]"``,
    which contains ``"[REDACTE"`` again, so the substitution grows the
    line on every pass and never scrubs it. ``REDACTED`` is 10
    characters and nothing under ``_MIN_SECRET_LEN`` counts as a secret,
    so this is exactly the six substrings of ``"[REDACTED]"`` that are 8
    characters or longer. A real credential is key material, never our
    own marker text, so the branch is unreachable in practice — it
    exists so the loop below has a terminating substitution for every
    input rather than leaning on the pass cap.
    """
    return "" if value in REDACTED else REDACTED


def redact_secrets(text: str) -> str:
    """Return ``text`` with every current secret value replaced by ``[REDACTED]``.

    Pure string replacement — no regex, so a secret containing ``.`` or
    ``$`` can't be misread as a pattern. Idempotent, and no current
    secret value survives into the result. Never raises on a ``str``
    input; a non-``str`` is coerced with ``str()`` first so an exception
    object can be passed directly.

    **Fails closed.** If the substitution loop cannot reach a state with
    no secret left in the text (see ``_MAX_PASSES``), the whole line is
    dropped and a bare ``[REDACTED]`` is returned. Losing a log message
    is the cheap failure; emitting a credential is not.
    """
    if not isinstance(text, str):
        text = str(text)
    secrets = secret_values()
    if not secrets:
        return text
    for _ in range(_MAX_PASSES):
        before = text
        for secret in secrets:
            if secret in text:
                text = text.replace(secret, _replacement_for(secret))
        if text == before:
            return text
    # Cap exhausted: the text is still moving, so we cannot claim it is
    # clean. Drop it.
    return REDACTED if any(s in text for s in secrets) else text


# SDK loggers whose DEBUG output includes signed requests. botocore.auth
# logs the canonical request — `x-amz-security-token:<session token>` in
# clear — at DEBUG; the pipeline modules configure the ROOT level from
# `LOG_LEVEL`, so `LOG_LEVEL=DEBUG` would have dumped the token into
# every DynamoDB call's log. Clamp these to INFO regardless.
SDK_LOGGERS: tuple[str, ...] = ("botocore", "boto3", "urllib3", "httpx", "httpcore")


def quiet_sdk_debug_logging() -> None:
    """Clamp SDK loggers to INFO so operator DEBUG runs don't log credentials.

    Idempotent; safe to call at import time from any module that pulls
    in one of these SDKs. Our own loggers are unaffected.
    """
    for name in SDK_LOGGERS:
        lg = logging.getLogger(name)
        if lg.level == logging.NOTSET or lg.level < logging.INFO:
            lg.setLevel(logging.INFO)
