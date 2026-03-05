import re
import logging
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger("apps")


def extract_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def count_words(text: str) -> int:
    return len(text.split())


def extract_internal_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    parsed_base = urlparse(base_url)
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.netloc == parsed_base.netloc and parsed.scheme in ("http", "https"):
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            links.add(clean.rstrip("/"))
    return list(links)


def extract_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def extract_brand_name(soup: BeautifulSoup, url: str) -> str:
    og_site = soup.find("meta", property="og:site_name")
    if og_site and og_site.get("content"):
        name = og_site["content"].strip()
        if len(name) <= 40:
            return name

    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        parts = re.split(r"[|\-–—:]", title_tag.string)
        # Try each part — prefer short ones that look like brand names
        candidates = [p.strip() for p in parts if p.strip()]
        # If multiple parts, pick the shortest (likely the brand, not tagline)
        if len(candidates) > 1:
            short = min(candidates, key=len)
            if len(short) <= 30:
                return short
        # Single part — only use if it's short enough to be a brand name
        if candidates and len(candidates[0]) <= 30:
            return candidates[0]

    # Fallback: pick the most meaningful domain part (skip generic TLDs and common subdomains)
    _SKIP = {"www", "com", "net", "org", "io", "co", "uk", "de", "fr", "app", "ai", "dev", "ui", "api", "get", "try"}
    domain = extract_domain(url)
    parts = [p for p in domain.split(".") if len(p) >= 3 and p.lower() not in _SKIP]
    if parts:
        # Prefer longest (most specific/unique) part — e.g. "aceternity" from "ui.aceternity.com"
        return max(parts, key=len).capitalize()
    return domain.split(".")[0].capitalize()


def safe_score(value: float, max_val: float = 100.0) -> float:
    return max(0.0, min(float(value), max_val))
