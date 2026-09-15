"""Contract for ``specodex.log_redact.redact_secrets`` (HARDENING 4.3).

Example-based cases pin the shapes that matter (prefix secrets, short
placeholders, non-str input); the Hypothesis property pins the general
contract: never raises, never leaks a seeded secret, idempotent, and an
identity when nothing is seeded.
"""

from __future__ import annotations

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from specodex.log_redact import (
    REDACTED,
    SECRET_ENV_VARS,
    redact_secrets,
    secret_values,
)


@pytest.fixture
def no_secrets(monkeypatch):
    for name in SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class TestRedactSecrets:
    def test_replaces_each_seeded_secret(self, monkeypatch, no_secrets):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy-SENTINEL-GEMINI-0123456789")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SENTINEL-AWS-SECRET-abcdef")
        out = redact_secrets(
            "401 for https://x/?key=AIzaSy-SENTINEL-GEMINI-0123456789 "
            "(aws=SENTINEL-AWS-SECRET-abcdef)"
        )
        assert "AIzaSy-SENTINEL" not in out
        assert "SENTINEL-AWS" not in out
        assert out.count(REDACTED) == 2

    def test_identity_when_nothing_is_seeded(self, no_secrets):
        s = "Error classifying pages 1, 2: 503 UNAVAILABLE"
        assert redact_secrets(s) == s
        assert secret_values() == []

    def test_short_placeholder_values_are_ignored(self, monkeypatch, no_secrets):
        # conftest seeds GEMINI_API_KEY with a stub; a 4-char value must
        # not turn every "test" in a log line into [REDACTED].
        monkeypatch.setenv("GEMINI_API_KEY", "test")
        assert redact_secrets("test run: test") == "test run: test"

    def test_longer_secret_replaced_before_its_prefix(self, monkeypatch, no_secrets):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIASENTINELKEY")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "AKIASENTINELKEY/session/longer")
        out = redact_secrets("token=AKIASENTINELKEY/session/longer id=AKIASENTINELKEY")
        assert out == f"token={REDACTED} id={REDACTED}"

    def test_accepts_an_exception_object(self, monkeypatch, no_secrets):
        monkeypatch.setenv("SERPER_API_KEY", "SERPER-SENTINEL-1234567890")
        err = RuntimeError("bad key SERPER-SENTINEL-1234567890")
        assert redact_secrets(err) == f"bad key {REDACTED}"  # type: ignore[arg-type]

    def test_idempotent(self, monkeypatch, no_secrets):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy-SENTINEL-GEMINI-0123456789")
        once = redact_secrets("k=AIzaSy-SENTINEL-GEMINI-0123456789")
        assert redact_secrets(once) == once


class TestMultiSecretRedaction:
    """Regression cases for the two substitution-boundary bugs.

    Both found by ``tests/unit/test_log_redact_property.py``, whose
    Hypothesis strategies seed up to three secrets at once — the single
    -secret property below cannot express either shape.

    1. **Splice-boundary leak.** Replacing one secret splices
       ``[REDACTED]`` into the text, and the splice can complete a fresh
       occurrence of a *different* secret across the substitution
       boundary. The synthesised secret is the longer of the two, so the
       longest-first loop is already past it when the splice creates it,
       and it survived into the log line.
    2. **Self-regrowing secret.** A value that is a substring of the
       marker was replaced by a string containing it again, so each pass
       lengthened the line rather than scrubbing it.

    Pinned explicitly so the shapes can't regress if the Hypothesis
    strategies drift.
    """

    def test_splice_does_not_leak_a_second_secret(self, monkeypatch, no_secrets):
        monkeypatch.setenv("AWS_SESSION_TOKEN", "AA[REDACTED]")
        monkeypatch.setenv("GEMINI_API_KEY", "BBBBBBBB")
        out = redact_secrets("AABBBBBBBB")
        assert "AA[REDACTED]" not in out
        assert out == REDACTED

    def test_splice_result_is_still_a_fixed_point(self, monkeypatch, no_secrets):
        monkeypatch.setenv("AWS_SESSION_TOKEN", "AA[REDACTED]")
        monkeypatch.setenv("GEMINI_API_KEY", "BBBBBBBB")
        once = redact_secrets("AABBBBBBBB")
        assert redact_secrets(once) == once

    def test_equal_length_secrets_sort_deterministically(self, monkeypatch, no_secrets):
        # Ties used to fall out of a set in PYTHONHASHSEED order, which
        # made the redaction of a two-secret line non-reproducible.
        monkeypatch.setenv("GEMINI_API_KEY", "ZZZZZZZZ")
        monkeypatch.setenv("SERPER_API_KEY", "AAAAAAAA")
        assert secret_values() == ["AAAAAAAA", "ZZZZZZZZ"]

    def test_marker_substring_secret_is_removed_not_regrown(
        self, monkeypatch, no_secrets
    ):
        # "[REDACTE" is a prefix of the marker, so substituting the
        # marker for it re-creates it and the line grows on every pass
        # ("[REDACTED]D]D]D]D]" before the fix). Such a value is deleted
        # outright instead.
        monkeypatch.setenv("GEMINI_API_KEY", "[REDACTE")
        out = redact_secrets("prefix [REDACTE suffix")
        assert "[REDACTE" not in out
        assert out == "prefix  suffix"

    def test_marker_substring_secret_is_idempotent(self, monkeypatch, no_secrets):
        monkeypatch.setenv("GEMINI_API_KEY", "REDACTED]")
        once = redact_secrets("tail REDACTED]")
        assert redact_secrets(once) == once

    def test_both_plain_secrets_still_redacted(self, monkeypatch, no_secrets):
        monkeypatch.setenv("GEMINI_API_KEY", "GEMINI-SENTINEL-000000")
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_SENTINEL_111111")
        out = redact_secrets(
            "call failed: key=GEMINI-SENTINEL-000000 stripe=sk_live_SENTINEL_111111"
        )
        assert out == f"call failed: key={REDACTED} stripe={REDACTED}"


# os.environ cannot hold a NUL byte or a lone surrogate, so exclude those
# from the seeded secret; everything else (unicode, regex metacharacters,
# whitespace inside) is fair. Trailing/leading whitespace is stripped
# rather than filtered so Hypothesis doesn't discard most examples, and
# re-padded to the redactor's 8-char floor when the strip shortens it.
_secret = st.text(
    st.characters(blacklist_characters="\x00", blacklist_categories=("Cs",)),
    min_size=8,
    max_size=40,
).map(lambda s: s.strip().ljust(8, "x"))
_text = st.text(max_size=200)


@settings(max_examples=200)
@given(secret=_secret, before=_text, after=_text, repeats=st.integers(0, 3))
def test_property_seeded_secret_never_survives(secret, before, after, repeats):
    """With one secret seeded, any text embedding it (0..3 times) comes
    back without it, the function never raises, and the result is a
    fixed point of a second pass."""
    previous = os.environ.get("GEMINI_API_KEY")
    os.environ["GEMINI_API_KEY"] = secret
    try:
        text = before + (secret + after) * repeats
        out = redact_secrets(text)
        assert isinstance(out, str)
        assert secret not in out
        assert redact_secrets(out) == out
        if repeats == 0 and secret not in text:
            assert out == text
    finally:
        if previous is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = previous
