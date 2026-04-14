"""Best-effort public social metrics for Instagram / Facebook.

Meta often serves login walls or bot challenges; follower counts may be unavailable.
We discover profile URLs from web mention results and optional handle guesses.
"""

from __future__ import annotations

import logging
import math
import re
from urllib.parse import urlparse

import requests

logger = logging.getLogger("apps")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def _extract_profile_urls_from_mentions(web_details: dict) -> tuple[str | None, str | None]:
    ig: str | None = None
    fb: str | None = None
    mentions = web_details.get("mentions") or []
    if not isinstance(mentions, list):
        return None, None
    for m in mentions:
        if not isinstance(m, dict):
            continue
        url = str(m.get("url") or "").strip()
        if not url:
            continue
        low = url.lower()
        if "instagram.com/" in low and ig is None:
            ig = _normalize_instagram_url(url)
        if "facebook.com/" in low and fb is None:
            fb = _normalize_facebook_url(url)
        if ig and fb:
            break
    return ig, fb


def _slug_from_brand(brand_name: str, brand_url: str) -> str:
    if brand_name and brand_name.strip():
        s = re.sub(r"[^a-zA-Z0-9]+", "", brand_name.strip().lower())
        if s:
            return s[:64]
    try:
        host = (urlparse(brand_url).hostname or "").replace("www.", "").split(".")
        if host:
            return re.sub(r"[^a-z0-9]", "", host[0].lower())[:64]
    except Exception:
        pass
    return ""


def _guess_urls(brand_name: str, brand_url: str) -> tuple[str | None, str | None]:
    slug = _slug_from_brand(brand_name, brand_url)
    if not slug:
        return None, None
    return (
        f"https://www.instagram.com/{slug}/",
        f"https://www.facebook.com/{slug}",
    )


def _normalize_instagram_url(url: str) -> str:
    try:
        p = urlparse(url if "://" in url else "https://" + url)
        path = (p.path or "").strip("/").split("/")
        if not path or not path[0]:
            return url
        user = path[0]
        if user in ("p", "reel", "reels", "stories", "explore"):
            return url
        return f"https://www.instagram.com/{user}/"
    except Exception:
        return url


def _normalize_facebook_url(url: str) -> str:
    try:
        p = urlparse(url if "://" in url else "https://" + url)
        return f"{p.scheme}://{p.netloc}{p.path.split('?')[0]}"
    except Exception:
        return url


def _parse_instagram_followers(html: str) -> int | None:
    for pattern in (
        r'"edge_followed_by"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        r'"follower_count"\s*:\s*(\d+)',
        r'"edge_followed_by":\{"count":(\d+)\}',
    ):
        m = re.search(pattern, html)
        if m:
            return int(m.group(1))
    return None


def _parse_facebook_followers(html: str) -> int | None:
    for pattern in (
        r'"followers_count"\s*:\s*(\d+)',
        r'"follower_count"\s*:\s*(\d+)',
        r'(\d[\d,]*)\s+followers',
    ):
        m = re.search(pattern, html, re.I)
        if m:
            raw = m.group(1).replace(",", "")
            if raw.isdigit():
                return int(raw)
    return None


def _fetch_followers(session: requests.Session, url: str, platform: str) -> tuple[int | None, str | None]:
    if not url:
        return None, "no_url"
    try:
        r = session.get(url, timeout=12, allow_redirects=True)
        if r.status_code != 200:
            return None, f"http_{r.status_code}"
        html = r.text or ""
        if platform == "instagram":
            n = _parse_instagram_followers(html)
        else:
            n = _parse_facebook_followers(html)
        if n is None and ("login" in html.lower() or "Log in" in html):
            return None, "login_wall"
        return n, None if n is not None else "not_found_in_html"
    except Exception as exc:
        logger.debug("social fetch %s: %s", url, exc)
        return None, "fetch_error"


def _platform_slice(
    url: str | None,
    followers: int | None,
    from_mention: bool,
) -> float:
    """Score one platform without inflating purely guessed profile URLs."""
    if not url:
        return 0.0
    n = followers if followers and followers > 0 else None
    if n:
        return 28 + min(22, 22 * math.log10(n + 1) / math.log10(100_001))
    if from_mention:
        return 12.0
    return 5.0


def _score_presence(
    ig_url: str | None,
    fb_url: str | None,
    ig_n: int | None,
    fb_n: int | None,
    ig_from_mention: bool,
    fb_from_mention: bool,
) -> float:
    total = _platform_slice(ig_url, ig_n, ig_from_mention) + _platform_slice(fb_url, fb_n, fb_from_mention)
    return round(min(100, total), 1)


def _score_market_capture(ig_n: int | None, fb_n: int | None) -> float:
    total = (ig_n or 0) + (fb_n or 0)
    if total <= 0:
        return 0.0
    # Saturating log scale: ~1M combined followers approaches max score
    cap = 100 * math.log10(total + 1) / math.log10(1_000_001)
    return round(min(100, max(0, cap)), 1)


def run_social_presence(
    brand_name: str,
    brand_url: str,
    web_mentions_details: dict,
) -> dict:
    ig_mention_url, fb_mention_url = _extract_profile_urls_from_mentions(web_mentions_details)
    ig_url = ig_mention_url
    fb_url = fb_mention_url
    guess_ig, guess_fb = _guess_urls(brand_name, brand_url)
    if ig_url is None and guess_ig:
        ig_url = guess_ig
    if fb_url is None and guess_fb:
        fb_url = guess_fb

    session = _session()
    ig_followers, ig_err = _fetch_followers(session, ig_url or "", "instagram")
    fb_followers, fb_err = _fetch_followers(session, fb_url or "", "facebook")

    ig_from_mention = bool(ig_mention_url)
    fb_from_mention = bool(fb_mention_url)
    brand_presence = _score_presence(
        ig_url, fb_url, ig_followers, fb_followers, ig_from_mention, fb_from_mention
    )
    market_capture = _score_market_capture(ig_followers, fb_followers)

    return {
        "instagram": {
            "url": ig_url,
            "followers": ig_followers,
            "error": ig_err,
            "from_guess": bool(ig_url and not ig_from_mention),
        },
        "facebook": {
            "url": fb_url,
            "followers": fb_followers,
            "error": fb_err,
            "from_guess": bool(fb_url and not fb_from_mention),
        },
        "brand_presence_score": brand_presence,
        "market_capture_score": market_capture,
        "method": "public_html",
        "interpretation": (
            "Guessed profile URLs without readable follower counts score low; "
            "mention-backed URLs score higher; market capture needs follower numbers."
        ),
    }
