"""
Citation source attribution — extract cited URLs from prompt responses and
persist them alongside each PromptResult.

Phase 1: regex-based extraction from response text (markdown links, bare URLs,
numbered footnote references). Works as a baseline across every engine.
Google/Bing callers may pass pre-structured citations (url+title+snippet) directly.
Phase 2 will add per-provider structured extraction (Perplexity citations[],
Gemini grounding_metadata, OpenAI annotations, Claude web-search citations).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger("apps")

# Markdown link  [label](https://...)
_MD_LINK_RE = re.compile(r"\[([^\]]{1,200})\]\((https?://[^\s)]+)\)")
# Bare URL anywhere in text
_BARE_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+[^\s<>\]\)\"'.,;:!?]")
# Numbered footnote reference:  [1] https://…   or   1. https://…
_FOOTNOTE_URL_RE = re.compile(r"(?:^|\n)\s*(?:\[\d+\]|\d+[.)])\s*(https?://\S+)")
# Trailing punctuation to strip
_TRAIL_PUNCT = ".,;:!?)"

# Skip junk / internal / tracking URLs
_SKIP_HOSTS = {
    "localhost",
    "127.0.0.1",
    "example.com",
    "example.org",
    "w3.org",
    "schema.org",
}


def host_of(url: str) -> str:
    """Return the normalized (lowercase, www-stripped) host for a URL, or ""."""
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _clean_url(u: str) -> str:
    while u and u[-1] in _TRAIL_PUNCT:
        u = u[:-1]
    return u


def extract_citations_from_text(text: str) -> list[dict]:
    """
    Parse response text for cited URLs. Returns a list of {url, title, snippet, position}
    dicts, deduplicated by URL in first-seen order.

    `title` comes from the markdown link label when available, otherwise empty.
    `snippet` is left empty (engines rarely include structured snippets in free text).
    """
    if not text:
        return []

    seen: dict[str, dict] = {}
    position = 0

    def _add(url: str, title: str = "") -> None:
        nonlocal position
        url = _clean_url(url.strip())
        if not url:
            return
        host = host_of(url)
        if not host or host in _SKIP_HOSTS:
            return
        if url in seen:
            # Upgrade title if we now have a better one
            if title and not seen[url]["title"]:
                seen[url]["title"] = title[:512]
            return
        position += 1
        seen[url] = {
            "url": url[:2048],
            "title": (title or "")[:512],
            "snippet": "",
            "position": position,
        }

    for m in _MD_LINK_RE.finditer(text):
        label, url = m.group(1), m.group(2)
        _add(url, label)

    for m in _FOOTNOTE_URL_RE.finditer(text):
        _add(m.group(1))

    for m in _BARE_URL_RE.finditer(text):
        _add(m.group(0))

    return list(seen.values())


def classify(url: str, brand_host: str, competitor_hosts: set[str]) -> tuple[bool, bool]:
    """Return (is_brand, is_competitor) for the cited URL."""
    h = host_of(url)
    if not h:
        return (False, False)
    is_brand = bool(brand_host) and (h == brand_host or h.endswith("." + brand_host))
    if is_brand:
        return (True, False)
    is_competitor = any(
        ch and (h == ch or h.endswith("." + ch)) for ch in competitor_hosts
    )
    return (False, is_competitor)


def persist_prompt_result(
    track,
    result_dict: dict,
    brand_host: str,
    competitor_hosts: set[str],
):
    """
    Create a PromptResult from result_dict, and bulk-create any attached citations.

    `result_dict` may contain a `citations` key (list of {url, title, snippet, position}) —
    structured citations passed from Google/Bing callers. Any additional URLs in
    `response_text` are extracted and merged.

    Other keys in result_dict go through unchanged as PromptResult fields.
    """
    from apps.analyzer.models import PromptResult, PromptCitation

    data = dict(result_dict)
    structured = data.pop("citations", []) or []
    response_text = data.get("response_text", "") or ""

    pr = PromptResult.objects.create(prompt_track=track, **data)

    # Merge structured (from search APIs) + regex-extracted (from LLM text)
    seen_urls: set[str] = set()
    merged: list[dict] = []

    for c in structured:
        url = _clean_url((c.get("url") or "").strip())
        if not url or url in seen_urls:
            continue
        host = host_of(url)
        if not host or host in _SKIP_HOSTS:
            continue
        seen_urls.add(url)
        merged.append({
            "url": url[:2048],
            "title": (c.get("title") or "")[:512],
            "snippet": (c.get("snippet") or "")[:2000],
            "position": int(c.get("position") or (len(merged) + 1)),
        })

    for c in extract_citations_from_text(response_text):
        if c["url"] in seen_urls:
            continue
        seen_urls.add(c["url"])
        merged.append(c)

    if not merged:
        return pr

    rows: list[PromptCitation] = []
    for c in merged:
        is_brand, is_competitor = classify(c["url"], brand_host, competitor_hosts)
        rows.append(
            PromptCitation(
                prompt_result=pr,
                url=c["url"],
                domain=host_of(c["url"]),
                title=c["title"],
                snippet=c.get("snippet", ""),
                position=c["position"],
                is_brand=is_brand,
                is_competitor=is_competitor,
            )
        )

    try:
        PromptCitation.objects.bulk_create(rows, ignore_conflicts=True)
    except Exception as exc:
        logger.warning("bulk_create citations failed for PromptResult %s: %s", pr.pk, exc)
    return pr


def competitor_hosts_for_run(run) -> set[str]:
    """Return the set of normalized competitor hosts for an AnalysisRun."""
    hosts: set[str] = set()
    for c in run.competitors.all():
        h = host_of(c.url or "")
        if h:
            hosts.add(h)
    return hosts
