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

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and self.soup is not None


def crawl_page(url: str) -> CrawlResult:
    result = CrawlResult(url=url)
    parsed = urlparse(url)
    result.is_https = parsed.scheme == "https"

    last_error = ""

    for attempt in range(MAX_RETRIES + 1):
        ua = USER_AGENTS[attempt % len(USER_AGENTS)]
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            # Avoid brotli payloads in environments without brotli decoder support.
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        try:
            start = time.time()
            resp = requests.get(
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


def check_file_exists(base_url: str, path: str) -> bool:
    parsed = urlparse(base_url)
    url = f"{parsed.scheme}://{parsed.netloc}/{path.lstrip('/')}"
    try:
        resp = requests.head(
            url,
            headers={"User-Agent": USER_AGENTS[0]},
            timeout=5,
            allow_redirects=True,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def fetch_file_content(base_url: str, path: str) -> str:
    parsed = urlparse(base_url)
    url = f"{parsed.scheme}://{parsed.netloc}/{path.lstrip('/')}"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENTS[0]},
            timeout=5,
            allow_redirects=True,
        )
        if resp.status_code == 200:
            return resp.text
    except requests.RequestException:
        pass
    return ""
