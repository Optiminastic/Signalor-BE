"""Google's "why isn't this page indexed" reasons, in plain words.

The text comes from Search Console's coverage states (the URL Inspection API
returns the same strings as the Page indexing report). Each reason says what
it means and who can act: the site (``fixable``) or only Google.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reason:
    key: str
    label: str
    explanation: str
    fixable: bool


# Order matters: the first match wins, so specific phrases come before general ones.
_REASONS: tuple[tuple[str, Reason], ...] = (
    (
        "noindex",
        Reason(
            "noindex",
            "Blocked by a noindex tag",
            "The page tells Google not to index it. Remove the noindex tag if the page should appear.",
            True,
        ),
    ),
    (
        "robots.txt",
        Reason("robots", "Blocked by robots.txt", "robots.txt stops Google from crawling the page.", True),
    ),
    (
        "google chose different canonical",
        Reason(
            "canonical_mismatch",
            "Google picked a different main page",
            "Google treats another URL as the main version of this page. Check the canonical tag.",
            True,
        ),
    ),
    (
        "duplicate without user-selected canonical",
        Reason(
            "duplicate",
            "Duplicate without a canonical",
            "Google sees this as a copy of another page and no canonical tag says which one is the main one.",
            True,
        ),
    ),
    (
        "alternate page with proper canonical",
        Reason(
            "alternate",
            "Alternate version (fine)",
            "This page points to another URL as the main one, as intended. Nothing to fix.",
            False,
        ),
    ),
    (
        "redirect",
        Reason(
            "redirect",
            "Page redirects",
            "The sitemap lists a URL that redirects. List the final URL instead.",
            True,
        ),
    ),
    (
        "soft 404",
        Reason(
            "soft_404",
            "Looks like an empty page",
            "Google thinks the page has no real content (a soft 404). Add content or remove it from the sitemap.",
            True,
        ),
    ),
    (
        "not found",
        Reason(
            "not_found",
            "Page not found (404)",
            "The sitemap lists a page that doesn't exist. Remove it from the sitemap or restore the page.",
            True,
        ),
    ),
    (
        "server error",
        Reason(
            "server_error",
            "Server error",
            "The site returned an error when Google tried to load the page.",
            True,
        ),
    ),
    (
        "crawled - currently not indexed",
        Reason(
            "crawled_not_indexed",
            "Crawled, not indexed yet",
            "Google read the page but chose not to index it yet, usually a quality or uniqueness signal. Improving the content helps.",
            False,
        ),
    ),
    (
        "discovered - currently not indexed",
        Reason(
            "discovered_not_indexed",
            "Found, not crawled yet",
            "Google knows the page exists but hasn't read it yet. Internal links and a fresh sitemap help.",
            False,
        ),
    ),
    (
        "unknown to google",
        Reason(
            "unknown",
            "Not known to Google yet",
            "Google hasn't seen this page. Submitting the sitemap and linking to it helps.",
            False,
        ),
    ),
    (
        "could not verify",
        Reason(
            "unverified",
            "Couldn't check",
            "Search Console didn't answer for this page this time. It will be checked again next week.",
            False,
        ),
    ),
)
_OTHER = Reason("other", "Other reason", "See this page in Search Console for Google's exact reason.", False)


def classify(coverage_state: str) -> Reason:
    """The plain-words reason for a coverage state string from Search Console."""
    text = (coverage_state or "").lower().replace("–", "-")  # Google sometimes uses an en dash
    return next((reason for phrase, reason in _REASONS if phrase in text), _OTHER)
