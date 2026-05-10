"""Tests for ``specodex.url_safety`` — the SSRF allowlist.

The defense rejects:
- Non-HTTPS schemes (http, file, ftp, gopher, javascript, data, etc.)
- Direct IP literals in blocked v4/v6 ranges (loopback, RFC1918,
  link-local + cloud metadata, multicast, etc.)
- Hostnames whose DNS A/AAAA records resolve to blocked IPs

Tests that require DNS use a fake ``socket.getaddrinfo`` to keep
behavior deterministic and offline-friendly.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

from specodex.url_safety import (
    UnsafeURLError,
    is_url_safe,
    validate_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_getaddrinfo(mapping: dict[str, list[str]]):
    """Return a ``socket.getaddrinfo`` substitute backed by a host→IPs dict.

    Any host not in the mapping raises ``socket.gaierror`` (NXDOMAIN-equivalent).
    """

    def fake(host, port, family=0, type=0, proto=0, flags=0):
        if host not in mapping:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        infos = []
        for addr in mapping[host]:
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            af = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
            sockaddr = (str(ip), 0, 0, 0) if ip.version == 6 else (str(ip), 0)
            infos.append((af, socket.SOCK_STREAM, 0, "", sockaddr))
        return infos

    return fake


@pytest.fixture
def patched_dns(monkeypatch: pytest.MonkeyPatch):
    """Yields a function that installs a fake DNS map for the duration of a test."""

    def install(mapping: dict[str, list[str]]):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(mapping))

    return install


# ---------------------------------------------------------------------------
# Scheme rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "gopher://example.com:70/",
        "javascript:alert(1)",
        "data:text/plain,hello",
        "ws://example.com",
        "wss://example.com",
        "ldap://example.com",
        "telnet://example.com",
    ],
)
def test_rejects_non_https_schemes(url: str, patched_dns):
    patched_dns({"example.com": ["93.184.216.34"]})  # public IP — schema is the gate
    with pytest.raises(UnsafeURLError, match="not allowed"):
        validate_url(url)


def test_accepts_https_to_public_host(patched_dns):
    patched_dns({"example.com": ["93.184.216.34"]})
    assert (
        validate_url("https://example.com/foo?bar=1") == "https://example.com/foo?bar=1"
    )


# ---------------------------------------------------------------------------
# IPv4 literal blocking — direct attacks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip,reason",
    [
        ("127.0.0.1", "loopback"),
        ("127.255.255.254", "loopback range"),
        ("10.0.0.1", "RFC1918 private"),
        ("172.16.0.1", "RFC1918 private"),
        ("172.31.255.254", "RFC1918 private upper bound"),
        ("192.168.1.1", "RFC1918 private"),
        ("169.254.169.254", "AWS/GCP/Azure metadata IP"),
        ("169.254.0.1", "link-local"),
        ("100.64.0.1", "CGNAT (RFC6598)"),
        ("0.0.0.0", "this network"),
        ("255.255.255.255", "broadcast"),
        ("224.0.0.1", "multicast"),
        ("240.0.0.1", "reserved"),
        ("198.18.1.1", "benchmark"),
    ],
)
def test_rejects_blocked_ipv4_literals(ip: str, reason: str):
    with pytest.raises(UnsafeURLError, match=r"blocked (IP literal|range)"):
        validate_url(f"https://{ip}/")


def test_accepts_public_ipv4_literal():
    # 8.8.8.8 (Google DNS) — public, should pass
    assert validate_url("https://8.8.8.8/") == "https://8.8.8.8/"


def test_ipv4_literal_path_does_not_call_dns(monkeypatch: pytest.MonkeyPatch):
    """IP literals must short-circuit DNS resolution. Tests the regression
    of an earlier bug where ``UnsafeURLError`` (a ``ValueError`` subclass)
    raised inside the literal-check ``try`` was shadowed by the
    ``except ValueError`` branch and the function fell through to
    ``socket.getaddrinfo``."""
    called = {"count": 0}

    def boom(*_args, **_kwargs):
        called["count"] += 1
        raise AssertionError("getaddrinfo should not be called for IP literals")

    monkeypatch.setattr(socket, "getaddrinfo", boom)

    # Public IP literal — should pass without DNS.
    assert validate_url("https://8.8.8.8/") == "https://8.8.8.8/"

    # Blocked IP literal — should raise UnsafeURLError, also without DNS.
    with pytest.raises(UnsafeURLError, match="blocked IP literal"):
        validate_url("https://127.0.0.1/")

    assert called["count"] == 0


# ---------------------------------------------------------------------------
# IPv6 literal blocking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ipv6,reason",
    [
        ("::1", "IPv6 loopback"),
        ("fe80::1", "IPv6 link-local"),
        ("fc00::1", "IPv6 ULA"),
        ("fd00::1", "IPv6 ULA upper-half"),
        ("ff02::1", "IPv6 multicast"),
        ("::", "IPv6 unspecified"),
        ("64:ff9b::1", "IPv6 NAT64"),
    ],
)
def test_rejects_blocked_ipv6_literals(ipv6: str, reason: str):
    with pytest.raises(UnsafeURLError, match=r"blocked (IP literal|range)"):
        validate_url(f"https://[{ipv6}]/")


def test_accepts_public_ipv6_literal():
    # 2001:4860:4860::8888 (Google public DNS over v6) — public
    assert (
        validate_url("https://[2001:4860:4860::8888]/")
        == "https://[2001:4860:4860::8888]/"
    )


def test_rejects_ipv6_with_zone_identifier():
    """``fe80::1%eth0`` would normally be parseable but a zone-id suggests
    link-local evasion; reject explicitly."""
    with pytest.raises(UnsafeURLError, match="zone identifier"):
        validate_url("https://[fe80::1%eth0]/")


def test_rejects_ipv4_mapped_ipv6_loopback():
    """``::ffff:127.0.0.1`` — IPv4-mapped IPv6 loopback should fall in the
    blocked v6 ::ffff:0:0/96 range."""
    with pytest.raises(UnsafeURLError, match=r"blocked (IP literal|range)"):
        validate_url("https://[::ffff:127.0.0.1]/")


# ---------------------------------------------------------------------------
# Hostname → DNS resolution
# ---------------------------------------------------------------------------


def test_rejects_localhost_resolved(patched_dns):
    patched_dns({"localhost": ["127.0.0.1", "::1"]})
    with pytest.raises(UnsafeURLError, match=r"blocked (IP literal|range)"):
        validate_url("https://localhost/foo")


def test_rejects_metadata_hostname_resolved(patched_dns):
    patched_dns({"metadata.google.internal": ["169.254.169.254"]})
    with pytest.raises(UnsafeURLError, match=r"blocked (IP literal|range)"):
        validate_url("https://metadata.google.internal/computeMetadata/v1/")


def test_rejects_internal_hostname_aws_metadata(patched_dns):
    patched_dns({"169.254.169.254.nip.io": ["169.254.169.254"]})
    with pytest.raises(UnsafeURLError, match=r"blocked (IP literal|range)"):
        validate_url("https://169.254.169.254.nip.io/")


def test_rejects_when_any_resolved_address_is_blocked(patched_dns):
    """If a hostname resolves to MULTIPLE addresses and ANY of them is
    blocked, the URL must be rejected — otherwise an attacker can publish
    A and AAAA records where one is public and the other is internal,
    and the OS-chosen address picks the internal one."""
    patched_dns({"sneaky.example.com": ["8.8.8.8", "127.0.0.1"]})
    with pytest.raises(UnsafeURLError, match=r"blocked (IP literal|range)"):
        validate_url("https://sneaky.example.com/")


def test_accepts_hostname_resolving_to_public_only(patched_dns):
    patched_dns({"good.example.com": ["93.184.216.34"]})
    assert (
        validate_url("https://good.example.com/path") == "https://good.example.com/path"
    )


def test_rejects_when_dns_resolution_fails(patched_dns):
    patched_dns({})  # no records at all
    with pytest.raises(UnsafeURLError, match="DNS resolution failed"):
        validate_url("https://nxdomain.example.com/")


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["", None, 0, 42, [], {}])
def test_rejects_empty_or_non_string(url):
    with pytest.raises(UnsafeURLError, match="Invalid URL"):
        validate_url(url)  # type: ignore[arg-type]


def test_rejects_url_with_no_host():
    with pytest.raises(UnsafeURLError, match="no host"):
        validate_url("https:///path-only")


# ---------------------------------------------------------------------------
# is_url_safe (non-raising variant)
# ---------------------------------------------------------------------------


def test_is_url_safe_returns_true_for_safe(patched_dns):
    patched_dns({"example.com": ["93.184.216.34"]})
    assert is_url_safe("https://example.com/") is True


def test_is_url_safe_returns_false_for_unsafe():
    assert is_url_safe("https://127.0.0.1/") is False
    assert is_url_safe("http://example.com/") is False
    assert is_url_safe("file:///etc/passwd") is False
    assert is_url_safe("") is False
