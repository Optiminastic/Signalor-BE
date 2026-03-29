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

    # llms.txt exists and has quality content (15 pts)
    # Check /llms.txt (WordPress/static + Shopify redirect), then app proxy path
    llms_content = fetch_file_content(crawl.url, "llms.txt")
    if not llms_content.strip():
        llms_content = fetch_file_content(crawl.url, "apps/signalor/llms.txt")
    has_llms_txt = bool(llms_content.strip())
    details["checks"]["llms_txt"] = has_llms_txt
    if has_llms_txt:
        llms_len = len(llms_content.strip())
        details["checks"]["llms_txt_length"] = llms_len
        has_urls = "http" in llms_content.lower() or "/" in llms_content
        has_descriptions = llms_len > 200
        if has_descriptions and has_urls:
            score += 15
            details["checks"]["llms_txt_quality"] = "good"
        elif llms_len > 50:
            score += 11
            details["checks"]["llms_txt_quality"] = "basic"
        else:
            score += 6
            details["checks"]["llms_txt_quality"] = "minimal"
            details["findings"].append("llms_txt_minimal_content")
        # llms.txt depth bonus (5 pts) — reward detailed, well-structured files
        if has_llms_txt and llms_len > 200:
            sections = sum(1 for line in llms_content.splitlines() if line.strip().startswith('#'))
            urls_count = llms_content.lower().count('http')
            if sections >= 3 and urls_count >= 3:
                score += 5
                details["checks"]["llms_txt_depth_bonus"] = True
            else:
                details["checks"]["llms_txt_depth_bonus"] = False
    else:
        details["findings"].append("no_llms_txt")

    # robots.txt allows AI bots (10 pts — intentional config is rewarded more than default)
    robots_txt = fetch_file_content(crawl.url, "robots.txt")
    has_robots = bool(robots_txt.strip())
    details["checks"]["has_robots_txt"] = has_robots
    if has_robots:
        allows_ai, blocked_bots = _check_robots_allows_ai(robots_txt)
        details["checks"]["ai_bots_allowed"] = allows_ai
        details["checks"]["blocked_bots"] = blocked_bots
        if allows_ai:
            score += 10  # Intentional robots.txt that allows AI = full credit
        else:
            details["findings"].append("ai_bots_blocked")
        # Explicit AI bot rules bonus (5 pts) — reward intentional AI crawler directives
        robots_lower = robots_txt.lower()
        explicit_ai_rules = sum(1 for bot in AI_BOT_AGENTS if bot.lower() in robots_lower)
        details["checks"]["explicit_ai_bot_rules"] = explicit_ai_rules
        if explicit_ai_rules >= 2:
            score += 5
            details["checks"]["ai_rules_bonus"] = True
    else:
        score += 5  # No robots.txt = default allow, but not an achievement
        details["checks"]["ai_bots_allowed"] = True

    # sitemap.xml (10 pts)
    has_sitemap = check_file_exists(crawl.url, "sitemap.xml")
    details["checks"]["has_sitemap"] = has_sitemap
    if has_sitemap:
        score += 10
    else:
        details["findings"].append("no_sitemap")

    # HTTPS (5 pts — critical infrastructure baseline)
    details["checks"]["is_https"] = crawl.is_https
    if crawl.is_https:
        score += 5
    else:
        details["findings"].append("no_https")

    # Load time (12 pts — reduced, CDN-hosted sites shouldn't get max easily)
    if crawl.load_time > 0:
        details["checks"]["load_time"] = round(crawl.load_time, 2)
        if crawl.load_time < 1.0:
            score += 12
        elif crawl.load_time < 2.0:
            score += 8
        elif crawl.load_time < 3.0:
            score += 5
        elif crawl.load_time < 5.0:
            score += 2
        else:
            details["findings"].append("slow_load_time")
    else:
        details["checks"]["load_time"] = None

    # ── Checks that REQUIRE page HTML ─────────────────────────────────

    if has_html and soup:
        # Meta robots ok (5 pts — absence of noindex isn't an achievement)
        meta_robots = soup.find("meta", attrs={"name": "robots"})
        robots_content = meta_robots.get("content", "").lower() if meta_robots else ""
        noindex = "noindex" in robots_content
        details["checks"]["meta_robots_ok"] = not noindex
        if not noindex:
            score += 5
        else:
            details["findings"].append("meta_noindex")

        # Viewport meta (2 pts — every platform auto-adds this)
        viewport = soup.find("meta", attrs={"name": "viewport"})
        details["checks"]["has_viewport"] = viewport is not None
        if viewport:
            score += 2
        else:
            details["findings"].append("no_viewport")

        # Canonical tag (5 pts — platforms auto-add, but still useful)
        canonical = soup.find("link", attrs={"rel": "canonical"})
        details["checks"]["has_canonical"] = canonical is not None
        if canonical:
            score += 5
        else:
            details["findings"].append("no_canonical")

        # OG / Twitter Card metadata (8 pts — requires actual content)
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
            score += 8
        elif og_score >= 3:
            score += 4
        elif og_score == 0:
            details["findings"].append("no_og_metadata")
        else:
            score += og_score

        # Internal linking depth (10 pts — measures real site structure)
        all_links = soup.find_all("a", href=True)
        from urllib.parse import urlparse
        site_domain = urlparse(crawl.url).netloc
        internal_links = set()
        for a in all_links:
            href = a.get("href", "")
            if href.startswith("/") or site_domain in href:
                path = urlparse(href).path.strip("/")
                if path and path not in ("", "#"):
                    internal_links.add(path)
        details["checks"]["internal_link_count"] = len(internal_links)
        if len(internal_links) >= 10:
            score += 10
        elif len(internal_links) >= 5:
            score += 6
        elif len(internal_links) >= 3:
            score += 3
        else:
            details["findings"].append("few_internal_links_technical")

        # AI-specific meta tags (5 pts) — reward explicit AI crawler directives in HTML
        ai_meta_score = 0
        # Check for bot-specific meta tags
        ai_bot_metas_found = 0
        for bot_name in ["googlebot", "bingbot", "gptbot", "anthropic-ai", "perplexitybot"]:
            bot_meta = soup.find("meta", attrs={"name": re.compile(bot_name, re.I)})
            if bot_meta:
                ai_bot_metas_found += 1
        if ai_bot_metas_found >= 2:
            ai_meta_score = 5  # Full credit for 2+ bot-specific metas
        elif ai_bot_metas_found == 1:
            ai_meta_score = 3
        details["checks"]["ai_meta_tags_score"] = ai_meta_score
        score += ai_meta_score

        # Structured data validation (5 pts) — reward valid JSON-LD on page
        import json as _json
        scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
        valid_schemas = 0
        for script in scripts:
            try:
                data = _json.loads(script.string or "")
                if isinstance(data, dict) and "@context" in data:
                    valid_schemas += 1
                elif isinstance(data, list) and len(data) > 0:
                    valid_schemas += 1
            except (ValueError, TypeError):
                pass
        details["checks"]["valid_schema_count"] = valid_schemas
        if valid_schemas >= 2:
            score += 5
        elif valid_schemas >= 1:
            score += 3

    else:
        details["checks"]["meta_robots_ok"] = None
        details["checks"]["has_viewport"] = None
        details["checks"]["has_canonical"] = None
        details["checks"]["crawl_blocked"] = True
        details["findings"].append("crawl_failed")
        # Crawl failed — page is inaccessible, cap score severely
        # File-level checks (llms.txt, robots, sitemap) still count
        # but max 30/100 since the page itself can't be analyzed
        score = min(score, 30)

    score = safe_score(score)
    details["score"] = score
    return score, details
