"""SSRF guard for server-side fetches of user-supplied URLs.

The analyzer fetches whatever URL a user submits (crawl, sitemap, robots/llms.txt,
storefront password POST). Without a guard, an attacker can point us at internal
targets — ``http://169.254.169.254/latest/meta-data/...`` (cloud credentials),
``http://localhost:15672`` (RabbitMQ), private ``10.x``/``192.168.x`` services —
and read the fetched body back through the analysis result.

Two layers, because a single ingress check is bypassable by an HTTP redirect or
DNS rebinding:

1. ``validate_public_url`` — reject at ingress (serializer) if the host resolves to
   any non-public address.
2. ``guarded_session`` — a ``requests`` Session whose adapter re-validates the host
   on EVERY request it sends, so a ``302 -> http://169.254.169.254`` redirect is
   blocked mid-chain, not just the first URL.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

# Cap redirect chains so a guard can't be worn down by a long redirect loop.
MAX_REDIRECTS = 5


class SSRFValidationError(ValueError):
    """Raised when a URL points at a non-public / disallowed address."""


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Any address that isn't a normal public host is off-limits."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 169.254.0.0/16 — includes the cloud metadata IP
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _host_is_public(host: str) -> bool:
    """True only if every address ``host`` resolves to is public.

    An IP literal is checked directly; a hostname is resolved and ALL of its
    addresses must be public (a name that resolves to even one private IP is
    rejected, closing the split-horizon trick).
    """
    # IP literal — no DNS needed.
    try:
        return not _ip_is_blocked(ipaddress.ip_address(host))
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Unresolvable here — let the actual fetch surface a normal connection
        # error rather than us guessing. Nothing to block: it can't be reached.
        return True

    for info in infos:
        addr = info[4][0]
        try:
            if _ip_is_blocked(ipaddress.ip_address(addr)):
                return False
        except ValueError:
            return False
    return True


def validate_public_url(url: str) -> str:
    """Return ``url`` if it is http(s) and resolves to a public address, else raise.

    Use at ingress (serializers) so a private/loopback/metadata target is rejected
    before it is ever stored or fetched.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFValidationError("Only http and https URLs are allowed.")
    host = parsed.hostname
    if not host:
        raise SSRFValidationError("URL has no host.")
    if not _host_is_public(host):
        raise SSRFValidationError("URL points at a private or disallowed address.")
    return url


class _SSRFGuardAdapter(HTTPAdapter):
    """Re-validates the target host on every send, so redirects can't escape."""

    def send(self, request, **kwargs):  # type: ignore[override]
        validate_public_url(request.url)
        return super().send(request, **kwargs)


def guarded_session(max_redirects: int = MAX_REDIRECTS) -> requests.Session:
    """A ``requests`` Session that blocks fetches to non-public addresses.

    Every request it sends — including each redirect hop — passes through the
    guard, so it is safe to leave ``allow_redirects=True`` on the callers.
    """
    session = requests.Session()
    adapter = _SSRFGuardAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.max_redirects = max_redirects
    return session
