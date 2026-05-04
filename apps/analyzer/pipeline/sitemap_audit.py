"""
Sitemap audit pipeline.

Discovers a site's sitemap, crawls the URLs, measures performance + structural
metrics, and scores each page for AI-readiness (deterministic heuristic, no LLM).

Public API:
- discover_sitemap(domain) -> list[str]
- fetch_robots(domain) -> dict
- audit_page(url, robots_allowed) -> dict
- score_page(fields) -> (int, severity, list[finding])
- fetch_psi_vitals(url) -> dict
- run_sitemap_audit(audit_id) -> None  (orchestrator, called from daemon thread)
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from django.db import close_old_connections

logger = logging.getLogger("apps")

USER_AGENT = "SignalorAuditBot/1.0 (+https://signalor.ai/bots)"
DEFAULT_TIMEOUT = 10
PSI_TIMEOUT = 45
HARD_URL_CAP = 200
SITEMAP_INDEX_DEPTH = 2
MAX_RESOURCES_FOR_HEAD = 40

AI_BOTS = [
    "GPTBot",
    "ClaudeBot",
    "PerplexityBot",
    "Google-Extended",
    "CCBot",
]


# ----------------------------------------------------------------------------
# Sitemap discovery
# ----------------------------------------------------------------------------

def _http_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> requests.Response | None:
    try:
        return requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        logger.debug("sitemap_audit: GET %s failed: %s", url, exc)
        return None


def _normalize_domain(value: str) -> str:
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    host = parsed.netloc or parsed.path
    return f"{parsed.scheme or 'https'}://{host.strip('/')}"


def _parse_sitemap_xml(xml_bytes: bytes) -> tuple[list[str], list[str]]:
    """Return (child_sitemaps, urls). Handles both sitemap-index and urlset."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return [], []
    tag = root.tag.lower()
    # strip namespace {...}
    tag = tag.split("}", 1)[-1]

    children: list[str] = []
    urls: list[str] = []
    if tag == "sitemapindex":
        for sm in root:
            loc = sm.find("{*}loc") if hasattr(sm, "find") else None
            if loc is None:
                for child in sm:
                    if child.tag.split("}", 1)[-1].lower() == "loc":
                        loc = child
                        break
            if loc is not None and loc.text:
                children.append(loc.text.strip())
    elif tag == "urlset":
        for u in root:
            for child in u:
                if child.tag.split("}", 1)[-1].lower() == "loc" and child.text:
                    urls.append(child.text.strip())
                    break
    return children, urls


def discover_sitemap(domain: str) -> dict[str, Any]:
    """
    Try to find a sitemap for `domain` and return:
      {
        "sitemap_url": "<first found>",
        "urls": [ ... up to HARD_URL_CAP ... ],
        "discovered": <int total found before cap>,
        "truncated": <bool>,
        "error": <str or "">,
      }
    """
    base = _normalize_domain(domain)
    candidates: list[str] = []

    # 1. robots.txt Sitemap: directive
    robots_resp = _http_get(f"{base}/robots.txt")
    if robots_resp and robots_resp.status_code < 400:
        for line in robots_resp.text.splitlines():
            line = line.strip()
            if line.lower().startswith("sitemap:"):
                candidates.append(line.split(":", 1)[1].strip())

    # 2. standard paths
    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        candidates.append(base + path)

    sitemap_url = ""
    urls: list[str] = []
    visited: set[str] = set()

    def walk(sm_url: str, depth: int) -> None:
        nonlocal sitemap_url
        if depth > SITEMAP_INDEX_DEPTH or sm_url in visited or len(urls) >= HARD_URL_CAP:
            return
        visited.add(sm_url)
        resp = _http_get(sm_url, timeout=DEFAULT_TIMEOUT)
        if not resp or resp.status_code >= 400 or not resp.content:
            return
        if not sitemap_url:
            sitemap_url = sm_url
        children, found = _parse_sitemap_xml(resp.content)
        for u in found:
            if u not in urls:
                urls.append(u)
            if len(urls) >= HARD_URL_CAP:
                return
        for child in children:
            walk(child, depth + 1)

    for cand in candidates:
        if len(urls) >= HARD_URL_CAP:
            break
        walk(cand, 0)
        if urls:
            break

    error = ""
    if not urls:
        error = "No sitemap found at /sitemap.xml, /sitemap_index.xml, or robots.txt"

    # dedupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)

    discovered = len(deduped)
    truncated = discovered > HARD_URL_CAP
    if truncated:
        deduped = deduped[:HARD_URL_CAP]

    return {
        "sitemap_url": sitemap_url,
        "urls": deduped,
        "discovered": discovered,
        "truncated": truncated,
        "error": error,
    }


# ----------------------------------------------------------------------------
# robots.txt
# ----------------------------------------------------------------------------

def fetch_robots(domain: str) -> dict[str, Any]:
    """
    Return {
      "allowed": {bot: bool, ...},
      "sitemap_urls": [...],
      "audit_bot_allowed": bool,  # whether SignalorAuditBot is allowed on /
      "raw": "<robots.txt text or ''>",
    }
    """
    base = _normalize_domain(domain)
    resp = _http_get(f"{base}/robots.txt")
    allowed = {bot: True for bot in AI_BOTS}
    sitemap_urls: list[str] = []
    audit_bot_allowed = True

    if not resp or resp.status_code >= 400 or not resp.text:
        return {
            "allowed": allowed,
            "sitemap_urls": sitemap_urls,
            "audit_bot_allowed": audit_bot_allowed,
            "raw": "",
        }

    raw = resp.text
    # Simple per-UA group parse
    current_agents: list[str] = []
    rules: dict[str, list[tuple[str, str]]] = {}  # agent -> [(directive, value)]

    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            current_agents = []
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "sitemap":
            sitemap_urls.append(value)
            continue
        if key == "user-agent":
            current_agents = [value]
            rules.setdefault(value, [])
            continue
        if key in ("disallow", "allow") and current_agents:
            for ag in current_agents:
                rules.setdefault(ag, []).append((key, value))

    def is_allowed(ua: str, path: str = "/") -> bool:
        candidates = [rules.get(ua, []), rules.get("*", [])]
        for group in candidates:
            if not group:
                continue
            longest: tuple[int, str, str] | None = None
            for directive, val in group:
                if val == "":
                    continue
                if path.startswith(val):
                    if longest is None or len(val) > longest[0]:
                        longest = (len(val), directive, val)
            if longest is not None:
                return longest[1] == "allow"
            # full disallow?
            for directive, val in group:
                if directive == "disallow" and val == "/":
                    return False
            return True
        return True

    for bot in AI_BOTS:
        allowed[bot] = is_allowed(bot)
    audit_bot_allowed = is_allowed("SignalorAuditBot")

    return {
        "allowed": allowed,
        "sitemap_urls": sitemap_urls,
        "audit_bot_allowed": audit_bot_allowed,
        "raw": raw,
    }


# ----------------------------------------------------------------------------
# Per-URL audit
# ----------------------------------------------------------------------------

def _visible_text_length(soup: BeautifulSoup) -> tuple[int, int]:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return len(text), len(text.split())


def _sum_resource_bytes(urls: list[str]) -> int:
    """HEAD the first N resources in parallel and sum content-length."""
    urls = urls[:MAX_RESOURCES_FOR_HEAD]
    if not urls:
        return 0

    def head(u: str) -> int:
        try:
            r = requests.head(
                u,
                headers={"User-Agent": USER_AGENT},
                timeout=5,
                allow_redirects=True,
            )
            cl = r.headers.get("content-length")
            return int(cl) if cl and cl.isdigit() else 0
        except Exception:
            return 0

    total = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for n in pool.map(head, urls):
            total += n
    return total


def audit_page(url: str, robots_allowed: dict[str, bool] | None = None) -> dict[str, Any]:
    """Fetch a single URL and return everything we can measure without a browser."""
    robots_allowed = robots_allowed or {bot: True for bot in AI_BOTS}
    out: dict[str, Any] = {
        "url": url,
        "final_url": url,
        "path": urlparse(url).path or "/",
        "state": "failed",
        "status_code": 0,
        "redirect_count": 0,
        "title": "",
        "meta_description": "",
        "h1_count": 0,
        "word_count": 0,
        "text_ratio": 0.0,
        "content_length": 0,
        "ttfb_ms": None,
        "resource_count": 0,
        "resource_bytes": 0,
        "link_count_total": 0,
        "link_count_internal": 0,
        "link_count_external": 0,
        "jsonld_count": 0,
        "has_canonical": False,
        "has_og": False,
        "is_noindex": False,
        "robots_allows_gptbot": bool(robots_allowed.get("GPTBot", True)),
        "robots_allows_claudebot": bool(robots_allowed.get("ClaudeBot", True)),
        "robots_allows_perplexitybot": bool(robots_allowed.get("PerplexityBot", True)),
        "error_message": "",
    }

    try:
        start = time.perf_counter()
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
    except requests.RequestException as exc:
        out["error_message"] = str(exc)[:480]
        return out

    out["status_code"] = resp.status_code
    out["final_url"] = resp.url
    out["redirect_count"] = len(resp.history)
    out["ttfb_ms"] = int(resp.elapsed.total_seconds() * 1000) if resp.elapsed else elapsed_ms
    out["content_length"] = len(resp.content or b"")

    if resp.history and 300 <= resp.history[0].status_code < 400:
        out["state"] = "redirect"
    elif 200 <= resp.status_code < 300:
        out["state"] = "crawled"
    else:
        out["state"] = "failed"
        out["error_message"] = f"HTTP {resp.status_code}"
        return out

    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" not in ctype:
        # Non-HTML: keep minimal metrics, nothing to parse.
        return out

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        out["error_message"] = f"parse error: {exc}"[:480]
        return out

    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        out["title"] = title_tag.string.strip()[:500]

    md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if md and md.get("content"):
        out["meta_description"] = md["content"].strip()[:1000]

    out["h1_count"] = len(soup.find_all("h1"))

    # Build text length BEFORE stripping so ratio is accurate
    html_len = len(resp.text) or 1
    text_len, words = _visible_text_length(BeautifulSoup(resp.text, "html.parser"))
    out["word_count"] = words
    out["text_ratio"] = round(text_len / html_len, 4) if html_len else 0.0

    # re-soup because _visible_text_length mutated
    soup = BeautifulSoup(resp.text, "html.parser")

    # canonical / og / noindex
    canon = soup.find("link", rel=lambda v: v and "canonical" in v)
    out["has_canonical"] = bool(canon and canon.get("href"))
    og_title = soup.find("meta", property="og:title")
    og_image = soup.find("meta", property="og:image")
    out["has_og"] = bool(og_title and og_image)
    robots_meta = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    if robots_meta and robots_meta.get("content"):
        out["is_noindex"] = "noindex" in robots_meta["content"].lower()

    # JSON-LD
    jsonld_scripts = soup.find_all("script", type="application/ld+json")
    out["jsonld_count"] = len(jsonld_scripts)

    # Resources
    resource_urls: list[str] = []
    for tag in soup.find_all(["img", "script"]):
        src = tag.get("src")
        if src:
            resource_urls.append(urljoin(url, src))
    for link in soup.find_all("link", rel=lambda v: v and "stylesheet" in v):
        href = link.get("href")
        if href:
            resource_urls.append(urljoin(url, href))
    out["resource_count"] = len(resource_urls)
    out["resource_bytes"] = _sum_resource_bytes(resource_urls)

    # Links
    base_host = urlparse(url).netloc.lower()
    internal = external = 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        parsed = urlparse(urljoin(url, href))
        if not parsed.netloc:
            internal += 1
        elif parsed.netloc.lower() == base_host:
            internal += 1
        else:
            external += 1
    out["link_count_total"] = internal + external
    out["link_count_internal"] = internal
    out["link_count_external"] = external

    return out


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------

def score_page(fields: dict[str, Any]) -> tuple[int, str, list[dict[str, Any]]]:
    """
    Returns (score 0-100, severity 'ok'|'warn'|'fail', findings list).

    Findings are {"code", "label", "severity"}.
    """
    findings: list[dict[str, Any]] = []
    score = 0
    sev_rank = {"ok": 0, "warn": 1, "fail": 2}
    max_sev = "ok"

    def bump(next_sev: str) -> None:
        nonlocal max_sev
        if sev_rank[next_sev] > sev_rank[max_sev]:
            max_sev = next_sev

    def add(code: str, label: str, severity: str) -> None:
        findings.append({"code": code, "label": label, "severity": severity})
        bump(severity)

    # HTTP status (weight 20)
    sc = int(fields.get("status_code") or 0)
    if 200 <= sc < 300:
        score += 20
    else:
        add("http_status", f"HTTP {sc or 'error'}", "fail")

    # Redirects (weight 5)
    redirects = int(fields.get("redirect_count") or 0)
    if redirects <= 1:
        score += 5
    else:
        add("redirect_chain", f"{redirects} redirect hops", "warn")

    # Title (weight 10)
    title = (fields.get("title") or "").strip()
    tlen = len(title)
    if not title:
        add("missing_title", "No <title> tag", "warn")
    elif 30 <= tlen <= 60:
        score += 10
    else:
        score += 5
        add("title_length", f"Title {tlen} chars (ideal 30–60)", "warn")

    # Meta description (weight 8)
    md = (fields.get("meta_description") or "").strip()
    mlen = len(md)
    if not md:
        add("missing_meta_desc", "No meta description", "warn")
    elif 120 <= mlen <= 160:
        score += 8
    else:
        score += 4
        add("meta_desc_length", f"Meta description {mlen} chars (ideal 120–160)", "warn")

    # H1 (weight 8)
    h1 = int(fields.get("h1_count") or 0)
    if h1 == 1:
        score += 8
    elif h1 == 0:
        add("missing_h1", "No <h1>", "warn")
    else:
        add("multiple_h1", f"{h1} <h1> tags (expected 1)", "warn")

    # Word count (weight 8) + soft-404
    words = int(fields.get("word_count") or 0)
    title_lower = title.lower()
    soft_404 = words < 50 and ("not found" in title_lower or "404" in title_lower)
    if soft_404:
        add("soft_404", "Likely soft-404 (low word count + 'not found' title)", "fail")
    elif words >= 300:
        score += 8
    elif words >= 150:
        score += 4
        add("thin_content", f"Only {words} words (target ≥300)", "warn")
    else:
        add("very_thin_content", f"Only {words} words", "warn")

    # JSON-LD (weight 15)
    jsonld = int(fields.get("jsonld_count") or 0)
    if jsonld >= 1:
        score += 15
    else:
        add("no_jsonld", "No JSON-LD structured data", "warn")

    # Canonical (weight 5)
    if fields.get("has_canonical"):
        score += 5
    else:
        add("no_canonical", "No canonical tag", "warn")

    # Open Graph (weight 5)
    if fields.get("has_og"):
        score += 5
    # no finding if missing; info only

    # noindex (weight 8)
    if fields.get("is_noindex"):
        add("noindex", "Page is marked noindex", "fail")
    else:
        score += 8

    # robots: GPTBot (4), ClaudeBot (2), PerplexityBot (2)
    if fields.get("robots_allows_gptbot", True):
        score += 4
    else:
        add("robots_block_gptbot", "robots.txt disallows GPTBot", "warn")
    if fields.get("robots_allows_claudebot", True):
        score += 2
    else:
        add("robots_block_claudebot", "robots.txt disallows ClaudeBot", "warn")
    if fields.get("robots_allows_perplexitybot", True):
        score += 2
    else:
        add("robots_block_perplexitybot", "robots.txt disallows PerplexityBot", "warn")

    score = max(0, min(100, score))
    return score, max_sev, findings


# ----------------------------------------------------------------------------
# PageSpeed Insights
# ----------------------------------------------------------------------------

def fetch_psi_vitals(url: str) -> dict[str, Any]:
    """Call Google PageSpeed Insights. Returns {} on any error."""
    api_key = os.environ.get("PAGESPEED_INSIGHTS_API_KEY", "").strip()
    endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    if api_key:
        params["key"] = api_key
    try:
        from apps.integrations._http import request_with_retry
        resp = request_with_retry(
            "GET",
            endpoint,
            params=params,
            timeout=PSI_TIMEOUT,
            max_retries=2,
        )
    except requests.RequestException as exc:
        logger.debug("psi: %s failed: %s", url, exc)
        return {}
    if resp.status_code != 200:
        return {}
    try:
        data = resp.json()
    except ValueError:
        return {}

    audits = (
        data.get("lighthouseResult", {})
        .get("audits", {})
    )

    def get_num(audit_key: str) -> int | None:
        node = audits.get(audit_key) or {}
        val = node.get("numericValue")
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    return {
        "lcp_ms": get_num("largest-contentful-paint"),
        "fcp_ms": get_num("first-contentful-paint"),
        "ttfb_ms": get_num("server-response-time"),
        "server_ms": get_num("server-response-time"),
    }


# ----------------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------------

def _avg_int(values: list[int | None]) -> int | None:
    nums = [v for v in values if isinstance(v, int)]
    return int(sum(nums) / len(nums)) if nums else None


def run_sitemap_audit(audit_id: int) -> None:
    """Orchestrator — runs in a daemon thread. Mutates SitemapAudit + pages."""
    from django.utils import timezone as djtz
    from apps.analyzer.models import SitemapAudit, SitemapAuditPage

    try:
        audit = SitemapAudit.objects.select_related("analysis_run").get(pk=audit_id)
    except SitemapAudit.DoesNotExist:
        logger.warning("run_sitemap_audit: audit %s gone", audit_id)
        return

    audit.status = SitemapAudit.Status.RUNNING
    audit.progress = 1
    audit.started_at = djtz.now()
    audit.save(update_fields=["status", "progress", "started_at"])

    try:
        domain = audit.analysis_run.url
        disc = discover_sitemap(domain)
        if not disc["urls"]:
            audit.status = SitemapAudit.Status.FAILED
            audit.error_message = disc.get("error") or "No URLs discovered"
            audit.finished_at = djtz.now()
            audit.progress = 100
            audit.save(update_fields=["status", "error_message", "finished_at", "progress"])
            return

        audit.sitemap_url = disc["sitemap_url"]
        audit.discovered_url_count = disc["discovered"]
        audit.truncated = disc["truncated"]
        audit.total_urls = len(disc["urls"])
        audit.save(update_fields=["sitemap_url", "discovered_url_count", "truncated", "total_urls"])

        robots = fetch_robots(domain)
        if not robots.get("audit_bot_allowed", True):
            audit.status = SitemapAudit.Status.FAILED
            audit.error_message = "robots.txt disallows SignalorAuditBot"
            audit.finished_at = djtz.now()
            audit.progress = 100
            audit.save(update_fields=["status", "error_message", "finished_at", "progress"])
            return

        urls = disc["urls"]
        done = 0
        lock = threading.Lock()
        page_ids: dict[str, int] = {}  # url -> SitemapAuditPage.id for pass-2 updates

        def persist_row(fields: dict[str, Any]) -> int:
            ai_score, severity, findings = score_page(fields)
            fields["ai_score"] = ai_score
            fields["severity"] = severity
            fields["findings"] = findings
            row = SitemapAuditPage.objects.create(
                audit=audit,
                url=fields["url"][:2048],
                path=(fields.get("path") or "")[:2048],
                final_url=(fields.get("final_url") or "")[:2048],
                state=fields.get("state", "failed"),
                status_code=int(fields.get("status_code") or 0),
                redirect_count=int(fields.get("redirect_count") or 0),
                title=(fields.get("title") or "")[:500],
                meta_description=(fields.get("meta_description") or "")[:1000],
                h1_count=int(fields.get("h1_count") or 0),
                word_count=int(fields.get("word_count") or 0),
                text_ratio=float(fields.get("text_ratio") or 0.0),
                content_length=int(fields.get("content_length") or 0),
                lcp_ms=fields.get("lcp_ms"),
                fcp_ms=fields.get("fcp_ms"),
                ttfb_ms=fields.get("ttfb_ms"),
                server_ms=fields.get("server_ms"),
                resource_count=int(fields.get("resource_count") or 0),
                resource_bytes=int(fields.get("resource_bytes") or 0),
                link_count_total=int(fields.get("link_count_total") or 0),
                link_count_internal=int(fields.get("link_count_internal") or 0),
                link_count_external=int(fields.get("link_count_external") or 0),
                jsonld_count=int(fields.get("jsonld_count") or 0),
                has_canonical=bool(fields.get("has_canonical")),
                has_og=bool(fields.get("has_og")),
                is_noindex=bool(fields.get("is_noindex")),
                robots_allows_gptbot=bool(fields.get("robots_allows_gptbot", True)),
                robots_allows_claudebot=bool(fields.get("robots_allows_claudebot", True)),
                robots_allows_perplexitybot=bool(fields.get("robots_allows_perplexitybot", True)),
                ai_score=ai_score,
                severity=severity,
                findings=findings,
                error_message=(fields.get("error_message") or "")[:500],
            )
            return row.pk

        def update_counts_and_avgs() -> None:
            """Recompute audit roll-ups from persisted pages."""
            from django.db.models import Avg, Count, Q
            agg = audit.pages.aggregate(
                indexed=Count("id", filter=Q(state="crawled")),
                redirect=Count("id", filter=Q(state="redirect")),
                queued=Count("id", filter=Q(state="queued")),
                failed=Count("id", filter=Q(state="failed")),
                lcp=Avg("lcp_ms"),
                fcp=Avg("fcp_ms"),
                ttfb=Avg("ttfb_ms", filter=Q(state="crawled")),
                ai=Avg("ai_score"),
            )
            SitemapAudit.objects.filter(pk=audit.pk).update(
                indexed_count=agg["indexed"] or 0,
                redirect_count=agg["redirect"] or 0,
                queued_count=agg["queued"] or 0,
                failed_count=agg["failed"] or 0,
                avg_lcp_ms=int(agg["lcp"]) if agg["lcp"] is not None else None,
                avg_fcp_ms=int(agg["fcp"]) if agg["fcp"] is not None else None,
                avg_ttfb_ms=int(agg["ttfb"]) if agg["ttfb"] is not None else None,
                avg_ai_score=int(agg["ai"]) if agg["ai"] is not None else None,
            )

        # Pass 1 — fetch + parse + persist each URL as it finishes
        def work(u: str) -> dict[str, Any]:
            return audit_page(u, robots_allowed=robots["allowed"])

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(work, u): u for u in urls}
            for fut in as_completed(futures):
                url = futures[fut]
                try:
                    fields = fut.result()
                except Exception as exc:
                    fields = {
                        "url": url,
                        "state": "failed",
                        "error_message": str(exc)[:480],
                        "status_code": 0,
                    }
                try:
                    close_old_connections()
                    pid = persist_row(fields)
                    page_ids[url] = pid
                except Exception:
                    logger.exception("persist_row failed for %s", url)
                with lock:
                    done += 1
                    pct = 5 + int((done / max(1, len(urls))) * 65)  # 5-70%
                    SitemapAudit.objects.filter(pk=audit.pk).update(progress=min(70, pct))
                    if done % 3 == 0 or done == len(urls):
                        update_counts_and_avgs()

        update_counts_and_avgs()

        # Pass 2 — PSI for successful pages only (rate-limit conscious)
        crawled_urls = list(
            audit.pages.filter(state="crawled").values_list("url", "id")
        )
        psi_done = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_psi_vitals, u): (u, pid) for u, pid in crawled_urls}
            for fut in as_completed(futures):
                u, pid = futures[fut]
                try:
                    vitals = fut.result()
                except Exception:
                    vitals = {}
                if vitals:
                    updates: dict[str, Any] = {}
                    if vitals.get("lcp_ms") is not None:
                        updates["lcp_ms"] = vitals["lcp_ms"]
                    if vitals.get("fcp_ms") is not None:
                        updates["fcp_ms"] = vitals["fcp_ms"]
                    if vitals.get("ttfb_ms"):
                        updates["ttfb_ms"] = vitals["ttfb_ms"]
                    if vitals.get("server_ms") is not None:
                        updates["server_ms"] = vitals["server_ms"]
                    if updates:
                        try:
                            close_old_connections()
                            SitemapAuditPage.objects.filter(pk=pid).update(**updates)
                        except Exception:
                            logger.exception("psi update failed for %s", u)
                with lock:
                    psi_done += 1
                    pct = 70 + int((psi_done / max(1, len(crawled_urls))) * 25)  # 70-95
                    SitemapAudit.objects.filter(pk=audit.pk).update(progress=min(95, pct))
                    if psi_done % 3 == 0 or psi_done == len(crawled_urls):
                        update_counts_and_avgs()

        update_counts_and_avgs()

        audit.status = SitemapAudit.Status.COMPLETE
        audit.progress = 100
        audit.finished_at = djtz.now()
        audit.save(update_fields=["status", "progress", "finished_at"])

    except Exception as exc:
        logger.exception("run_sitemap_audit failed")
        audit.status = SitemapAudit.Status.FAILED
        audit.error_message = str(exc)[:480]
        audit.progress = 100
        audit.finished_at = djtz.now()
        audit.save(update_fields=["status", "error_message", "progress", "finished_at"])
