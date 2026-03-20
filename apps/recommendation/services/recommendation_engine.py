from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Recommendation:
    title: str
    priority: str
    category: str
    reason: str
    action: str
    evidence: list[str]


_RULES: list[dict] = [
    {
        "title": "Publish a robots.txt file",
        "priority": "high",
        "category": "site_foundation",
        "triggers": [
            "no robots.txt file was found",
            "no robots.txt",
        ],
        "reason": "Crawlers may not discover crawl rules or sitemap references.",
        "action": "Add a robots.txt with sitemap URL(s) and allow AI crawlers.",
    },
    {
        "title": "Generate and submit an XML sitemap",
        "priority": "high",
        "category": "site_foundation",
        "triggers": [
            "no xml sitemap was found",
            "no xml sitemap",
        ],
        "reason": "Sitemaps accelerate indexing and improve discovery coverage.",
        "action": "Create /sitemap.xml and reference it in robots.txt.",
    },
    {
        "title": "Enable HTTPS everywhere",
        "priority": "high",
        "category": "site_foundation",
        "triggers": [
            "site is not served over https",
            "not served over https",
            "no https",
        ],
        "reason": "HTTPS is a baseline trust and security signal.",
        "action": "Install TLS and redirect all HTTP traffic to HTTPS.",
    },
    {
        "title": "Add canonical tags",
        "priority": "medium",
        "category": "indexability",
        "triggers": [
            "no canonical tag was found",
            "conflicting canonical tags",
        ],
        "reason": "Canonical tags prevent duplicate-content ambiguity.",
        "action": "Add a single, self-referencing canonical tag to each page.",
    },
    {
        "title": "Publish llms.txt for AI discovery",
        "priority": "medium",
        "category": "llm_readiness",
        "triggers": [
            "no llms.txt file was found",
            "llms.txt file was found but contains minimal content",
        ],
        "reason": "LLM discovery improves AI visibility and citation eligibility.",
        "action": "Publish /.well-known/llms.txt with a short site summary and key URLs.",
    },
    {
        "title": "Add JSON-LD structured data",
        "priority": "high",
        "category": "structured_data",
        "triggers": [
            "no json-ld structured data was found",
            "no json-ld structured data",
            "no json-ld",
        ],
        "reason": "Structured data helps engines interpret entities and content.",
        "action": "Add JSON-LD for Organization, Article/Product, FAQPage where applicable.",
    },
    {
        "title": "Implement Open Graph tags",
        "priority": "medium",
        "category": "metadata",
        "triggers": [
            "og:title meta tag is absent",
            "og:description meta tag is absent",
            "og:image meta tag is absent",
            "og:url meta tag is absent",
            "og:site_name meta tag is absent",
        ],
        "reason": "Social previews and crawlers rely on OG metadata.",
        "action": "Add og:title, og:description, og:image, og:url, og:site_name.",
    },
    {
        "title": "Add Twitter Card tags",
        "priority": "low",
        "category": "metadata",
        "triggers": [
            "twitter:card meta tag is absent",
            "twitter:title meta tag is absent",
            "twitter:description meta tag is absent",
            "twitter:image meta tag is absent",
        ],
        "reason": "Improves visibility and consistency for social sharing.",
        "action": "Add twitter:card, twitter:title, twitter:description, twitter:image.",
    },
    {
        "title": "Add author bylines and bios",
        "priority": "medium",
        "category": "trust",
        "triggers": [
            "no author byline or attribution was found",
            "no person schema markup was found",
            "no professional credentials were found",
        ],
        "reason": "Author transparency improves E-E-A-T credibility.",
        "action": "Add author bylines, author pages, and Person schema with credentials.",
    },
    {
        "title": "Add trust pages",
        "priority": "medium",
        "category": "trust",
        "triggers": [
            "no trust pages",
            "no trust pages (about, contact, privacy, terms)",
        ],
        "reason": "Trust pages improve credibility and compliance.",
        "action": "Add About, Contact, Privacy, and Terms pages and link them in navigation.",
    },
    {
        "title": "Improve citation density",
        "priority": "medium",
        "category": "content_quality",
        "triggers": [
            "no citation or source reference patterns were found",
            "no citations found",
        ],
        "reason": "Citations support authority and AI extractability.",
        "action": "Add inline citations, references, and source links for key claims.",
    },
    {
        "title": "Add statistics and data points",
        "priority": "low",
        "category": "content_quality",
        "triggers": [
            "no statistical data points were found",
            "no statistics found",
        ],
        "reason": "Quantitative evidence improves trust and quotability.",
        "action": "Add relevant metrics, benchmarks, and data points with sources.",
    },
    {
        "title": "Increase AI-friendly structure",
        "priority": "medium",
        "category": "llm_readiness",
        "triggers": [
            "content does not open with a direct answer",
            "no key takeaway or summary blocks were found",
            "no definition patterns were found",
            "no short, factual declarative sentences suitable for direct quoting were found",
        ],
        "reason": "AI systems prioritize answer-first and well-chunked content.",
        "action": "Add answer-first intros, definitions, key takeaways, and quotable sentences.",
    },
    {
        "title": "Add FAQ content with schema",
        "priority": "low",
        "category": "llm_readiness",
        "triggers": [
            "faqpage json-ld schema was found containing",
            "no faqpage json-ld schema was found",
        ],
        "reason": "FAQ schema improves extractability for AI and search.",
        "action": "Add FAQ sections and FAQPage JSON-LD on key pages.",
    },
]


def generate_recommendations(report_text: str) -> list[Recommendation]:
    text = (report_text or "").lower()
    seen_titles: set[str] = set()
    results: list[Recommendation] = []

    for rule in _RULES:
        matched: list[str] = []
        for trigger in rule["triggers"]:
            if trigger.lower() in text:
                matched.append(trigger)
        if not matched:
            continue

        title = rule["title"]
        if title in seen_titles:
            continue
        seen_titles.add(title)

        results.append(
            Recommendation(
                title=title,
                priority=rule["priority"],
                category=rule["category"],
                reason=rule["reason"],
                action=rule["action"],
                evidence=matched,
            )
        )

    return results


def summarize_recommendations(recs: list[Recommendation]) -> dict:
    counts = {"high": 0, "medium": 0, "low": 0}
    for rec in recs:
        if rec.priority in counts:
            counts[rec.priority] += 1
    return {
        "total": len(recs),
        "by_priority": counts,
    }
