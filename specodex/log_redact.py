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
    one first keeps the shorter replacement from splitting it.
    """
    vals = {
        v
        for name in SECRET_ENV_VARS
        if (v := os.environ.get(name)) and len(v) >= _MIN_SECRET_LEN
    }
    return sorted(vals, key=len, reverse=True)


def redact_secrets(text: str) -> str:
    """Return ``text`` with every current secret value replaced by ``[REDACTED]``.

    Pure string replacement — no regex, so a secret containing ``.`` or
    ``$`` can't be misread as a pattern. Idempotent. Never raises on a
    ``str`` input; a non-``str`` is coerced with ``str()`` first so an
    exception object can be passed directly.
    """
    if not isinstance(text, str):
        text = str(text)
    for secret in secret_values():
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


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
