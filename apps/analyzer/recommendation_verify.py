"""
Re-fetch the analyzed URL and heuristically verify that a recommendation's fix
appears to be present. Used instead of trusting manual "mark as done" checkboxes.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .models import AnalysisRun, Recommendation
from .pipeline.crawler import check_file_exists, fetch_file_content
from .pipeline.technical import _check_robots_allows_ai

logger = logging.getLogger("apps")

# Pipeline finding key → verifier kind (on-page recommendations only).
FINDING_TO_VERIFY_KIND: dict[str, str] = {
    "no_h1": "h1_present",
    "multiple_h1": "single_h1",
    "broken_heading_hierarchy": "headings",
    "no_faq_section": "faq",
    "no_lists": "lists",
    "no_answer_first": "content_soft",
    "few_internal_links": "internal_links",
    "no_citations": "citations",
    "no_statistics": "statistics",
    "no_expert_quotes": "expert_quotes",
    "weak_authoritative_tone": "content_soft",
    "poor_readability": "content_soft",
    "no_technical_terms": "content_soft",
    "low_vocabulary_diversity": "content_soft",
    "low_word_count": "word_count",
    "poor_paragraph_structure": "content_soft",
    "keyword_stuffing": "content_soft",
    "no_jsonld": "schema",
    "invalid_jsonld_structure": "schema_valid",
    "no_faqpage_schema": "schema_faqpage",
    "no_article_schema": "schema_article",
    "no_organization_schema": "schema_organization",
    "incomplete_article_schema": "schema_article",
    "incomplete_organization_schema": "schema_organization",
    "incomplete_faqpage_schema": "schema_faqpage",
    "incomplete_product_schema": "schema_product",
    "incomplete_blogposting_schema": "schema_article",
    "incomplete_newsarticle_schema": "schema_article",
    "incomplete_howto_schema": "schema_howto",
    "schema_disconnected_graph": "schema_valid",
    "no_author": "author",
    "no_author_bio": "author_bio",
    "no_publish_date": "publish_date",
    "no_updated_date": "updated_date",
    "few_external_citations": "citations",
    "no_trust_links": "trust_links",
    "low_source_diversity": "source_diversity",
    "no_about_page": "about_link",
    "no_first_hand_experience": "content_soft",
    "no_expertise_indicators": "content_soft",
    "low_authority": "content_soft",
    "low_trust_signals": "footer_trust",
    "generic_ai_writing_detected": "content_soft",
    "no_llms_txt": "llms",
    "ai_bots_blocked": "robots_ai_allow",
    "no_sitemap": "sitemap",
    "crawl_failed": "page_reachable",
    "meta_noindex": "meta_indexable",
    "no_https": "https_check",
    "slow_load_time": "manual_speed",
    "no_viewport": "viewport",
    "no_canonical": "canonical",
    "crawl_blocked_403": "page_reachable",
    "crawl_timeout": "page_reachable",
    "low_text_html_ratio": "content_soft",
    "js_dependent_content": "manual_js",
    "no_social_profiles": "social_links",
}

_MANUAL_KIND_MESSAGES: dict[str, str] = {
    "manual_speed": (
        "Load time is not re-measured in this check. Confirm performance with Lighthouse "
        "or your host/CDN tools on the published URL."
    ),
    "manual_js": (
        "Heavy JavaScript rendering cannot be fully verified here. Open the live page in a "
        "browser and confirm your content appears after publish."
    ),
}


def _base_host(url: str) -> str:
    try:
        h = (urlparse(url).netloc or "").lower()
        if h.startswith("www."):
            h = h[4:]
        return h
    except Exception:
        return ""


def _fetch_html(run: AnalysisRun) -> tuple[str, BeautifulSoup | None]:
    from .pipeline.crawler import crawl_page

    cr = crawl_page(run.url)
    if cr.ok and cr.soup:
        return cr.html or "", cr.soup
    try:
        from .tasks import _crawl_via_integration

        alt = _crawl_via_integration(run)
        if alt and alt.ok and alt.soup:
            return alt.html or "", alt.soup
    except Exception as exc:
        logger.warning("verify: integration crawl fallback failed run=%s: %s", run.id, exc)
    return "", None


def _count_external_links(soup: BeautifulSoup, page_url: str) -> int:
    base = _base_host(page_url)
    n = 0
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if href.startswith("http"):
            if _base_host(href) and _base_host(href) != base:
                n += 1
    return n


def _has_ref_section(soup: BeautifulSoup) -> bool:
    text = soup.get_text(" ", strip=True).lower()
    return bool(
        re.search(
            r"\b(references|sources|citations|bibliography|works cited|further reading)\b",
            text,
        )
    )


def _json_ld_blocks(soup: BeautifulSoup) -> list[dict]:
    out: list[dict] = []
    for script in soup.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                out.append(data)
            elif isinstance(data, list):
                out.extend(x for x in data if isinstance(x, dict))
        except json.JSONDecodeError:
            continue
    return out


def _json_ld_types(blocks: list[dict]) -> set[str]:
    types: set[str] = set()

    def walk(obj):
        if isinstance(obj, dict):
            t = obj.get("@type")
            if isinstance(t, str):
                types.add(t.lower())
            elif isinstance(t, list):
                for x in t:
                    if isinstance(x, str):
                        types.add(x.lower())
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)

    for b in blocks:
        walk(b)
    return types


def _json_ld_has_valid_context(blocks: list[dict]) -> bool:
    return any((b.get("@context") if isinstance(b, dict) else None) for b in blocks)


def _external_http_hosts(soup: BeautifulSoup, page_url: str) -> list[str]:
    base = _base_host(page_url)
    hosts: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href.startswith("http"):
            continue
        h = _base_host(href)
        if h and h != base:
            hosts.append(h)
    return hosts


def _meta_robots_has_noindex(soup: BeautifulSoup) -> bool:
    for m in soup.find_all("meta"):
        name = (m.get("name") or "").lower()
        prop = (m.get("property") or "").lower()
        if name == "robots" or prop == "robots":
            content = (m.get("content") or "").lower()
            if "noindex" in content:
                return True
    return False


def _has_publish_date_signal(soup: BeautifulSoup) -> bool:
    for m in soup.find_all("meta"):
        prop = (m.get("property") or "").lower()
        name = (m.get("name") or "").lower()
        if "published_time" in prop or "published_time" in name:
            if (m.get("content") or "").strip():
                return True
        if prop in ("article:published_time", "og:published_time"):
            if (m.get("content") or "").strip():
                return True
    for t in soup.find_all("time"):
        if t.get("datetime") and len((t.get("datetime") or "").strip()) >= 8:
            return True
    return False


def _has_updated_date_signal(soup: BeautifulSoup) -> bool:
    for m in soup.find_all("meta"):
        prop = (m.get("property") or "").lower()
        name = (m.get("name") or "").lower()
        if "modified_time" in prop or "modified_time" in name or prop == "og:updated_time":
            if (m.get("content") or "").strip():
                return True
    text = soup.get_text(" ", strip=True).lower()
    if "last updated" in text or "updated on" in text:
        if re.search(r"\b20\d{2}\b", text):
            return True
    return False


def _has_author_bio_signal(soup: BeautifulSoup) -> bool:
    if soup.select_one(".author-bio, [class*='author-bio'], [itemprop='author']"):
        return True
    for fig in soup.find_all(["aside", "section", "div"]):
        cls = " ".join(fig.get("class") or []).lower()
        if "author" in cls and fig.find("img") and len(fig.get_text(strip=True)) > 80:
            return True
    return False


_TRUST_TLD_SUFFIXES = (".gov", ".edu", ".mil")
_TRUST_ROOTS = frozenset(
    {
        "wikipedia.org",
        "nih.gov",
        "who.int",
        "un.org",
        "nature.com",
        "science.org",
        "arxiv.org",
        "pubmed.ncbi.nlm.nih.gov",
    }
)


def _count_trust_external_links(soup: BeautifulSoup, page_url: str) -> int:
    n = 0
    for h in _external_http_hosts(soup, page_url):
        hl = h.lower()
        if any(hl.endswith(s) for s in _TRUST_TLD_SUFFIXES):
            n += 1
            continue
        for root in _TRUST_ROOTS:
            if hl == root or hl.endswith("." + root):
                n += 1
                break
    return n


def _distinct_external_domains(soup: BeautifulSoup, page_url: str) -> int:
    return len(set(_external_http_hosts(soup, page_url)))


_SOCIAL_FRAGMENTS = (
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "github.com",
    "youtube.com",
    "tiktok.com",
)


def _has_social_profile_links(soup: BeautifulSoup) -> bool:
    for a in soup.find_all("a", href=True):
        h = (a.get("href") or "").lower()
        if any(s in h for s in _SOCIAL_FRAGMENTS):
            return True
    return False


def _footer_trust_signals(soup: BeautifulSoup) -> int:
    """How many distinct trust/footer link types (contact, privacy, terms, about)."""
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = (a.get("href") or "").lower()
        if "contact" in h:
            seen.add("contact")
        if "privacy" in h:
            seen.add("privacy")
        if "terms" in h or "legal" in h:
            seen.add("terms")
        if "/about" in h:
            seen.add("about")
    return len(seen)


def _content_soft_result(soup: BeautifulSoup, page_url: str, base_host: str) -> dict:
    """Several copy-quality tips share weak signals; pass if the page shows enough improvement."""
    text = soup.get_text(" ", strip=True)
    signals: list[str] = []
    ext = _count_external_links(soup, page_url)
    if ext >= 2:
        signals.append(f"{ext} external link(s)")
    if soup.find(["ul", "ol"]):
        signals.append("lists")
    if soup.find("blockquote"):
        signals.append("blockquotes")
    if re.search(r"\d+\s*%|\d{1,3}(?:,\d{3})+\b|\b20\d{2}\b", text):
        signals.append("numeric/stat-style data")
    if len(text) >= 1200:
        signals.append("substantial copy")
    if soup.find("h1"):
        signals.append("H1")
    internal = 0
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if href.startswith("/") or (base_host and base_host in href):
            internal += 1
    if internal >= 3:
        signals.append(f"{internal} internal links")
    if _json_ld_blocks(soup):
        signals.append("JSON-LD")

    if len(signals) >= 2:
        return {
            "status": "verified",
            "message": "Verified: live page shows "
            + ", ".join(signals[:5])
            + ". Re-run analysis to update your score.",
        }
    if ext >= 3 or _has_ref_section(soup):
        return {
            "status": "verified",
            "message": "Verified: strong outbound links or a references-style section on the live page.",
        }
    bits: list[str] = []
    if ext >= 1:
        bits.append(f"{ext} external link(s)")
    if soup.find(["ul", "ol"]):
        bits.append("lists")
    if soup.find("blockquote"):
        bits.append("blockquote(s)")
    if re.search(r"\d+\s*%|\d{1,3}(?:,\d{3})+\b|\b20\d{2}\b", text):
        bits.append("numbers")
    if len(text) >= 400:
        bits.append(f"~{len(text)} chars text")
    if soup.find("h1"):
        bits.append("H1")
    if internal >= 1:
        bits.append(f"{internal} internal link(s)")
    if _json_ld_blocks(soup):
        bits.append("JSON-LD")
    summary = ", ".join(bits) if bits else "no matching signals"
    return {
        "status": "failed",
        "message": (
            f"Verification failed — saw {summary}; need at least two different improvement signals on the live page."
        ),
    }


def _unmapped_scope_label(rec: Recommendation) -> str:
    """Short label for tips with no automated verifier (avoid 'technical / technical')."""
    p = (rec.pillar or "").strip()
    c = (rec.category or "").strip()
    if p and c and p.lower() == c.lower():
        return p
    if p and c:
        return f"{p} · {c}"
    return p or c or "this recommendation type"


def _verify_kind(rec: Recommendation) -> str:
    """Map a recommendation to a verifier. Order: finding_code → titles → pillar fallback."""
    code = (getattr(rec, "finding_code", None) or "").strip()
    if code and code in FINDING_TO_VERIFY_KIND:
        return FINDING_TO_VERIFY_KIND[code]

    title = (rec.title or "").lower()
    desc = (rec.description or "").lower()
    combined = f"{title} {desc}"
    cat = (rec.category or "").lower()
    pillar = (rec.pillar or "").lower()

    # ── High-signal GEO / content rules (match RECOMMENDATION_RULES titles) ──
    if "citation" in title or "citation" in desc or "authoritative citation" in combined:
        return "citations"
    if "statistic" in title or "data point" in title or "quantitative" in desc:
        return "statistics"
    if "expert quote" in combined or ("quote" in title and "expert" in combined):
        return "expert_quotes"
    if "structured list" in title or (
        "list" in title and "internal" not in title and "checklist" not in title
    ):
        return "lists"
    if ("expand" in title and "content" in title) or "thin content" in desc or (
        "word" in title and "count" in title
    ):
        return "word_count"
    if "h1 tag" in title or title.startswith("add an h1"):
        return "h1_present"
    if "only one h1" in title or "multiple h1" in title:
        return "single_h1"
    if "heading hierarchy" in title or ("heading" in title and "fix" in title):
        return "headings"
    if "internal link" in title:
        return "internal_links"
    # Soft copy edits (tone, readability, vocabulary, paragraphs, answer-first, keywords, jargon)
    if any(
        k in title or k in desc
        for k in (
            "readability",
            "authoritative tone",
            "vocabulary",
            "paragraph",
            "answer-first",
            "answer first",
            "keyword stuff",
            "domain-specific",
            "technical terms",
            "fluency",
        )
    ):
        return "content_soft"

    if "faq" in title or ("question" in title and "answer" in desc):
        return "faq"
    if "json-ld" in combined or "structured data" in combined or (
        cat == "schema" and "faqpage" not in title
    ):
        return "schema"
    if "meta description" in title or "meta description" in desc:
        return "meta_description"
    if "robots.txt" in title or ("robots" in title and "txt" in title):
        return "robots"
    if "llms.txt" in title or ("llms" in title and "txt" in title):
        return "llms"
    if "canonical" in title:
        return "canonical"
    if "viewport" in title:
        return "viewport"
    if "author" in title or "byline" in combined or "author information" in title:
        return "author"
    if "about page" in title or "about us" in title:
        return "about_link"
    if "contact" in title and "page" in title:
        return "contact_link"
    if "privacy" in title and "policy" in title:
        return "privacy_link"
    if "heading" in title or "h1" in title or "h2" in title:
        return "headings"

    # Pillar fallbacks — most unmatched items are content or schema
    if pillar == "schema" or cat == "schema":
        return "schema"
    if pillar == "content" or cat == "content":
        return "content_soft"
    if pillar == "technical" or cat == "technical":
        return "technical_bundle"
    if pillar == "eeat" or cat == "eeat":
        return "eeat_bundle"

    return "generic"


def verify_recommendation_fix(run: AnalysisRun, rec: Recommendation) -> dict:
    """
    Returns a dict suitable for the auto-fix verify API:
    { recommendation_id, status: 'verified'|'failed'|'manual', message, fix_type }
    """
    rid = rec.id
    html, soup = _fetch_html(run)
    if not soup:
        return {
            "recommendation_id": rid,
            "status": "failed",
            "message": "Verification failed — could not load the live URL (blocked, offline, or wrong address).",
            "fix_type": "verification",
        }

    kind = _verify_kind(rec)
    base_host = _base_host(run.url or "")
    page_url = run.url or ""

    try:
        if kind in _MANUAL_KIND_MESSAGES:
            return {
                "recommendation_id": rid,
                "status": "manual",
                "message": _MANUAL_KIND_MESSAGES[kind],
                "fix_type": "verification",
            }

        if kind == "page_reachable":
            return {
                "recommendation_id": rid,
                "status": "verified",
                "message": "Verified: live URL returned HTML for this check (page is reachable).",
                "fix_type": "verification",
            }

        if kind == "https_check":
            if urlparse(page_url).scheme.lower() == "https":
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: analyzed URL uses HTTPS.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — URL is not HTTPS (scheme is not https).",
                "fix_type": "verification",
            }

        if kind == "meta_indexable":
            if not _meta_robots_has_noindex(soup):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: no noindex directive found in meta robots.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — meta robots still contains noindex.",
                "fix_type": "verification",
            }

        if kind == "schema_valid":
            blocks = _json_ld_blocks(soup)
            if _json_ld_has_valid_context(blocks):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: JSON-LD with @context is present on the live page.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no valid JSON-LD block with @context found.",
                "fix_type": "verification",
            }

        if kind == "schema_faqpage":
            types = _json_ld_types(_json_ld_blocks(soup))
            if "faqpage" in types or "question" in types:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: FAQPage or Question JSON-LD detected.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no FAQPage/Question type in JSON-LD.",
                "fix_type": "verification",
            }

        if kind == "schema_organization":
            types = _json_ld_types(_json_ld_blocks(soup))
            if "organization" in types:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: Organization JSON-LD detected.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no Organization type in JSON-LD.",
                "fix_type": "verification",
            }

        if kind == "schema_article":
            types = _json_ld_types(_json_ld_blocks(soup))
            if types & {"article", "blogposting", "newsarticle"}:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: Article, BlogPosting, or NewsArticle JSON-LD detected.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no Article/BlogPosting/NewsArticle type in JSON-LD.",
                "fix_type": "verification",
            }

        if kind == "schema_product":
            types = _json_ld_types(_json_ld_blocks(soup))
            if "product" in types:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: Product JSON-LD detected.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no Product type in JSON-LD.",
                "fix_type": "verification",
            }

        if kind == "schema_howto":
            types = _json_ld_types(_json_ld_blocks(soup))
            if "howto" in types or "howtostep" in types:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: HowTo JSON-LD detected.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no HowTo/HowToStep type in JSON-LD.",
                "fix_type": "verification",
            }

        if kind == "publish_date":
            if _has_publish_date_signal(soup):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: publish date signal found (meta or <time datetime>).",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no article:published_time, og:published_time, or <time datetime> detected.",
                "fix_type": "verification",
            }

        if kind == "updated_date":
            if _has_updated_date_signal(soup):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: modified/updated date signal found.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no modified_time meta or clear “last updated” + year in body.",
                "fix_type": "verification",
            }

        if kind == "author_bio":
            if _has_author_bio_signal(soup):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: author/bio block detected on the live page.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no author-bio-style section (image + text or .author-bio) detected.",
                "fix_type": "verification",
            }

        if kind == "trust_links":
            tn = _count_trust_external_links(soup, page_url)
            if tn >= 1:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": f"Verified: found {tn} outbound link(s) to high-trust domains (.gov/.edu/Wikipedia-class).",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no outbound links to .gov, .edu, or similar high-trust hosts.",
                "fix_type": "verification",
            }

        if kind == "source_diversity":
            ddom = _distinct_external_domains(soup, page_url)
            if ddom >= 3:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": f"Verified: {ddom} distinct external domains linked from the page.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": f"Verification failed — {ddom} distinct external domain(s); rule looks for ≥3.",
                "fix_type": "verification",
            }

        if kind == "footer_trust":
            fts = _footer_trust_signals(soup)
            if fts >= 2:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: multiple trust/footer links found (contact, privacy, terms, or about).",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — add at least two of: contact, privacy/terms, or about links in the page.",
                "fix_type": "verification",
            }

        if kind == "social_links":
            if _has_social_profile_links(soup):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: social profile link(s) (LinkedIn, X/Twitter, GitHub, etc.) detected.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no common social network profile URLs in anchor hrefs.",
                "fix_type": "verification",
            }

        if kind == "sitemap":
            if check_file_exists(page_url, "sitemap.xml") or check_file_exists(page_url, "sitemap_index.xml"):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: sitemap.xml or sitemap_index.xml responds at the site root.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no sitemap.xml or sitemap_index.xml at the origin (HTTP 200).",
                "fix_type": "verification",
            }

        if kind == "robots_ai_allow":
            robots_txt = fetch_file_content(page_url, "robots.txt")
            allows, blocked = _check_robots_allows_ai(robots_txt)
            if allows:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: robots.txt does not blanket-disallow major AI crawlers.",
                    "fix_type": "verification",
                }
            bl = ", ".join(blocked[:5]) if blocked else "rules"
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": f"Verification failed — robots.txt still appears to block AI crawlers ({bl}).",
                "fix_type": "verification",
            }

        if kind == "technical_bundle":
            tbits: list[str] = []
            tscore = 0
            if urlparse(page_url).scheme.lower() == "https":
                tscore += 1
                tbits.append("HTTPS")
            if soup.find("meta", attrs={"name": lambda x: x and str(x).lower() == "viewport"}):
                tscore += 1
                tbits.append("viewport")
            link = soup.find("link", rel=lambda x: x and "canonical" in str(x).lower())
            if link and (link.get("href") or "").strip().startswith("http"):
                tscore += 1
                tbits.append("canonical")
            if not _meta_robots_has_noindex(soup):
                tscore += 1
                tbits.append("indexable robots meta")
            if check_file_exists(page_url, "robots.txt"):
                tscore += 1
                tbits.append("robots.txt")
            if _json_ld_blocks(soup):
                tscore += 1
                tbits.append("JSON-LD")
            if tscore >= 2:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: technical signals on live page — " + ", ".join(tbits[:6]) + ".",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — page still missing multiple basic technical signals (HTTPS, viewport, canonical, indexable meta, robots.txt, or JSON-LD).",
                "fix_type": "verification",
            }

        if kind == "eeat_bundle":
            ebits: list[str] = []
            escore = 0
            if soup.find("meta", attrs={"name": re.compile(r"^author$", re.I)}):
                escore += 1
                ebits.append("author meta")
            if soup.find("meta", attrs={"property": re.compile(r"article:", re.I)}):
                escore += 1
                ebits.append("article Open Graph/meta")
            if _json_ld_blocks(soup):
                escore += 1
                ebits.append("JSON-LD")
            extn = _count_external_links(soup, page_url)
            if extn >= 2:
                escore += 1
                ebits.append(f"{extn} external link(s)")
            if _has_social_profile_links(soup):
                escore += 1
                ebits.append("social links")
            if _has_author_bio_signal(soup):
                escore += 1
                ebits.append("author bio block")
            if escore >= 2:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: E-E-A-T-style signals — " + ", ".join(ebits[:6]) + ".",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — need at least two signals among author meta, article meta, JSON-LD, external links, social links, or author bio.",
                "fix_type": "verification",
            }

        if kind == "h1_present":
            if soup.find("h1"):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: an H1 is present on the live page.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no <h1> found on the live page.",
                "fix_type": "verification",
            }

        if kind == "single_h1":
            n = len(soup.find_all("h1"))
            if n == 1:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: exactly one H1 on the live page.",
                    "fix_type": "verification",
                }
            if n == 0:
                msg = "Verification failed — no H1 on the live page."
            else:
                msg = f"Verification failed — found {n} H1 elements (rule requires exactly one)."
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": msg,
                "fix_type": "verification",
            }

        if kind == "statistics":
            text = soup.get_text(" ", strip=True)
            if re.search(r"\d+\s*%|\d{1,3}(?:,\d{3})+\b|\b20\d{2}\b|\d+\.\d+", text):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: numeric data or percentages found on the live page.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no statistics-style numbers (%, years, decimals) detected in page text.",
                "fix_type": "verification",
            }

        if kind == "expert_quotes":
            text = soup.get_text(" ", strip=True)
            if soup.find("blockquote") or "“" in text or "”" in text or re.search(
                r'"\s*[A-Z][^"]{12,}"', text
            ):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: blockquote or attributed quote-style text detected.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no blockquote or attributed quote pattern detected.",
                "fix_type": "verification",
            }

        if kind == "lists":
            if soup.find(["ul", "ol"]):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: structured list (ul/ol) found on the live page.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no <ul> or <ol> list found on the live page.",
                "fix_type": "verification",
            }

        if kind == "word_count":
            text = soup.get_text(" ", strip=True)
            if len(text) >= 800:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": f"Verified: page has substantial text (~{len(text)} visible characters).",
                    "fix_type": "verification",
                }
            nchars = len(text)
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": f"Verification failed — visible text ~{nchars} characters (threshold ~800).",
                "fix_type": "verification",
            }

        if kind == "content_soft":
            r = _content_soft_result(soup, run.url or "", base_host)
            return {
                "recommendation_id": rid,
                "status": r["status"],
                "message": r["message"],
                "fix_type": "verification",
            }

        if kind == "citations":
            ext = _count_external_links(soup, run.url or "")
            ref = _has_ref_section(soup)
            if ext >= 3 or (ref and ext >= 1):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": f"Verified: found {ext} external citation link(s)"
                    + (" and a references-style section." if ref else "."),
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": (
                    f"Verification failed — {ext} outbound link(s) to other domains; "
                    f"references-style section: {'yes' if ref else 'no'}. "
                    f"Rule: ≥3 outbound links, or a references block plus ≥1 outbound link."
                ),
                "fix_type": "verification",
            }

        if kind == "faq":
            blocks = _json_ld_blocks(soup)
            types = _json_ld_types(blocks)
            if "faqpage" in types or "question" in types:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: FAQ structured data detected on the page.",
                    "fix_type": "verification",
                }
            text = soup.get_text(" ", strip=True).lower()
            if "faq" in text and "?" in text:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: FAQ-style content detected on the page.",
                    "fix_type": "verification",
                }
            has_faq_word = "faq" in text
            tail = (
                "body mentions FAQ but no Q&A pattern detected."
                if has_faq_word
                else "no FAQ schema or Q&A pattern in body."
            )
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": f"Verification failed — no FAQPage/Question JSON-LD; {tail}",
                "fix_type": "verification",
            }

        if kind == "schema":
            blocks = _json_ld_blocks(soup)
            if blocks:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: JSON-LD structured data is present.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no JSON-LD (<script type=\"application/ld+json\">) on the live page.",
                "fix_type": "verification",
            }

        if kind == "meta_description":
            m = soup.find("meta", attrs={"name": lambda x: x and x.lower() == "description"})
            content = (m.get("content") or "").strip() if m else ""
            if len(content) >= 20:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: meta description is present.",
                    "fix_type": "verification",
                }
            ln = len(content)
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": f"Verification failed — meta description missing or too short ({ln} chars; need ~20+).",
                "fix_type": "verification",
            }

        if kind == "robots":
            parsed = urlparse(run.url or "")
            origin = f"{parsed.scheme}://{parsed.netloc}"
            try:
                r = requests.get(f"{origin}/robots.txt", timeout=12, headers={"User-Agent": "Mozilla/5.0 (compatible; SignalorVerify/1.0)"})
                if r.ok and "user-agent" in (r.text or "").lower():
                    return {
                        "recommendation_id": rid,
                        "status": "verified",
                        "message": "Verified: robots.txt is reachable and looks valid.",
                        "fix_type": "verification",
                    }
            except Exception:
                pass
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — /robots.txt missing, not HTTP 200, or no User-agent rules.",
                "fix_type": "verification",
            }

        if kind == "llms":
            parsed = urlparse(run.url or "")
            origin = f"{parsed.scheme}://{parsed.netloc}"
            for path in ("/llms.txt", "/apps/signalor/llms.txt"):
                try:
                    r = requests.get(f"{origin}{path}", timeout=12, headers={"User-Agent": "Mozilla/5.0 (compatible; SignalorVerify/1.0)"})
                    if r.ok and len((r.text or "").strip()) > 10:
                        return {
                            "recommendation_id": rid,
                            "status": "verified",
                            "message": f"Verified: llms.txt found at {path}.",
                            "fix_type": "verification",
                        }
                except Exception:
                    continue
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — llms.txt not found or empty at /llms.txt or /apps/signalor/llms.txt.",
                "fix_type": "verification",
            }

        if kind == "canonical":
            link = soup.find("link", rel=lambda x: x and "canonical" in str(x).lower())
            href = (link.get("href") or "").strip() if link else ""
            if href.startswith("http"):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: canonical link tag is present.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no <link rel=\"canonical\"> with an http(s) URL.",
                "fix_type": "verification",
            }

        if kind == "viewport":
            m = soup.find("meta", attrs={"name": lambda x: x and x.lower() == "viewport"})
            if m and (m.get("content") or "").strip():
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: viewport meta tag is present.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no <meta name=\"viewport\"> on the live page.",
                "fix_type": "verification",
            }

        if kind == "author":
            if soup.select_one('meta[property="article:author"]') or soup.find(
                "meta", attrs={"name": re.compile(r"^author$", re.I)}
            ):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: author metadata detected.",
                    "fix_type": "verification",
                }
            if soup.find("a", rel=lambda x: x and "author" in str(x).lower()):
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: author link (rel=author) detected.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no article:author, meta author, or rel=author link found.",
                "fix_type": "verification",
            }

        if kind in ("about_link", "contact_link", "privacy_link"):
            internal = 0
            for a in soup.find_all("a", href=True):
                h = (a.get("href") or "").lower()
                if kind == "about_link" and "/about" in h:
                    internal += 1
                if kind == "contact_link" and "contact" in h:
                    internal += 1
                if kind == "privacy_link" and "privacy" in h:
                    internal += 1
            if internal > 0:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": "Verified: matching navigation/footer link found.",
                    "fix_type": "verification",
                }
            need = {"about_link": "/about", "contact_link": "contact", "privacy_link": "privacy"}[kind]
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": f"Verification failed — no anchor href containing '{need}' found.",
                "fix_type": "verification",
            }

        if kind == "headings":
            for tag in ("h1", "h2", "h3"):
                if soup.find(tag):
                    return {
                        "recommendation_id": rid,
                        "status": "verified",
                        "message": "Verified: heading structure (h1–h3) is present.",
                        "fix_type": "verification",
                    }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": "Verification failed — no h1, h2, or h3 elements on the live page.",
                "fix_type": "verification",
            }

        if kind == "internal_links":
            internal = 0
            for a in soup.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                if href.startswith("/") or (base_host and base_host in href):
                    internal += 1
            if internal >= 3:
                return {
                    "recommendation_id": rid,
                    "status": "verified",
                    "message": f"Verified: found {internal} internal links.",
                    "fix_type": "verification",
                }
            return {
                "recommendation_id": rid,
                "status": "failed",
                "message": f"Verification failed — {internal} internal link(s) (rule requires ≥3).",
                "fix_type": "verification",
            }

        # Unmapped tip: live page was fetched but we have no rule for this item (not a user failure)
        scope = _unmapped_scope_label(rec)
        return {
            "recommendation_id": rid,
            "status": "manual",
            "message": (
                f"No automated live check for this item yet ({scope}). "
                f"Confirm the change on your published page in the CMS or dev tools."
            ),
            "fix_type": "verification",
        }

    except Exception as exc:
        logger.exception("verify_recommendation_fix failed run=%s rec=%s", run.id, rec.id)
        return {
            "recommendation_id": rid,
            "status": "failed",
            "message": f"Verification failed — server error while checking: {exc!s}",
            "fix_type": "verification",
        }
