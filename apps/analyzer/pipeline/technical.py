"""
Technical Scorer v3 — Measures real AI readiness, not just checklist.

Score = (Infrastructure × 0.25) + (Performance × 0.25) +
        (Crawlability × 0.20) + (AI Readability × 0.20) +
        (Structure Quality × 0.10)
"""
import json
import logging
import re
from urllib.parse import urlparse

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
    return len(blocked) == 0, blocked


# ── 1. Infrastructure (25 pts) ────────────────────────────────────────────

def _score_infrastructure(crawl: CrawlResult) -> tuple[float, dict]:
    """HTTPS, robots.txt, sitemap, llms.txt — the foundation."""
    details = {}
    score = 0.0

    # HTTPS (3 pts)
    details["is_https"] = crawl.is_https
    if crawl.is_https:
        score += 3

    # robots.txt + AI bot rules (5 pts)
    robots_txt = fetch_file_content(crawl.url, "robots.txt", session=crawl.session)
    has_robots = bool(robots_txt.strip())
    details["has_robots_txt"] = has_robots
    if has_robots:
        allows_ai, blocked = _check_robots_allows_ai(robots_txt)
        details["ai_bots_allowed"] = allows_ai
        details["blocked_bots"] = blocked
        if allows_ai:
            score += 3
        # Explicit AI bot rules bonus
        robots_lower = robots_txt.lower()
        explicit_rules = sum(1 for bot in AI_BOT_AGENTS if bot.lower() in robots_lower)
        details["explicit_ai_rules"] = explicit_rules
        if explicit_rules >= 2:
            score += 2
    else:
        score += 2  # No robots = default allow
        details["ai_bots_allowed"] = True

    # sitemap.xml (4 pts)
    has_sitemap = check_file_exists(crawl.url, "sitemap.xml", session=crawl.session)
    details["has_sitemap"] = has_sitemap
    if has_sitemap:
        score += 4
    else:
        details["_finding_sitemap"] = "no_sitemap"

    # llms.txt (8 pts)
    llms_content = fetch_file_content(crawl.url, "llms.txt", session=crawl.session)
    if not llms_content.strip():
        llms_content = fetch_file_content(crawl.url, "apps/signalor/llms.txt", session=crawl.session)
    has_llms = bool(llms_content.strip())
    details["has_llms_txt"] = has_llms

    if has_llms:
        llms_len = len(llms_content.strip())
        details["llms_txt_length"] = llms_len
        sections = sum(1 for line in llms_content.splitlines() if line.strip().startswith('#'))
        urls = llms_content.lower().count('http')

        if llms_len > 200 and sections >= 3 and urls >= 3:
            score += 8
            details["llms_txt_quality"] = "excellent"
        elif llms_len > 200:
            score += 6
            details["llms_txt_quality"] = "good"
        elif llms_len > 50:
            score += 4
            details["llms_txt_quality"] = "basic"
        else:
            score += 2
            details["llms_txt_quality"] = "minimal"
    else:
        details["_finding_llms"] = "no_llms_txt"

    # AI meta tags in HTML (3 pts)
    if crawl.soup:
        ai_metas = 0
        for bot in ["gptbot", "anthropic-ai", "perplexitybot", "claudebot"]:
            if crawl.soup.find("meta", attrs={"name": re.compile(bot, re.I)}):
                ai_metas += 1
        details["ai_meta_tags"] = ai_metas
        if ai_metas >= 2:
            score += 3
        elif ai_metas >= 1:
            score += 1

    return min(score, 25), details


# ── 2. Performance (25 pts) ───────────────────────────────────────────────

def _score_performance(crawl: CrawlResult) -> tuple[float, dict]:
    """Real load time + page weight metrics."""
    details = {}
    score = 0.0

    # 2a. Load time (15 pts) — actual measured time
    if crawl.load_time > 0:
        load_time = round(crawl.load_time, 2)
        details["load_time_seconds"] = load_time

        if load_time < 0.5:
            score += 15
        elif load_time < 1.0:
            score += 12
        elif load_time < 2.0:
            score += 8
        elif load_time < 3.0:
            score += 5
        elif load_time < 5.0:
            score += 2
        else:
            details["_finding_speed"] = "slow_load_time"
    else:
        details["load_time_seconds"] = None
        score += 5  # Unknown — neutral

    # 2b. Page weight (5 pts) — smaller = better for AI crawlers
    html_size = len(crawl.html) if crawl.html else 0
    details["html_size_kb"] = round(html_size / 1024, 1)

    if html_size < 50_000:       # <50KB
        score += 5
    elif html_size < 150_000:    # <150KB
        score += 3
    elif html_size < 500_000:    # <500KB
        score += 1
    # >500KB = bloated, no points

    # 2c. Resource efficiency (5 pts) — fewer external scripts = faster crawl
    if crawl.soup:
        scripts = crawl.soup.find_all("script", src=True)
        details["external_script_count"] = len(scripts)
        if len(scripts) <= 5:
            score += 5
        elif len(scripts) <= 15:
            score += 3
        elif len(scripts) <= 30:
            score += 1
        # 30+ scripts = heavy page
    else:
        score += 2

    return min(score, 25), details


# ── 3. Crawlability (20 pts) ─────────────────────────────────────────────

def _score_crawlability(crawl: CrawlResult) -> tuple[float, dict]:
    """Can AI crawlers access and follow this site's content?"""
    details = {}
    score = 0.0

    if not crawl.soup:
        details["crawl_failed"] = True
        details["_finding_crawl"] = "crawl_failed"
        return 0, details

    soup = crawl.soup

    # 3a. Meta robots OK — no noindex (5 pts)
    meta_robots = soup.find("meta", attrs={"name": "robots"})
    robots_content = meta_robots.get("content", "").lower() if meta_robots else ""
    noindex = "noindex" in robots_content
    details["meta_robots_ok"] = not noindex
    if not noindex:
        score += 5
    else:
        details["_finding_noindex"] = "meta_noindex"

    # 3b. Canonical tag (3 pts)
    canonical = soup.find("link", attrs={"rel": "canonical"})
    details["has_canonical"] = canonical is not None
    if canonical:
        score += 3

    # 3c. Internal link depth (7 pts) — can crawlers discover content?
    site_domain = urlparse(crawl.url).netloc
    internal_paths = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/") or site_domain in href:
            path = urlparse(href).path.strip("/")
            if path:
                internal_paths.add(path)
    details["internal_link_count"] = len(internal_paths)

    if len(internal_paths) >= 15:
        score += 7
    elif len(internal_paths) >= 8:
        score += 5
    elif len(internal_paths) >= 3:
        score += 3
    elif len(internal_paths) >= 1:
        score += 1

    # 3d. Viewport meta (2 pts) — mobile-friendly for AI crawlers
    has_viewport = soup.find("meta", attrs={"name": "viewport"}) is not None
    details["has_viewport"] = has_viewport
    if has_viewport:
        score += 2

    # 3e. Valid JSON-LD (3 pts) — structured data parseable
    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    valid_schemas = 0
    for script in scripts:
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and "@context" in data:
                valid_schemas += 1
            elif isinstance(data, list) and data:
                valid_schemas += 1
        except (ValueError, TypeError):
            pass
    details["valid_schema_count"] = valid_schemas
    if valid_schemas >= 2:
        score += 3
    elif valid_schemas >= 1:
        score += 1

    return min(score, 20), details


# ── 4. AI Readability (20 pts) ────────────────────────────────────────────

def _score_ai_readability(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Can AI cleanly extract meaningful content from this page?
    Measures: text-to-HTML ratio, content separability, JS dependency, noise.
    """
    details = {}
    score = 0.0

    if not crawl.soup or not crawl.html:
        return 0, {"no_html": True}

    html = crawl.html
    text = crawl.text
    soup = crawl.soup

    # 4a. Text-to-HTML ratio (6 pts) — higher = cleaner content
    html_len = len(html)
    text_len = len(text)
    ratio = text_len / html_len if html_len > 0 else 0
    details["text_to_html_ratio"] = round(ratio, 3)

    if ratio >= 0.25:       # 25%+ text = very clean
        score += 6
    elif ratio >= 0.15:     # 15-25% = good
        score += 4
    elif ratio >= 0.08:     # 8-15% = average
        score += 2
    else:                   # <8% = mostly markup/scripts
        details["_finding_ratio"] = "low_text_html_ratio"

    # 4b. DOM complexity (4 pts) — simpler DOM = easier for AI to parse
    all_tags = soup.find_all(True)
    dom_node_count = len(all_tags)
    details["dom_node_count"] = dom_node_count

    if dom_node_count < 500:
        score += 4
    elif dom_node_count < 1500:
        score += 3
    elif dom_node_count < 3000:
        score += 1
    # 3000+ nodes = very heavy DOM

    # 4c. JS dependency for content (5 pts) — penalty if content requires JS
    # Check: is there meaningful text in the raw HTML, or is it all JS-rendered?
    noscript_fallback = soup.find("noscript")
    inline_scripts = len(soup.find_all("script", src=False))

    # If very little text but lots of JS → likely JS-rendered content
    js_dependent = False
    if text_len < 200 and inline_scripts > 5:
        js_dependent = True
    # Check for common SPA frameworks
    html_lower = html.lower()
    spa_signals = ["__next_data__", "__nuxt", "window.__initial_state__",
                   "react-root", "ng-app", "data-v-", "#app"]
    spa_count = sum(1 for s in spa_signals if s in html_lower)

    details["js_dependent"] = js_dependent
    details["spa_framework_signals"] = spa_count
    details["inline_script_count"] = inline_scripts

    if not js_dependent and spa_count == 0:
        score += 5  # Server-rendered, clean
    elif not js_dependent:
        score += 3  # SPA but has content
    else:
        score += 0  # JS-dependent content
        details["_finding_js"] = "js_dependent_content"

    # 4d. Content noise ratio (5 pts) — ads, nav, footer vs main content
    # Measure: how much of the text is in <main>, <article>, or content-like divs
    main_content = ""
    for tag in ["main", "article"]:
        el = soup.find(tag)
        if el:
            main_content = el.get_text(strip=True)
            break
    if not main_content:
        # Try common content class patterns
        for cls in ["content", "post-content", "entry-content", "article-body", "page-content"]:
            el = soup.find(class_=re.compile(cls, re.I))
            if el and len(el.get_text(strip=True)) > 100:
                main_content = el.get_text(strip=True)
                break

    if main_content:
        content_ratio = len(main_content) / text_len if text_len > 0 else 0
        details["main_content_ratio"] = round(content_ratio, 2)
        if content_ratio >= 0.5:
            score += 5  # Most text is in main content area
        elif content_ratio >= 0.3:
            score += 3
        elif content_ratio >= 0.15:
            score += 1
    else:
        # No semantic main/article tag — harder for AI to extract
        details["main_content_ratio"] = 0
        score += 1  # Still has some text

    return min(score, 20), details


# ── 5. Structure Quality (10 pts) ────────────────────────────────────────

def _score_structure_quality(crawl: CrawlResult) -> tuple[float, dict]:
    """OG metadata, heading structure — helps AI understand page purpose."""
    details = {}
    score = 0.0

    if not crawl.soup:
        return 0, {}

    soup = crawl.soup

    # 5a. OG metadata (5 pts)
    og_title = soup.find("meta", property="og:title")
    og_desc = soup.find("meta", property="og:description")
    og_image = soup.find("meta", property="og:image")

    og_count = sum([
        bool(og_title and og_title.get("content")),
        bool(og_desc and og_desc.get("content")),
        bool(og_image and og_image.get("content")),
    ])
    details["og_tags_present"] = og_count
    details["has_og_title"] = bool(og_title and og_title.get("content"))
    details["has_og_description"] = bool(og_desc and og_desc.get("content"))

    if og_count >= 3:
        score += 5
    elif og_count >= 2:
        score += 3
    elif og_count >= 1:
        score += 1

    # 5b. Heading structure (3 pts) — clear H1 + hierarchy
    h1_tags = soup.find_all("h1")
    all_headings = soup.find_all(re.compile(r"^h[1-6]$"))
    details["h1_count"] = len(h1_tags)
    details["total_headings"] = len(all_headings)

    if len(h1_tags) == 1 and len(all_headings) >= 3:
        score += 3
    elif len(h1_tags) == 1:
        score += 2
    elif all_headings:
        score += 1

    # 5c. Language declared (2 pts)
    html_tag = soup.find("html")
    has_lang = html_tag and html_tag.get("lang")
    details["has_lang_attr"] = bool(has_lang)
    if has_lang:
        score += 2

    return min(score, 10), details


# ── Main Scorer ───────────────────────────────────────────────────────────

def score_technical(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Technical Score v3 — Real AI readiness.

    Score = (Infrastructure × 0.25) + (Performance × 0.25) +
            (Crawlability × 0.20) + (AI Readability × 0.20) +
            (Structure Quality × 0.10)
    """
    has_html = crawl.ok

    details = {"checks": {}, "findings": []}

    # Score each dimension
    infra_raw, infra_details = _score_infrastructure(crawl)         # max 25
    perf_raw, perf_details = _score_performance(crawl)              # max 25
    crawl_raw, crawl_details = _score_crawlability(crawl)           # max 20
    ai_read_raw, ai_read_details = _score_ai_readability(crawl)     # max 20
    struct_raw, struct_details = _score_structure_quality(crawl)     # max 10

    # Normalize to 0-100
    infra_score = (infra_raw / 25) * 100
    perf_score = (perf_raw / 25) * 100
    crawl_score = (crawl_raw / 20) * 100
    ai_read_score = (ai_read_raw / 20) * 100
    struct_score = (struct_raw / 10) * 100

    # Weighted total
    total = (
        infra_score * 0.25 +
        perf_score * 0.25 +
        crawl_score * 0.20 +
        ai_read_score * 0.20 +
        struct_score * 0.10
    )

    # Store details
    details["checks"]["infrastructure"] = infra_details
    details["checks"]["performance"] = perf_details
    details["checks"]["crawlability"] = crawl_details
    details["checks"]["ai_readability"] = ai_read_details
    details["checks"]["structure_quality"] = struct_details

    details["checks"]["infra_score"] = round(infra_score, 1)
    details["checks"]["perf_score"] = round(perf_score, 1)
    details["checks"]["crawl_score"] = round(crawl_score, 1)
    details["checks"]["ai_read_score"] = round(ai_read_score, 1)
    details["checks"]["struct_score"] = round(struct_score, 1)

    # Collect findings
    for sub in [infra_details, perf_details, crawl_details, ai_read_details, struct_details]:
        for key, val in sub.items():
            if key.startswith("_finding"):
                details["findings"].append(val)

    # Crawl failure cap
    if not has_html:
        total = min(total, 30)
        details["findings"].append("crawl_failed")

    total = safe_score(total)
    details["score"] = total

    return total, details
