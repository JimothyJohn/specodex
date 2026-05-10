"""URL safety / SSRF defense for outbound user-controlled fetches.

Call ``validate_url(url)`` before passing any user-supplied URL to
``httpx``, Playwright, ``requests``, or any other HTTP client. The
check is mechanical — it rejects:

- Non-HTTPS schemes (``http://``, ``file://``, ``ftp://``,
  ``gopher://``, ``javascript:``, ``data:``, etc.).
- URLs whose hostname is or resolves to a private, link-local,
  loopback, multicast, or cloud-metadata IP. Both IPv4 and IPv6
  ranges are blocked.

If the URL passes, ``validate_url`` returns it unchanged. If not, it
raises ``UnsafeURLError``.

Known limitation — DNS rebinding. ``validate_url`` resolves the
hostname once for the safety check, but the underlying HTTP client
will resolve again at fetch time. An attacker controlling a
short-TTL DNS record can flip the answer between the two
resolutions. Closing this hole requires resolving once here, passing
the IP forward, and forcing the HTTP client to use it (with the
original ``Host`` header). The simpler check above stops the common
metadata-IP / loopback / RFC1918 cases that account for the vast
majority of real SSRF attempts; DNS-rebinding hardening is tracked
as a follow-up.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Union
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    """Raised when a URL fails the SSRF safety check."""


_IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


# IPv4 networks that must never be the destination of an outbound user
# fetch. Includes RFC1918 private, link-local + cloud metadata, loopback,
# CGNAT, IETF protocol, multicast, reserved, and broadcast.
_BLOCKED_NETS_V4: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("0.0.0.0/8"),  # "this network"
    ipaddress.IPv4Network("10.0.0.0/8"),  # RFC1918 private
    ipaddress.IPv4Network("100.64.0.0/10"),  # CGNAT (RFC6598)
    ipaddress.IPv4Network("127.0.0.0/8"),  # loopback
    ipaddress.IPv4Network("169.254.0.0/16"),  # link-local + cloud metadata
    ipaddress.IPv4Network("172.16.0.0/12"),  # RFC1918 private
    ipaddress.IPv4Network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.IPv4Network("192.168.0.0/16"),  # RFC1918 private
    ipaddress.IPv4Network("198.18.0.0/15"),  # benchmark
    ipaddress.IPv4Network("224.0.0.0/4"),  # multicast
    ipaddress.IPv4Network("240.0.0.0/4"),  # reserved
    ipaddress.IPv4Network("255.255.255.255/32"),  # broadcast
)


_BLOCKED_NETS_V6: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("::/128"),  # unspecified
    ipaddress.IPv6Network("::1/128"),  # loopback
    ipaddress.IPv6Network("::ffff:0:0/96"),  # IPv4-mapped (covered separately for v4)
    ipaddress.IPv6Network("64:ff9b::/96"),  # NAT64
    ipaddress.IPv6Network("100::/64"),  # discard-only
    ipaddress.IPv6Network("fc00::/7"),  # unique local
    ipaddress.IPv6Network("fe80::/10"),  # link-local
    ipaddress.IPv6Network("ff00::/8"),  # multicast
)


ALLOWED_SCHEMES: frozenset[str] = frozenset({"https"})


def _is_blocked_ip(ip: _IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _BLOCKED_NETS_V4)
    return any(ip in net for net in _BLOCKED_NETS_V6)


def _resolve_addresses(host: str) -> list[_IPAddress]:
    """Resolve ``host`` via DNS, returning every IP the OS would connect to.

    Uses ``socket.getaddrinfo`` with ``AF_UNSPEC`` so both A and AAAA records
    are considered. Raises ``UnsafeURLError`` if resolution fails.
    """
    try:
        infos = socket.getaddrinfo(
            host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror as e:
        raise UnsafeURLError(f"DNS resolution failed for {host!r}: {e}") from None

    addrs: list[_IPAddress] = []
    for info in infos:
        sockaddr = info[4]
        addr_str = sockaddr[0]
        try:
            addrs.append(ipaddress.ip_address(addr_str))
        except ValueError:
            continue

    if not addrs:
        raise UnsafeURLError(
            f"DNS resolution returned no usable addresses for {host!r}"
        )
    return addrs


def validate_url(url: str) -> str:
    """Validate a URL against the SSRF allowlist. Return the URL on pass.

    Raises ``UnsafeURLError`` for any of:

    - Empty / non-string input
    - URL with no host
    - Disallowed scheme (only ``https`` is allowed)
    - Host that is or resolves to an IP in the blocked ranges

    Returns the original URL string unchanged when safe.
    """
    if not isinstance(url, str) or not url:
        raise UnsafeURLError(f"Invalid URL: {url!r}")

    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"Scheme {parsed.scheme!r} not allowed; only HTTPS is permitted"
        )

    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"URL has no host: {url!r}")

    # Strip any IPv6 zone identifier ("fe80::1%eth0") — those would never
    # round-trip safely through ``ipaddress.ip_address`` anyway, but are an
    # interesting evasion trick worth being explicit about.
    if "%" in host:
        raise UnsafeURLError(
            f"URL host carries a zone identifier (likely link-local evasion): {host!r}"
        )

    # Direct IP literal in the host — check without DNS.
    # Important: parse the literal in its own try/except so the
    # ipaddress.ValueError doesn't shadow our own UnsafeURLError
    # (which is a ValueError subclass) when the literal is blocked.
    literal_ip: _IPAddress | None
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _is_blocked_ip(literal_ip):
            raise UnsafeURLError(
                f"URL host is a blocked IP literal: {literal_ip} ({host})"
            )
        return url

    addrs = _resolve_addresses(host)
    for ip in addrs:
        if _is_blocked_ip(ip):
            raise UnsafeURLError(f"URL host {host!r} resolves to a blocked range: {ip}")

    return url


def is_url_safe(url: str) -> bool:
    """Non-raising variant of :func:`validate_url`."""
    try:
        validate_url(url)
    except UnsafeURLError:
        return False
    return True
