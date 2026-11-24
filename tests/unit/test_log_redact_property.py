"""Property companion for ``specodex.log_redact`` (HARDENING 4.3).

The sibling ``test_log_redact.py`` already carries a Hypothesis property,
but it seeds exactly **one** secret. Every contract violation this file
found lives in the interaction *between* secrets — the marker spliced in
for one value completing a fresh occurrence of another across the
substitution boundary. Per CLAUDE.md "Property testing — adversarial by
default", a security-relevant surface gets 300 examples.

Contracts pinned here, all of them taken verbatim from the module's own
docstrings:

- ``redact_secrets`` never raises on a ``str`` and always returns a
  ``str`` (and accepts a non-``str`` by coercion).
- **Containment:** no current secret value appears in the result. This
  is the whole point of the function — a survivor is a credential in a
  log line.
- **Idempotence:** the result is a fixed point of a second call.
- **Identity:** with nothing seeded, the text comes back byte-identical.
- ``secret_values`` is length-descending, deduplicated, floors at
  ``_MIN_SECRET_LEN``, and is a pure function of the values (no
  hash-order dependence).

Two violations found, both pinned as explicit cases in the sibling file
under ``TestMultiSecretRedaction``:

1. **Splice-boundary leak.** With ``AWS_SESSION_TOKEN="AA[REDACTED]"``
   and ``GEMINI_API_KEY="BBBBBBBB"``, ``redact_secrets("AABBBBBBBB")``
   returned ``"AA[REDACTED]"`` — the session token verbatim, and not a
   fixed point of a second pass.
2. **Self-regrowing secret.** A value that is a substring of the marker
   (``"[REDACTE"``) was replaced by a string that contains it again, so
   each pass lengthened the line instead of scrubbing it
   (``"[REDACTED]D]D]D]D]"``). Now substituted with the empty string.

Neither is reachable with a real credential — a splice needs a secret
carrying the marker's own text. They are contract bugs of the kind
CLAUDE.md's property-testing section describes: the docstring promised
containment and idempotence, and the code delivered neither once more
than one value was configured.
"""

from __future__ import annotations

import os

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from specodex.log_redact import (
    REDACTED,
    SECRET_ENV_VARS,
    redact_secrets,
    secret_values,
)

# os.environ cannot hold a NUL byte or a lone surrogate, so neither can a
# real secret value. Everything else is fair game: unicode, regex
# metacharacters, whitespace inside the value. Values are stripped (a
# leading/trailing-space env var is not a thing worth modelling) and
# re-padded to the redactor's 8-char floor so the strip does not push an
# example below the threshold and silently turn it into a no-op.
_secret_text = st.text(
    st.characters(blacklist_characters="\x00", blacklist_categories=("Cs",)),
    min_size=8,
    max_size=40,
).map(lambda s: s.strip().ljust(8, "x"))

# Values built out of the REDACTED marker's own characters are the shapes
# that straddle a substitution boundary. Hypothesis will not stumble onto
# "AA[REDACTED]" from free text in any reasonable number of examples, so
# give the co-occurrence its own strategy rather than trusting the
# general one to find it (the same lesson as the drawing-page-finder
# negative-cap miss in CLAUDE.md).
_marker_fragment = st.integers(min_value=1, max_value=len(REDACTED)).flatmap(
    lambda n: st.sampled_from([REDACTED[:n], REDACTED[-n:]])
)
_boundary_secret = st.tuples(
    st.text(st.characters(blacklist_categories=("Cs", "Cc")), max_size=6),
    _marker_fragment,
    st.text(st.characters(blacklist_categories=("Cs", "Cc")), max_size=6),
).map(lambda parts: "".join(parts).ljust(8, "z"))

_any_secret = st.one_of(_secret_text, _boundary_secret)
_text = st.text(max_size=200)

_SLOTS = ("GEMINI_API_KEY", "AWS_SESSION_TOKEN", "SERPER_API_KEY")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every example from a known-empty credential environment.

    conftest seeds ``GEMINI_API_KEY`` with a stub for the pipeline tests;
    leaving it in place would make "identity when nothing is seeded"
    untestable and would let a stub value bleed into containment
    assertions.
    """
    for name in SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _seed(secrets: list[str]) -> list[str]:
    """Put ``secrets`` into the credential env vars; return what stuck.

    Returns the values the redactor will actually act on — duplicates
    collapse (one env var per slot) and the module floors at 8 chars, so
    the caller cannot assume its input list is what gets scrubbed.
    """
    for name, value in zip(_SLOTS, secrets):
        os.environ[name] = value
    return secret_values()


@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    secrets=st.lists(_any_secret, min_size=1, max_size=3),
    before=_text,
    between=_text,
    after=_text,
)
def test_no_seeded_secret_survives(secrets, before, between, after):
    """Containment + totality: with up to three secrets seeded and a text
    that embeds them in sequence, the result is a ``str`` carrying none
    of them."""
    live = _seed(secrets)
    text = before + "".join(s + between for s in secrets) + after

    out = redact_secrets(text)

    assert isinstance(out, str)
    for secret in live:
        assert secret not in out, f"{secret!r} survived into {out!r}"


@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    secrets=st.lists(_any_secret, min_size=1, max_size=3),
    before=_text,
    between=_text,
    after=_text,
)
def test_idempotent(secrets, before, between, after):
    """A second pass changes nothing — the documented fixed-point
    contract, and the half that a one-pass redactor failed."""
    _seed(secrets)
    text = before + "".join(s + between for s in secrets) + after

    once = redact_secrets(text)
    assert redact_secrets(once) == once


@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(text=_text)
def test_identity_with_nothing_seeded(text):
    """No credentials in the environment means the redactor is a no-op —
    it must not mangle ordinary log text."""
    assert secret_values() == []
    assert redact_secrets(text) == text


@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(value=st.one_of(st.none(), st.integers(), st.floats(), st.binary(), _text))
def test_never_raises_on_arbitrary_input(value):
    """The call sites pass exception objects straight in, so the coercion
    path has to be total over anything with a well-behaved ``__str__``."""
    os.environ["GEMINI_API_KEY"] = "SENTINEL-GEMINI-0123456789"
    out = redact_secrets(value)
    assert isinstance(out, str)
    assert "SENTINEL-GEMINI" not in out


@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(secrets=st.lists(_any_secret, min_size=0, max_size=3))
def test_secret_values_contract(secrets):
    """Length-descending, deduplicated, floored at 8 chars, and a pure
    function of the values — no set-iteration order leaking through."""
    live = _seed(secrets)

    assert live == sorted(live, key=lambda v: (-len(v), v))
    assert len(live) == len(set(live))
    assert all(len(v) >= 8 for v in live)
    # Same environment, same answer: a hash-seeded tie order would show
    # up here as a mismatch on a rerun within the same process only if
    # the set were rebuilt differently, so also assert the stronger
    # property that the order is derivable from the values alone.
    assert secret_values() == live
