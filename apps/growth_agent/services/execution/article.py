"""
Structured article format and pure renderers (HTML for CMSs, Markdown for repos).

The LLM never returns raw HTML: it returns these blocks, and every string is
escaped on render, so model output cannot inject markup or scripts into the
brand's site. The FAQ also becomes FAQPage JSON-LD, which AI engines read.
"""

from __future__ import annotations

import html
import json
import re

from pydantic import BaseModel, Field

SLUG_MAX_LENGTH = 80
_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#|])")


class Section(BaseModel):
    heading: str
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class FaqItem(BaseModel):
    question: str
    answer: str


class ArticleDraft(BaseModel):
    title: str = Field(description="Clear title that matches how buyers ask the question")
    meta_description: str = Field(description="Under 160 characters")
    answer: str = Field(description="2 to 3 sentence direct answer to the question, first thing on the page")
    sections: list[Section] = Field(default_factory=list)
    faq: list[FaqItem] = Field(default_factory=list)


def slugify(text: str) -> str:
    slug = _SLUG_UNSAFE.sub("-", text.lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH].rstrip("-") or "article"


def word_count(article: ArticleDraft) -> int:
    parts = [article.answer]
    for section in article.sections:
        parts += [section.heading, *section.paragraphs, *section.bullets]
    for item in article.faq:
        parts += [item.question, item.answer]
    return sum(len(p.split()) for p in parts)


def faq_jsonld(article: ArticleDraft) -> dict | None:
    if not article.faq:
        return None
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q.question, "acceptedAnswer": {"@type": "Answer", "text": q.answer}}
            for q in article.faq
        ],
    }


def _jsonld_script(article: ArticleDraft) -> str:
    data = faq_jsonld(article)
    if data is None:
        return ""
    # "</" is escaped so the JSON can never close the script tag early.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def render_html(article: ArticleDraft) -> str:
    e = html.escape
    out = [f"<p><strong>{e(article.answer)}</strong></p>"]
    for section in article.sections:
        out.append(f"<h2>{e(section.heading)}</h2>")
        out += [f"<p>{e(p)}</p>" for p in section.paragraphs]
        if section.bullets:
            out.append("<ul>" + "".join(f"<li>{e(b)}</li>" for b in section.bullets) + "</ul>")
    if article.faq:
        out.append("<h2>Frequently asked questions</h2>")
        for item in article.faq:
            out += [f"<h3>{e(item.question)}</h3>", f"<p>{e(item.answer)}</p>"]
    script = _jsonld_script(article)
    return "\n".join(out + ([script] if script else []))


def _md(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def render_markdown(article: ArticleDraft, frontmatter: dict[str, str]) -> str:
    """Markdown with YAML frontmatter, for Next.js/MDX content folders."""
    lines: list[str] = []
    if frontmatter:
        lines = [
            "---",
            *(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in frontmatter.items()),
            "---",
            "",
        ]
    lines += [f"**{_md(article.answer)}**", ""]
    for section in article.sections:
        lines += [f"## {_md(section.heading)}", ""]
        for paragraph in section.paragraphs:
            lines += [_md(paragraph), ""]
        if section.bullets:
            lines += [f"- {_md(b)}" for b in section.bullets] + [""]
    if article.faq:
        lines += ["## Frequently asked questions", ""]
        for item in article.faq:
            lines += [f"### {_md(item.question)}", "", _md(item.answer), ""]
    return "\n".join(lines).rstrip() + "\n"
