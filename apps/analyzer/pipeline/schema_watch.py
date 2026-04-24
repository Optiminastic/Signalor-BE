"""
Schema Watchtower pipeline.

Re-fetches a set of URLs (products, articles, FAQs) for an analysis run and
validates the JSON-LD against a static rule set. Reports per-URL issues and
severity so merchants can see which pages have broken structured data before
AI assistants stop citing them.

v1: static validator only. v2 will diff against a stored baseline snapshot
for drift detection ("price changed 90% → probably broken", "availability
flipped to OutOfStock").

Public API:
- discover_watch_urls(run) -> list[(url, kind)]
- fetch_schema(url) -> dict
- validate_schema(parsed, url) -> (severity, issues, fix_targets, kind)
- run_schema_watch(watch_id) -> None  (orchestrator, daemon thread)
"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from django.db import close_old_connections

logger = logging.getLogger("apps")

USER_AGENT = "SignalorSchemaBot/1.0 (+https://signalor.ai/bots)"
DEFAULT_TIMEOUT = 12
HARD_URL_CAP = 100  # tighter than sitemap — deeper per-URL analysis

# ----------------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------------

PRODUCT_PATH_HINTS = ("/product/", "/products/", "/p/", "/item/", "/shop/")
ARTICLE_PATH_HINTS = (
    "/blog/",
    "/blogs/",
    "/post/",
    "/posts/",
    "/article/",
    "/news/",
    "/guide/",
    "/resources/",
)


_DATE_ARTICLE_RE = re.compile(r"/\d{4}/\d{1,2}/")


def _kind_hint(url: str) -> str:
    path = urlparse(url).path.lower()
    if any(h in path for h in PRODUCT_PATH_HINTS):
        return "product"
    if any(h in path for h in ARTICLE_PATH_HINTS) or _DATE_ARTICLE_RE.search(path):
        return "article"
    return "page"


def discover_watch_urls(run) -> list[tuple[str, str]]:
    """Pull candidate URLs for schema checking from the latest complete
    SitemapAudit on the run. Prefer product + article pages. Falls back to
    the run's root URL if no audit exists."""
    from apps.analyzer.models import SitemapAudit

    audit = (
        SitemapAudit.objects.filter(analysis_run=run, status="complete")
        .order_by("-finished_at")
        .first()
    )
    if not audit:
        root = run.url
        return [(root, "page")] if root else []

    rows = audit.pages.filter(state="crawled").values_list("url", "jsonld_count")
    candidates: list[tuple[str, str]] = []
    for url, _jsonld_count in rows:
        candidates.append((url, _kind_hint(url)))

    # Sort: product first, then article, then page. Cap at HARD_URL_CAP.
    priority = {"product": 0, "article": 1, "page": 2}
    candidates.sort(key=lambda t: priority.get(t[1], 3))
    return candidates[:HARD_URL_CAP]


# ----------------------------------------------------------------------------
# Fetch + parse
# ----------------------------------------------------------------------------

def _flatten_graph(parsed: Any) -> list[dict]:
    """JSON-LD can use `@graph`; flatten to a list of dicts."""
    out: list[dict] = []
    if isinstance(parsed, list):
        for item in parsed:
            out.extend(_flatten_graph(item))
    elif isinstance(parsed, dict):
        if "@graph" in parsed and isinstance(parsed["@graph"], list):
            for item in parsed["@graph"]:
                out.extend(_flatten_graph(item))
        else:
            out.append(parsed)
    return out


def _types_of(node: dict) -> list[str]:
    t = node.get("@type")
    if isinstance(t, list):
        return [str(x) for x in t]
    if t:
        return [str(t)]
    return []


def fetch_schema(url: str) -> dict[str, Any]:
    """Fetch URL, extract + parse all JSON-LD blocks. Returns dict with status,
    flat list of nodes, and any parse errors."""
    out: dict[str, Any] = {
        "url": url,
        "status_code": 0,
        "nodes": [],
        "error_message": "",
    }
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        out["error_message"] = str(exc)[:480]
        return out

    out["status_code"] = resp.status_code
    if resp.status_code >= 400:
        out["error_message"] = f"HTTP {resp.status_code}"
        return out

    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" not in ctype:
        out["error_message"] = f"non-HTML content-type: {ctype}"
        return out

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        out["error_message"] = f"parse: {exc}"[:480]
        return out

    nodes: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Try stripping JS-style comments / trailing commas
            cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                nodes.append({"_parse_error": True, "_raw_snippet": raw[:200]})
                continue
        nodes.extend(_flatten_graph(parsed))

    out["nodes"] = nodes
    return out


# ----------------------------------------------------------------------------
# Validation rules
# ----------------------------------------------------------------------------

FAIL = "fail"
WARN = "warn"
INFO = "info"
OK = "ok"

SEVERITY_RANK = {OK: 0, INFO: 0, WARN: 1, FAIL: 2}


def _as_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _get(node: dict, *keys: str) -> Any:
    for k in keys:
        if k in node and node[k] not in (None, "", []):
            return node[k]
    return None


def _validate_product(node: dict) -> list[dict]:
    issues: list[dict] = []
    if not _get(node, "name"):
        issues.append({"code": "product_missing_name", "label": "Product schema has no name", "severity": FAIL, "type": "Product"})
    offers = _as_list(node.get("offers"))
    if not offers:
        issues.append({"code": "product_missing_offers", "label": "Product has no offers/price schema", "severity": FAIL, "type": "Product"})
    else:
        first = offers[0] if isinstance(offers[0], dict) else {}
        if not _get(first, "price", "lowPrice", "highPrice"):
            issues.append({"code": "offer_missing_price", "label": "Offer has no price", "severity": FAIL, "type": "Offer"})
        if not _get(first, "priceCurrency"):
            issues.append({"code": "offer_missing_currency", "label": "Offer has no priceCurrency", "severity": WARN, "type": "Offer"})
        availability = str(_get(first, "availability") or "")
        if not availability:
            issues.append({"code": "offer_missing_availability", "label": "Offer has no availability", "severity": FAIL, "type": "Offer"})
        elif availability.endswith("OutOfStock"):
            issues.append({"code": "offer_out_of_stock", "label": "Product currently marked out of stock", "severity": INFO, "type": "Offer"})
        elif availability.endswith("Discontinued"):
            issues.append({"code": "offer_discontinued", "label": "Product marked discontinued", "severity": WARN, "type": "Offer"})
    if not _get(node, "brand"):
        issues.append({"code": "product_missing_brand", "label": "Product has no brand", "severity": WARN, "type": "Product"})
    if not _get(node, "image"):
        issues.append({"code": "product_missing_image", "label": "Product has no image", "severity": WARN, "type": "Product"})
    if not _get(node, "description"):
        issues.append({"code": "product_missing_description", "label": "Product has no description", "severity": WARN, "type": "Product"})
    if not _get(node, "sku", "mpn", "gtin", "gtin13", "gtin8"):
        issues.append({"code": "product_missing_sku", "label": "Product has no SKU/GTIN/MPN", "severity": WARN, "type": "Product"})
    if not _get(node, "aggregateRating", "review"):
        issues.append({"code": "product_missing_rating", "label": "Product has no reviews or aggregate rating", "severity": INFO, "type": "Product"})
    return issues


def _validate_article(node: dict) -> list[dict]:
    issues: list[dict] = []
    if not _get(node, "headline"):
        issues.append({"code": "article_missing_headline", "label": "Article has no headline", "severity": FAIL, "type": "Article"})
    if not _get(node, "datePublished"):
        issues.append({"code": "article_missing_date_published", "label": "Article has no datePublished", "severity": FAIL, "type": "Article"})
    if not _get(node, "author"):
        issues.append({"code": "article_missing_author", "label": "Article has no author", "severity": WARN, "type": "Article"})
    if not _get(node, "image"):
        issues.append({"code": "article_missing_image", "label": "Article has no image", "severity": WARN, "type": "Article"})
    if not _get(node, "publisher"):
        issues.append({"code": "article_missing_publisher", "label": "Article has no publisher", "severity": WARN, "type": "Article"})
    return issues


def _validate_faqpage(node: dict) -> list[dict]:
    issues: list[dict] = []
    entities = _as_list(node.get("mainEntity"))
    if len(entities) == 0:
        issues.append({"code": "faq_empty", "label": "FAQPage has no questions (mainEntity)", "severity": FAIL, "type": "FAQPage"})
    elif len(entities) < 2:
        issues.append({"code": "faq_too_few", "label": "FAQPage has fewer than 2 questions", "severity": WARN, "type": "FAQPage"})
    missing_answer = 0
    for q in entities:
        if isinstance(q, dict) and not _get(q, "acceptedAnswer"):
            missing_answer += 1
    if missing_answer:
        issues.append({
            "code": "faq_missing_answers",
            "label": f"{missing_answer} question(s) have no acceptedAnswer",
            "severity": FAIL,
            "type": "Question",
        })
    return issues


def _validate_aggregate_rating(node: dict) -> list[dict]:
    issues: list[dict] = []
    if not _get(node, "ratingValue"):
        issues.append({"code": "rating_missing_value", "label": "AggregateRating has no ratingValue", "severity": FAIL, "type": "AggregateRating"})
    if not _get(node, "reviewCount", "ratingCount"):
        issues.append({"code": "rating_missing_count", "label": "AggregateRating has no reviewCount", "severity": WARN, "type": "AggregateRating"})
    return issues


def _validate_organization(node: dict) -> list[dict]:
    issues: list[dict] = []
    if not _get(node, "name"):
        issues.append({"code": "org_missing_name", "label": "Organization has no name", "severity": WARN, "type": "Organization"})
    if not _get(node, "logo"):
        issues.append({"code": "org_missing_logo", "label": "Organization has no logo", "severity": WARN, "type": "Organization"})
    if not _get(node, "url"):
        issues.append({"code": "org_missing_url", "label": "Organization has no url", "severity": INFO, "type": "Organization"})
    return issues


TYPE_VALIDATORS = {
    "Product": _validate_product,
    "Article": _validate_article,
    "BlogPosting": _validate_article,
    "NewsArticle": _validate_article,
    "TechArticle": _validate_article,
    "FAQPage": _validate_faqpage,
    "AggregateRating": _validate_aggregate_rating,
    "Organization": _validate_organization,
    "LocalBusiness": _validate_organization,
}


def validate_schema(fetched: dict) -> tuple[str, list[dict], list[str], str]:
    """Apply rules. Returns (severity, issues, fix_targets, kind)."""
    kind = _kind_hint(fetched["url"])
    issues: list[dict] = []
    fix_targets: set[str] = set()

    # Parse-level errors first
    for node in fetched.get("nodes") or []:
        if node.get("_parse_error"):
            issues.append({
                "code": "jsonld_parse_error",
                "label": "JSON-LD block failed to parse",
                "severity": FAIL,
                "type": "JSON-LD",
            })
            fix_targets.add("jsonld")
            break

    types_seen: set[str] = set()
    for node in fetched.get("nodes") or []:
        if node.get("_parse_error"):
            continue
        for t in _types_of(node):
            types_seen.add(t)
            validator = TYPE_VALIDATORS.get(t)
            if validator:
                node_issues = validator(node)
                issues.extend(node_issues)
                for iss in node_issues:
                    fix_targets.add(t)

    # No JSON-LD at all on what should be a product/article? That's a finding.
    if not types_seen:
        if kind == "product":
            issues.append({
                "code": "no_product_schema",
                "label": "Page looks like a product but has no Product schema",
                "severity": FAIL,
                "type": "Product",
            })
            fix_targets.add("Product")
        elif kind == "article":
            issues.append({
                "code": "no_article_schema",
                "label": "Page looks like an article but has no Article/BlogPosting schema",
                "severity": FAIL,
                "type": "Article",
            })
            fix_targets.add("Article")
        else:
            issues.append({
                "code": "no_schema",
                "label": "Page has no JSON-LD structured data",
                "severity": WARN,
                "type": "JSON-LD",
            })

    # Surface fetch-level error, if any
    if fetched.get("error_message"):
        issues.insert(0, {
            "code": "fetch_error",
            "label": fetched["error_message"],
            "severity": FAIL,
            "type": "Fetch",
        })

    severity = OK
    for iss in issues:
        if SEVERITY_RANK.get(iss["severity"], 0) > SEVERITY_RANK[severity]:
            severity = iss["severity"]
    # Collapse info-only to ok for row severity
    if severity == INFO:
        severity = OK

    return severity, issues, sorted(fix_targets), kind


# ----------------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------------

def run_schema_watch(watch_id: int) -> None:
    from django.utils import timezone as djtz
    from apps.analyzer.models import SchemaWatch, SchemaWatchPage

    try:
        watch = SchemaWatch.objects.select_related("analysis_run").get(pk=watch_id)
    except SchemaWatch.DoesNotExist:
        logger.warning("run_schema_watch: watch %s gone", watch_id)
        return

    watch.status = SchemaWatch.Status.RUNNING
    watch.progress = 1
    watch.started_at = djtz.now()
    watch.save(update_fields=["status", "progress", "started_at"])

    try:
        targets = discover_watch_urls(watch.analysis_run)
        if not targets:
            watch.status = SchemaWatch.Status.FAILED
            watch.error_message = "No URLs to watch (no completed sitemap audit yet — run one first)."
            watch.progress = 100
            watch.finished_at = djtz.now()
            watch.save(update_fields=["status", "error_message", "progress", "finished_at"])
            return

        watch.total_urls = len(targets)
        watch.save(update_fields=["total_urls"])

        done = 0
        lock = threading.Lock()

        def persist(url: str, kind: str, fetched: dict) -> None:
            severity, issues, fix_targets, _ = validate_schema(fetched)
            types = []
            for node in fetched.get("nodes") or []:
                if node.get("_parse_error"):
                    continue
                for t in _types_of(node):
                    if t not in types:
                        types.append(t)
            # Trim raw JSON-LD payload to prevent runaway row sizes
            raw = [
                n for n in (fetched.get("nodes") or []) if not n.get("_parse_error")
            ][:20]
            SchemaWatchPage.objects.create(
                watch=watch,
                url=url[:2048],
                path=(urlparse(url).path or "/")[:2048],
                page_kind=kind,
                status_code=int(fetched.get("status_code") or 0),
                schema_types=types,
                jsonld_count=len(raw),
                raw_jsonld=raw,
                severity=severity,
                issues=issues,
                fix_targets=fix_targets,
                error_message=(fetched.get("error_message") or "")[:500],
            )

        def work(target: tuple[str, str]) -> None:
            url, kind = target
            try:
                fetched = fetch_schema(url)
            except Exception as exc:
                fetched = {
                    "url": url,
                    "status_code": 0,
                    "nodes": [],
                    "error_message": str(exc)[:480],
                }
            try:
                close_old_connections()
                persist(url, kind, fetched)
            except Exception:
                logger.exception("schema_watch persist failed for %s", url)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(work, t): t for t in targets}
            for fut in as_completed(futures):
                fut.result()
                with lock:
                    done += 1
                    pct = 5 + int((done / max(1, len(targets))) * 90)  # 5-95
                    SchemaWatch.objects.filter(pk=watch.pk).update(progress=min(95, pct))

        # Roll up counts
        from django.db.models import Count, Q
        agg = watch.pages.aggregate(
            healthy=Count("id", filter=Q(severity="ok")),
            warn=Count("id", filter=Q(severity="warn")),
            broken=Count("id", filter=Q(severity="fail")),
        )
        watch.healthy_count = agg["healthy"] or 0
        watch.warn_count = agg["warn"] or 0
        watch.broken_count = agg["broken"] or 0
        watch.status = SchemaWatch.Status.COMPLETE
        watch.progress = 100
        watch.finished_at = djtz.now()
        watch.save()

    except Exception as exc:
        logger.exception("run_schema_watch failed")
        watch.status = SchemaWatch.Status.FAILED
        watch.error_message = str(exc)[:480]
        watch.progress = 100
        watch.finished_at = djtz.now()
        watch.save(update_fields=["status", "error_message", "progress", "finished_at"])
