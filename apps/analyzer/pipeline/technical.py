import logging
import re

from .crawler import CrawlResult, check_file_exists, fetch_file_content
from .utils import safe_score

logger = logging.getLogger("apps")

AI_BOT_AGENTS = [
    "GPTBot", "Google-Extended", "anthropic-ai", "ClaudeBot",
    "PerplexityBot", "ChatGPT-User", "CCBot",
]


def _check_robots_allows_ai(robots_txt: str) -> tuple[bool, list[str]]:
    blocked = []
    if not robots_txt:
        return True, []

    lines = robots_txt.lower().splitlines()
    current_agent = None
    for line in lines:
        line = line.strip()
        if line.startswith("user-agent:"):
            current_agent = line.split(":", 1)[1].strip()
        elif line.startswith("disallow:") and current_agent:
            path = line.split(":", 1)[1].strip()
            if path == "/" or path == "/*":
                for bot in AI_BOT_AGENTS:
                    if current_agent == "*" or bot.lower() in current_agent:
                        blocked.append(bot)

    allows = len(blocked) == 0
    return allows, blocked


def score_technical(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Score technical GEO signals.
    Works even if crawl failed — file checks (llms.txt, robots.txt, sitemap)
    and URL-level checks (HTTPS) don't require page HTML.
    """
    has_html = crawl.ok
    soup = crawl.soup

    details = {"checks": {}, "findings": []}
    score = 0.0

    # ── Checks that work WITHOUT page HTML ────────────────────────────

    # llms.txt exists and has quality content (20 pts)
    llms_content = fetch_file_content(crawl.url, "llms.txt")
    has_llms_txt = bool(llms_content.strip())
    details["checks"]["llms_txt"] = has_llms_txt
    if has_llms_txt:
        llms_len = len(llms_content.strip())
        details["checks"]["llms_txt_length"] = llms_len
        # Quality check: has real content (not just a blank/minimal file)
        has_urls = "http" in llms_content.lower() or "/" in llms_content
        has_descriptions = llms_len > 200
        if has_descriptions and has_urls:
            score += 20  # Full score: exists + has URLs + substantial content
            details["checks"]["llms_txt_quality"] = "good"
        elif llms_len > 50:
            score += 15  # Exists with some content
            details["checks"]["llms_txt_quality"] = "basic"
        else:
            score += 8   # Exists but very minimal
            details["checks"]["llms_txt_quality"] = "minimal"
            details["findings"].append("llms_txt_minimal_content")
    else:
        details["findings"].append("no_llms_txt")

    # robots.txt allows AI bots (20 pts)
    robots_txt = fetch_file_content(crawl.url, "robots.txt")
    has_robots = bool(robots_txt.strip())
    details["checks"]["has_robots_txt"] = has_robots
    if has_robots:
        allows_ai, blocked_bots = _check_robots_allows_ai(robots_txt)
        details["checks"]["ai_bots_allowed"] = allows_ai
        details["checks"]["blocked_bots"] = blocked_bots
        if allows_ai:
            score += 20
        else:
            details["findings"].append("ai_bots_blocked")
    else:
        score += 20
        details["checks"]["ai_bots_allowed"] = True

    # sitemap.xml (10 pts)
    has_sitemap = check_file_exists(crawl.url, "sitemap.xml")
    details["checks"]["has_sitemap"] = has_sitemap
    if has_sitemap:
        score += 10
    else:
        details["findings"].append("no_sitemap")

    # HTTPS (5 pts)
    details["checks"]["is_https"] = crawl.is_https
    if crawl.is_https:
        score += 5
    else:
        details["findings"].append("no_https")

    # Load time (15 pts) — use whatever we got, even from a failed request
    if crawl.load_time > 0:
        details["checks"]["load_time"] = round(crawl.load_time, 2)
        if crawl.load_time < 1.5:
            score += 15
        elif crawl.load_time < 3.0:
            score += 10
        elif crawl.load_time < 5.0:
            score += 5
        else:
            details["findings"].append("slow_load_time")
    else:
        details["checks"]["load_time"] = None

    # ── Checks that REQUIRE page HTML ─────────────────────────────────

    if has_html and soup:
        # Meta robots ok (10 pts)
        meta_robots = soup.find("meta", attrs={"name": "robots"})
        robots_content = meta_robots.get("content", "").lower() if meta_robots else ""
        noindex = "noindex" in robots_content
        details["checks"]["meta_robots_ok"] = not noindex
        if not noindex:
            score += 10
        else:
            details["findings"].append("meta_noindex")

        # Viewport meta (10 pts)
        viewport = soup.find("meta", attrs={"name": "viewport"})
        details["checks"]["has_viewport"] = viewport is not None
        if viewport:
            score += 10
        else:
            details["findings"].append("no_viewport")

        # Canonical tag (10 pts)
        canonical = soup.find("link", attrs={"rel": "canonical"})
        details["checks"]["has_canonical"] = canonical is not None
        if canonical:
            score += 10
        else:
            details["findings"].append("no_canonical")

        # OG / Twitter Card metadata (10 pts) — affects AI scraping quality
        og_title = soup.find("meta", property="og:title")
        og_desc = soup.find("meta", property="og:description")
        og_image = soup.find("meta", property="og:image")
        twitter_card = soup.find("meta", attrs={"name": "twitter:card"})
        og_score = 0
        if og_title and og_title.get("content"):
            og_score += 3
        if og_desc and og_desc.get("content"):
            og_score += 3
        if og_image and og_image.get("content"):
            og_score += 2
        if twitter_card and twitter_card.get("content"):
            og_score += 2
        details["checks"]["og_metadata_score"] = og_score
        details["checks"]["has_og_title"] = bool(og_title and og_title.get("content"))
        details["checks"]["has_og_description"] = bool(og_desc and og_desc.get("content"))
        if og_score >= 6:
            score += 10
        elif og_score >= 3:
            score += 5
        elif og_score == 0:
            details["findings"].append("no_og_metadata")
        else:
            score += og_score
    else:
        details["checks"]["meta_robots_ok"] = None
        details["checks"]["has_viewport"] = None
        details["checks"]["has_canonical"] = None
        details["checks"]["crawl_blocked"] = True
        # Scale score: we checked 70pts worth, normalize to 100
        # so partial results are fairly represented
        if score > 0:
            score = score * (100 / 70)

    score = safe_score(score)
    details["score"] = score
    return score, details
