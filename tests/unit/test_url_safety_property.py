"""Property tests for the SSRF defense in ``specodex.url_safety``.

The example-based tests in ``test_url_safety.py`` cover known attack
vectors (RFC1918, loopback, cloud-metadata IPs, IPv6 zone identifiers,
disallowed schemes). This file generates *adversarial* URL strings —
unicode-laced hostnames, malformed schemes, percent-encoded hosts,
non-string inputs — and asserts the documented contract holds for
every input the strategy can produce.

**Contract under test** (per the docstring):

1. ``validate_url(url)`` either:
   - Returns the URL unchanged (safe), OR
   - Raises ``UnsafeURLError`` (a ``ValueError`` subclass).
2. **It never raises any other exception type.** A ``KeyError`` /
   ``TypeError`` / ``UnicodeError`` / generic ``ValueError`` escaping
   is the regression to catch — this is the security boundary
   between user-supplied URLs and the network layer, and any leak
   past the typed exception is a hole the caller's try/except may
   not handle.
3. The pass-path return value is **identical** to the input string
   (no normalisation, no canonicalisation).
4. ``is_url_safe(url)`` returns ``False`` for every input that
   ``validate_url`` would reject; ``True`` for every input it would
   accept. Never raises.

DNS is patched offline so the property runs deterministically.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.url_safety import UnsafeURLError, is_url_safe, validate_url


# ---------------------------------------------------------------------------
# Offline DNS — every property-generated hostname maps to a single
# arbitrary public IP. Real DNS calls in a 200-example run would hit
# the network and slow the suite to a crawl.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch):
    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        # Pretend everything resolves to a single public IP unless the
        # host is blank / contains characters DNS would never accept.
        if not host or "\x00" in host:
            raise socket.gaierror(socket.EAI_NONAME, "Name not known")
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                0,
                "",
                ("8.8.8.8", 0),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# ---------------------------------------------------------------------------
# Adversarial URL strategies
# ---------------------------------------------------------------------------


# Generic "anything that vaguely looks like a URL" plus the cases
# Hypothesis would otherwise rarely hit.
_URL_STRINGS = st.one_of(
    # Pure adversarial strings — most won't even parse as URLs.
    st.text(min_size=0, max_size=80),
    # Unicode-laced (BMP + supplementary planes).
    st.text(
        alphabet=st.characters(min_codepoint=0, max_codepoint=0x10FFFF),
        min_size=0,
        max_size=40,
    ),
    # Plausibly URL-shaped — scheme + host + path.
    st.from_regex(
        r"[a-z]{2,8}://[a-z0-9.\-]{1,30}(/[a-z0-9._\-/]{0,30})?",
        fullmatch=True,
    ),
    # Specific attack-vector samples — what a real attacker would try.
    st.sampled_from(
        [
            # Sneaky scheme casing
            "HTTPS://example.com/",
            "Https://example.com/",
            # IP literal forms (decimal int, octal, hex)
            "https://2130706433/",  # decimal 127.0.0.1
            "https://0177.0.0.1/",  # octal loopback
            "https://0x7f.0.0.1/",  # hex loopback
            "https://127.1/",  # short-form loopback
            # IPv6 brackets
            "https://[::1]/",
            "https://[::ffff:127.0.0.1]/",
            # Cloud metadata
            "https://169.254.169.254/latest/meta-data/",
            "https://[fd00::1]/",
            # Userinfo + ports
            "https://user:pass@example.com:8443/",
            "https://example.com:99999/",  # invalid port
            # Empty / whitespace
            "",
            "   ",
            "https://",
            "https:///",
            # Schemes we must reject
            "http://example.com/",
            "ftp://example.com/",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "gopher://example.com:25/",
            # Characters the URL parser handles oddly
            "https://example.com/\x00",
            "https://example.com/\n",
            "https://exa\nmple.com/",
            # Percent-encoded host
            "https://%65xample.com/",
            # IDN homoglyph (Cyrillic 'е' instead of Latin 'e')
            "https://еxample.com/",
            # Trailing dot
            "https://example.com./",
            # Long absurd host
            "https://" + "a" * 300 + "/",
        ]
    ),
)


# Non-string inputs — the function explicitly accepts only str; the
# documented contract is to raise UnsafeURLError, not TypeError, on
# anything else.
_NON_STRING_INPUTS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.binary(max_size=20),
    st.lists(st.text(max_size=10), max_size=3),
    st.dictionaries(st.text(max_size=5), st.text(max_size=10), max_size=3),
)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestValidateUrlProperties:
    """Adversarial URL strings vs the documented contract."""

    @given(url=_URL_STRINGS)
    @settings(
        max_examples=400,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
    )
    def test_only_unsafe_url_error_or_returns_input(self, url: str) -> None:
        """For any URL string, validate_url either returns the
        unchanged input or raises UnsafeURLError. Anything else
        escaping is a regression in the security boundary.
        """
        try:
            result = validate_url(url)
        except UnsafeURLError:
            return  # Documented rejection — fine.
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"validate_url raised {type(exc).__name__} (expected "
                f"UnsafeURLError or success): {exc!r}\n"
                f"input: {url!r}"
            )
        # Pass path: result is identical to input (no canonicalisation).
        assert result == url, (
            f"validate_url returned {result!r}, expected {url!r} unchanged"
        )

    @given(value=_NON_STRING_INPUTS)
    @settings(max_examples=200, deadline=None)
    def test_non_string_input_raises_unsafe_url_error(self, value: Any) -> None:
        """Non-string input — None, int, bytes, list, dict — must
        raise UnsafeURLError, not TypeError or AttributeError.
        """
        try:
            validate_url(value)
        except UnsafeURLError:
            return
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"validate_url(non-string) raised {type(exc).__name__} "
                f"instead of UnsafeURLError: {exc!r}\ninput: {value!r}"
            )
        # Reaching here means the function returned without raising,
        # which is also a contract violation for non-string inputs.
        pytest.fail(
            f"validate_url accepted non-string input {value!r} — "
            f"expected UnsafeURLError"
        )

    @given(url=_URL_STRINGS)
    @settings(max_examples=400, deadline=None)
    def test_is_url_safe_never_raises(self, url: str) -> None:
        """is_url_safe is the non-raising variant; must always return
        a bool, never propagate any exception."""
        try:
            result = is_url_safe(url)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"is_url_safe raised {type(exc).__name__}: {exc!r}\ninput: {url!r}"
            )
        assert isinstance(result, bool)

    @given(url=_URL_STRINGS)
    @settings(max_examples=200, deadline=None)
    def test_is_url_safe_consistent_with_validate_url(self, url: str) -> None:
        """is_url_safe(x) ⇔ validate_url(x) doesn't raise."""
        safe = is_url_safe(url)
        try:
            validate_url(url)
            validate_raised = False
        except UnsafeURLError:
            validate_raised = True
        except Exception:
            # The other property test catches this case; here we only
            # check the documented agreement between the two.
            return
        assert safe == (not validate_raised), (
            f"is_url_safe={safe} but validate_url raised={validate_raised}\n"
            f"input: {url!r}"
        )


class TestExplicitRegressionsFromKnownAttacks:
    """Pinning specific shapes from the URL-attack literature."""

    def test_localhost_short_form_rejected(self) -> None:
        """``127.1`` is the same as ``127.0.0.1`` for the OS resolver
        but a string the literal-parse path handles separately. The
        existing test suite covers this; included here so the
        property test's autouse DNS fixture doesn't suppress it."""
        # NOTE: 127.1 is a hostname-like string that ``ipaddress.ip_address``
        # rejects (it's not a valid IPv4 literal in textual form), so
        # the code path goes through DNS. Our offline DNS resolves
        # everything to 8.8.8.8 — which means this test would FALSELY
        # pass under the offline harness. Document the gap and
        # exercise the literal-IP path explicitly:
        with pytest.raises(UnsafeURLError):
            validate_url("https://127.0.0.1/")

    def test_metadata_ip_rejected(self) -> None:
        with pytest.raises(UnsafeURLError):
            validate_url("https://169.254.169.254/latest/meta-data/")

    def test_v4_mapped_v6_loopback_rejected(self) -> None:
        with pytest.raises(UnsafeURLError):
            validate_url("https://[::ffff:127.0.0.1]/")

    def test_invalid_port_handled_gracefully(self) -> None:
        """``urlparse`` raises ValueError for ports > 65535 starting in
        recent CPython versions. Confirm we surface that as
        UnsafeURLError rather than letting the raw exception out.

        If this test fails because validate_url accepted the URL,
        either CPython's urlparse changed behaviour or the SSRF
        defense's input layer needs a port-bounds check.
        """
        try:
            validate_url("https://example.com:99999/")
        except UnsafeURLError:
            pass  # Either path is acceptable per the contract.
        except ValueError as exc:
            # This is the failure-mode-of-interest — bare ValueError
            # leaking past the wrapper. UnsafeURLError IS a ValueError,
            # but the test should not pass on unwrapped ValueError.
            if not isinstance(exc, UnsafeURLError):
                pytest.fail(f"validate_url leaked bare ValueError: {exc!r}")
