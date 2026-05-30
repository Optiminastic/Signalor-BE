"""Helpers for the Organization model — kept tiny on purpose."""

from urllib.parse import urlparse


def normalize_url(url: str) -> str:
    """
    Canonicalize a website URL for dedup + indexing.

    Returns the bare host (lowercase, no scheme, no ``www.``, no port, no
    path). Empty/unparseable input returns "".

    Examples (all → "signalor.ai"):
        signalor.ai
        https://signalor.ai
        https://www.signalor.ai/
        HTTPS://Signalor.AI/pricing?ref=x

    The path is intentionally dropped — for onboarding, two orgs at the same
    host are the same business, even if the user pasted different sub-pages.
    """
    if not url:
        return ""
    s = url.strip().lower()
    if not s:
        return ""

    # urlparse needs a scheme to populate netloc; add one if missing so a
    # bare-host input like "signalor.ai" still parses correctly.
    if "://" not in s:
        s = "https://" + s

    try:
        host = urlparse(s).netloc
    except Exception:
        return ""

    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if ":" in host:  # strip port (rare for prod URLs)
        host = host.split(":", 1)[0]
    return host
