import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .utils import extract_internal_links, extract_text

logger = logging.getLogger("apps")

# Rotate user agents to reduce blocking
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

TIMEOUT = 15
MAX_RETRIES = 2


@dataclass
class CrawlResult:
    url: str
    status_code: int = 0
    html: str = ""
    soup: BeautifulSoup | None = None
    text: str = ""
    internal_links: list[str] = field(default_factory=list)
    load_time: float = 0.0
    error: str = ""
    is_https: bool = False
    session: requests.Session | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and self.soup is not None


def crawl_page(url: str, storefront_password: str = "") -> CrawlResult:
    result = CrawlResult(url=url)
    parsed = urlparse(url)
    result.is_https = parsed.scheme == "https"

    # If storefront password provided, authenticate first (Shopify dev stores)
    session = requests.Session()
    if storefront_password:
        try:
            password_url = f"{parsed.scheme}://{parsed.netloc}/password"
            session.post(password_url, data={"password": storefront_password}, timeout=10)
        except Exception:
            pass  # Continue even if password auth fails

    result.session = session
    last_error = ""

    for attempt in range(MAX_RETRIES + 1):
        ua = USER_AGENTS[attempt % len(USER_AGENTS)]
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        try:
            start = time.time()
            resp = session.get(
                url,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            result.load_time = time.time() - start
            result.status_code = resp.status_code

            if resp.status_code == 200:
                html = resp.text or ""
                # If server declares/guesses a bad encoding, force a safer fallback.
                if html and html.count("\ufffd") > 50:
                    resp.encoding = resp.apparent_encoding or "utf-8"
                    html = resp.text or ""

                # Detect Shopify password-protected stores (returns 200 with login form)
                if ("shopify" in html.lower() and "password" in html.lower()
                        and ('id="password"' in html or "storefront-password" in html)):
                    result.error = "This Shopify store is password-protected. Remove the storefront password in Shopify Admin -> Online Store -> Preferences to analyze it."
                    return result

                # Detect WordPress maintenance mode
                if "maintenance mode" in html.lower() and len(html) < 5000:
                    result.error = "This site is in maintenance mode. Disable maintenance mode to analyze it."
                    return result

                result.html = html
                result.soup = BeautifulSoup(html, "html.parser")
                text_soup = BeautifulSoup(html, "html.parser")
                result.text = extract_text(text_soup)
                result.internal_links = extract_internal_links(result.soup, url)
                return result

            # Retry on server errors (5xx) or rate limiting (429)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {resp.status_code}"
                logger.info("Crawl attempt %d/%d got %d for %s, retrying...",
                            attempt + 1, MAX_RETRIES + 1, resp.status_code, url)
                time.sleep(2 * (attempt + 1))  # Backoff: 2s, 4s
                continue

            # Non-retryable HTTP error — set human-readable message
            error_messages = {
                401: "This site is password-protected. Remove the password or whitelist our crawler to analyze it.",
                403: "Access forbidden (403). The site is blocking our crawler. Check firewall or security plugin settings.",
                404: "Page not found (404). The URL may be incorrect or the page was deleted.",
                410: "Page permanently removed (410). This URL no longer exists.",
                451: "Page unavailable for legal reasons (451).",
            }
            result.error = error_messages.get(resp.status_code, f"HTTP {resp.status_code}")
            return result

        except requests.Timeout:
            last_error = "Request timed out"
            logger.info("Crawl attempt %d/%d timed out for %s", attempt + 1, MAX_RETRIES + 1, url)
            time.sleep(1)
            continue
        except requests.ConnectionError as exc:
            last_error = f"Connection error: {exc}"
            logger.info("Crawl attempt %d/%d connection error for %s", attempt + 1, MAX_RETRIES + 1, url)
            time.sleep(1)
            continue
        except requests.exceptions.SSLError as exc:
            last_error = f"SSL certificate error: The site's SSL certificate is invalid or expired. Fix your SSL certificate to analyze this site."
            logger.warning("SSL error for %s: %s", url, exc)
            break
        except requests.RequestException as exc:
            err_str = str(exc).lower()
            if "name resolution" in err_str or "nodename" in err_str or "getaddrinfo" in err_str:
                last_error = "Domain not found. Check if the URL is correct and the domain exists."
            elif "connection refused" in err_str:
                last_error = "Connection refused. The server is not accepting connections."
            else:
                last_error = f"Could not reach the site: {exc}"
            logger.warning("Crawl error for %s: %s", url, exc)
            break

    # All retries exhausted
    result.error = last_error
    return result


def check_file_exists(base_url: str, path: str, session: requests.Session | None = None) -> bool:
    parsed = urlparse(base_url)
    url = f"{parsed.scheme}://{parsed.netloc}/{path.lstrip('/')}"
    http = session or requests
    try:
        resp = http.head(
            url,
            headers={"User-Agent": USER_AGENTS[0]},
            timeout=5,
            allow_redirects=True,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def fetch_file_content(base_url: str, path: str, session: requests.Session | None = None) -> str:
    parsed = urlparse(base_url)
    url = f"{parsed.scheme}://{parsed.netloc}/{path.lstrip('/')}"
    http = session or requests
    try:
        resp = http.get(
            url,
            headers={"User-Agent": USER_AGENTS[0]},
            timeout=5,
            allow_redirects=True,
        )
        if resp.status_code == 200:
            text = resp.text
            # Guard: if the response is an HTML password page, it's not the file
            if "storefront-password" in text or ('id="password"' in text and "password protected" in text.lower()):
                return ""
            return text
    except requests.RequestException:
        pass
    return ""


# ── Multi-page site discovery ─────────────────────────────────────────────

@dataclass
class SiteMap:
    """Discovered pages from a site, categorized by type."""
    homepage: str = ""
    products: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)       # static pages (about, contact, etc.)
    blog_posts: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    @property
    def all_urls(self) -> list[str]:
        urls = []
        if self.homepage:
            urls.append(self.homepage)
        urls.extend(self.products)
        urls.extend(self.collections)
        urls.extend(self.pages)
        urls.extend(self.blog_posts)
        urls.extend(self.other)
        return urls

    @property
    def total(self) -> int:
        return len(self.all_urls)


def discover_site_pages(
    base_url: str,
    session: requests.Session | None = None,
    max_products: int = 5,
    max_collections: int = 3,
    max_pages: int = 5,
    max_blog: int = 3,
) -> SiteMap:
    """
    Discover important pages on a site using sitemap + internal links.
    No hardcoding — detects page types from URL patterns.

    Strategy:
    1. Fetch sitemap.xml → extract all URLs
    2. Categorize URLs by pattern (products, collections, pages, blog)
    3. Cap each category to avoid over-crawling
    4. If no sitemap → discover from homepage internal links
    """
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    site_map = SiteMap(homepage=base_url)
    http = session or requests.Session()

    all_discovered: set[str] = set()

    # ── Try sitemap first ──
    sitemap_urls = _parse_sitemap(origin, http)
    if sitemap_urls:
        all_discovered.update(sitemap_urls)
        logger.info("Sitemap discovery: %d URLs found", len(sitemap_urls))
    else:
        # Fallback: discover from homepage internal links
        homepage_links = _discover_from_page(base_url, http)
        all_discovered.update(homepage_links)
        logger.info("Link discovery (no sitemap): %d URLs found", len(homepage_links))

    # ── Categorize URLs ──
    for url in all_discovered:
        path = urlparse(url).path.lower().strip("/")

        if not path or path == "":
            continue  # Skip homepage (already added)

        if "/products/" in path or path.startswith("products/"):
            site_map.products.append(url)
        elif "/collections/" in path or path.startswith("collections/"):
            site_map.collections.append(url)
        elif "/blogs/" in path or "/blog/" in path or path.startswith("blog/"):
            site_map.blog_posts.append(url)
        elif "/pages/" in path or path.startswith("pages/"):
            site_map.pages.append(url)
        elif any(kw in path for kw in ["about", "contact", "shipping", "faq", "privacy", "terms", "policy", "team", "story"]):
            site_map.pages.append(url)
        elif any(kw in path for kw in ["category", "categories", "shop", "store"]):
            site_map.collections.append(url)
        elif path.count("/") == 0:
            # Top-level pages (e.g., /pricing, /features)
            site_map.pages.append(url)
        else:
            site_map.other.append(url)

    # ── Cap each category ──
    site_map.products = site_map.products[:max_products]
    site_map.collections = site_map.collections[:max_collections]
    site_map.pages = site_map.pages[:max_pages]
    site_map.blog_posts = site_map.blog_posts[:max_blog]
    site_map.other = site_map.other[:3]

    logger.info(
        "Site discovery complete: %d products, %d collections, %d pages, %d blog, %d other",
        len(site_map.products), len(site_map.collections),
        len(site_map.pages), len(site_map.blog_posts), len(site_map.other),
    )

    return site_map


def _parse_sitemap(origin: str, http: requests.Session) -> list[str]:
    """Parse sitemap.xml (handles sitemap index + nested sitemaps)."""
    import xml.etree.ElementTree as ET

    urls: list[str] = []

    def _fetch_and_parse(url: str, depth: int = 0):
        if depth > 2:  # Max nesting depth
            return
        try:
            resp = http.get(url, headers={"User-Agent": USER_AGENTS[0]}, timeout=10)
            if resp.status_code != 200:
                return
            text = resp.text
            # Guard against password pages
            if "storefront-password" in text or "password protected" in text.lower():
                return

            root = ET.fromstring(text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

            # Check if it's a sitemap index (contains <sitemap> entries)
            for sitemap in root.findall(".//sm:sitemap/sm:loc", ns):
                if sitemap.text:
                    _fetch_and_parse(sitemap.text.strip(), depth + 1)

            # Extract page URLs
            for loc in root.findall(".//sm:url/sm:loc", ns):
                if loc.text:
                    page_url = loc.text.strip()
                    if page_url.startswith("http"):
                        urls.append(page_url)
        except Exception as exc:
            logger.debug("Sitemap parse error for %s: %s", url, exc)

    _fetch_and_parse(f"{origin}/sitemap.xml")
    return urls


def _discover_from_page(url: str, http: requests.Session) -> list[str]:
    """Fallback: discover internal links from a page's HTML."""
    try:
        resp = http.get(url, headers={"User-Agent": USER_AGENTS[0]}, timeout=15)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        parsed = urlparse(url)
        base_domain = parsed.netloc

        discovered = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/") and not href.startswith("//"):
                full = f"{parsed.scheme}://{parsed.netloc}{href.split('?')[0].split('#')[0]}"
                discovered.add(full)
            elif base_domain in href:
                clean = href.split("?")[0].split("#")[0]
                discovered.add(clean)

        return list(discovered)
    except Exception:
        return []


def crawl_site(
    base_url: str,
    storefront_password: str = "",
    max_pages: int = 15,
) -> tuple[CrawlResult, SiteMap, list[CrawlResult]]:
    """
    Crawl a full site: homepage + discovered pages.

    Returns:
    - homepage CrawlResult (primary, used for scoring)
    - SiteMap (discovered pages)
    - list of CrawlResult for additional pages
    """
    # Crawl homepage first
    homepage_result = crawl_page(base_url, storefront_password)

    # Discover site pages
    site_map = discover_site_pages(
        base_url,
        session=homepage_result.session,
        max_products=5,
        max_collections=3,
        max_pages=5,
        max_blog=3,
    )

    # Crawl additional pages (skip homepage, already crawled)
    additional: list[CrawlResult] = []
    urls_to_crawl = [u for u in site_map.all_urls if u != base_url][:max_pages]

    for url in urls_to_crawl:
        try:
            result = crawl_page(url, storefront_password="")  # Session already authenticated
            result.session = homepage_result.session  # Share the session
            additional.append(result)
        except Exception as exc:
            logger.warning("Failed to crawl %s: %s", url, exc)

    logger.info("Site crawl complete: homepage + %d additional pages", len(additional))

    return homepage_result, site_map, additional
