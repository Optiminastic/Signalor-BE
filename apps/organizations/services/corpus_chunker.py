"""HTML -> knowledge-base chunks (Epic 3, Knowledge Ingestion).

Pure and deterministic: given a page's HTML, produce heading-scoped, size-bounded
``ChunkDraft``s ready to embed and store. No DB, no network, no side effects -
which keeps the segmentation logic trivially unit-testable.

Two stages, each small and independently testable:
  1. ``_sections`` - clean the HTML and split it into (heading_path, text) sections
     by walking ``h1``-``h3`` in document order.
  2. ``_windows`` - slice a section's text into overlapping, char-bounded windows.
"""

import hashlib
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup
from django.conf import settings

# Char proxy for ~500-800 tokens. Tunable without a migration (affects new chunks only).
CHUNK_MAX_CHARS = int(getattr(settings, "CORPUS_CHUNK_MAX_CHARS", 2800))
# Small carry-over so a fact split across a window boundary survives in both.
CHUNK_OVERLAP_CHARS = int(getattr(settings, "CORPUS_CHUNK_OVERLAP_CHARS", 200))
# Drop trivially small fragments (nav crumbs, stray labels) - not worth embedding.
CHUNK_MIN_CHARS = int(getattr(settings, "CORPUS_CHUNK_MIN_CHARS", 40))

# Structural noise stripped before text extraction.
_DROP_TAGS = ("script", "style", "nav", "footer", "header", "aside", "noscript", "form", "svg")
_HEADING_TAGS = ("h1", "h2", "h3")
_BLOCK_TAGS = ("p", "li", "td", "th", "blockquote", "pre", "figcaption", "dd", "dt")
_WS_RE = re.compile(r"\s+")


@dataclass
class ChunkDraft:
    text: str
    heading_path: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    content_hash: str = ""


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _page_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return _normalize(soup.title.string)[:200]
    h1 = soup.find("h1")
    return _normalize(h1.get_text(" "))[:200] if h1 else ""


def _sections(html: str) -> list[tuple[list[str], str]]:
    """Clean HTML and split into (heading_path, text) sections by h1-h3."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()
    root = soup.body or soup

    sections: list[tuple[list[str], str]] = []
    stack: list[tuple[int, str]] = []  # (heading level, text)
    buf: list[str] = []

    def flush() -> None:
        text = _normalize(" ".join(buf))
        if text:
            sections.append(([t for _, t in stack], text))
        buf.clear()

    for el in root.find_all([*_HEADING_TAGS, *_BLOCK_TAGS]):
        if el.name in _HEADING_TAGS:
            flush()
            level = int(el.name[1])
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, _normalize(el.get_text(" "))))
        else:
            text = _normalize(el.get_text(" "))
            if text:
                buf.append(text)
    flush()
    return sections


def _windows(text: str) -> list[str]:
    """Slice ``text`` into <=CHUNK_MAX_CHARS windows with CHUNK_OVERLAP_CHARS overlap."""
    if len(text) <= CHUNK_MAX_CHARS:
        return [text]
    step = max(1, CHUNK_MAX_CHARS - CHUNK_OVERLAP_CHARS)
    return [text[start : start + CHUNK_MAX_CHARS] for start in range(0, len(text), step)]


def chunk_page(html: str, *, url: str) -> list[ChunkDraft]:
    """Turn a page's HTML into heading-scoped, size-bounded chunk drafts."""
    soup = BeautifulSoup(html or "", "html.parser")
    page_title = _page_title(soup)

    drafts: list[ChunkDraft] = []
    position = 0
    for heading_path, section_text in _sections(html):
        for window in _windows(section_text):
            body = window.strip()
            if len(body) < CHUNK_MIN_CHARS:
                continue
            drafts.append(
                ChunkDraft(
                    text=body,
                    heading_path=heading_path,
                    metadata={
                        "page_title": page_title,
                        "source_url": url,
                        "position": position,
                        "char_count": len(body),
                    },
                    content_hash=_hash(body),
                )
            )
            position += 1
    return drafts
