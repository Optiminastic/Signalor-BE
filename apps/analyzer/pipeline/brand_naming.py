"""Resolve a stable brand label for visibility: URL hostname wins over generic / mismatched names."""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Stored names that are common English words — likely wrong as a company name for search/LLM.
_GENERIC_STORED_LABELS = frozenset({
    "major", "minor", "prime", "alpha", "beta", "best", "top", "new", "home", "shop",
    "store", "blog", "news", "app", "site", "web", "online", "global", "local", "world",
    "the", "my", "our", "your", "brand", "company", "group", "media", "digital", "studio",
    "hello", "welcome", "test", "demo", "dev", "staging", "page", "main", "start",
})

# Multi-part public suffixes where registrable label is one more to the left (e.g. co.uk).
_SECOND_IN_PUBLIC_SUFFIX = frozenset({"co", "com", "org", "net", "gov", "edu", "ac", "sch", "ltd", "plc"})


def _normalize_alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def registrable_host_label(url: str) -> str:
    """Leftmost meaningful hostname label (e.g. lokmatmaharashtrian.com → lokmatmaharashtrian)."""
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        return ""
    if not host:
        return ""
    host = host.split(":")[0].removeprefix("www.")
    parts = [p for p in host.split(".") if p]
    if not parts:
        return ""
    if len(parts) >= 3 and parts[-2] in _SECOND_IN_PUBLIC_SUFFIX and len(parts[-1]) == 2:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0]


def humanize_host_label(label: str) -> str:
    """Turn a hostname slug into display text (title case, hyphen/underscore → space)."""
    if not label:
        return ""
    s = label.replace("_", " ").replace("-", " ")
    return " ".join(w.capitalize() for w in s.split() if w)


def visibility_brand_label(url: str, stored_brand_name: str = "") -> str:
    """
    Label used for brand visibility, social reach, and AI perception.

    - Primary identity is the analyzed URL's registrable hostname (humanized).
    - A non-empty stored name is kept only if it clearly matches that host or isn't generic.
    """
    host_label = registrable_host_label(url)
    from_url = humanize_host_label(host_label)
    if not from_url:
        return (stored_brand_name or "").strip() or "Unknown"

    stored = (stored_brand_name or "").strip()
    if not stored:
        return from_url

    s_norm = _normalize_alnum(stored)
    h_norm = _normalize_alnum(host_label)

    if len(s_norm) < 3:
        return from_url
    if stored.lower() in _GENERIC_STORED_LABELS:
        return from_url

    # Stored name is a substring of hostname (e.g. "Lokmat" vs lokmatmaharashtrian) — keep user's spelling.
    if s_norm in h_norm or h_norm.startswith(s_norm):
        return stored

    # Hostname starts with stored prefix (e.g. acme vs acmecorp) — prefer URL identity
    if h_norm.startswith(s_norm) and len(s_norm) >= 4:
        return stored

    # Clear mismatch (e.g. "Major" vs lokmatmaharashtrian) — URL is source of truth
    if s_norm not in h_norm and h_norm not in s_norm:
        return from_url

    return stored
