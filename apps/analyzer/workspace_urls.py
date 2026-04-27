"""Normalize site URLs for workspace / onboarding checks."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_workspace_url(raw: str) -> str:
    """
    Compare hosts/paths across http(s) and optional www. prefix.
    Empty input returns "".
    """
    u = (raw or "").strip().lower()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    parsed = urlparse(u)
    host = parsed.netloc or ""
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}".rstrip("/") if path else host
