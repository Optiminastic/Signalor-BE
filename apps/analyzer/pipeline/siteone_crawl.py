"""SiteOne Crawler integration — run the SiteOne CLI and parse its JSON report.

An optional *technical/SEO* data source for the analyzer. SiteOne already computes
per-category quality scores (performance / SEO / security / accessibility /
best-practices), each with human-readable deductions and fixes, plus 404, redirect,
security-header and per-page SEO tables. We shell out to its CLI, capture the JSON
report (``--output-json-file``), and adapt it into a typed :class:`SiteOneReport`
for the technical + content scorers.

Gated behind two conditions so production is untouched until explicitly enabled:
  * ``SIGNALOR_USE_SITEONE`` truthy (default off), and
  * a resolvable binary — ``SITEONE_CRAWLER_BIN`` (absolute path) or
    ``siteone-crawler`` on ``PATH``.

Callers use :func:`is_configured` to decide whether to attempt a run and treat any
:class:`SiteOneError` as a soft failure (fall back to the normal crawl).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

logger = logging.getLogger("apps")

# SiteOne CLI knobs (kept small — this runs inside a user-facing analysis).
DEFAULT_MAX_URLS = 30
DEFAULT_WORKERS = 3
DEFAULT_REQUEST_TIMEOUT = 8  # per-request seconds (SiteOne --timeout)
DEFAULT_RUN_TIMEOUT = 180  # overall subprocess budget, seconds

_TRUTHY = ("1", "true", "yes", "on")


class SiteOneError(Exception):
    """Raised when the SiteOne crawl fails or its output can't be parsed."""


@dataclass
class Deduction:
    """A single scored issue SiteOne subtracted from a category (with its fix)."""

    reason: str
    fix: str
    points: float


@dataclass
class CategoryScore:
    """One SiteOne quality category, e.g. ``security`` scored 7.5/10."""

    code: str  # performance | seo | security | accessibility | best-practices
    name: str
    score: float  # 0-10
    weight: float
    label: str  # Excellent | Good | ...
    deductions: list[Deduction] = field(default_factory=list)


@dataclass
class SiteOneReport:
    """Parsed, scorer-friendly view of a SiteOne JSON report."""

    url: str
    overall_score: float | None  # 0-10 when present
    categories: list[CategoryScore]
    summary_by_severity: dict[str, int]  # CRITICAL/WARNING/NOTICE/OK/INFO -> count
    summary_items: list[dict]  # [{aplCode, status, text}]
    broken_links: list[dict]  # rows from tables['404']
    redirects: list[dict]  # rows from tables['redirects']
    security_findings: list[dict]  # rows from tables['security']
    seo_pages: list[dict]  # rows from tables['seo']
    total_urls: int
    request_ms_avg: float
    request_ms_p90: float
    count_by_status: dict[str, int]

    def category(self, code: str) -> CategoryScore | None:
        """Return the category with ``code`` (e.g. ``"security"``) or ``None``."""
        return next((c for c in self.categories if c.code == code), None)


def resolve_binary() -> str | None:
    """Resolve the SiteOne executable from env or PATH, or ``None`` if absent."""
    explicit = os.getenv("SITEONE_CRAWLER_BIN", "").strip()
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    return shutil.which("siteone-crawler")


def is_configured() -> bool:
    """Whether SiteOne is enabled (flag on) *and* the binary is resolvable."""
    flag = os.getenv("SIGNALOR_USE_SITEONE", "false").strip().lower()
    if flag not in _TRUTHY:
        return False
    return resolve_binary() is not None


def run_report(
    url: str,
    *,
    max_urls: int = DEFAULT_MAX_URLS,
    workers: int = DEFAULT_WORKERS,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> SiteOneReport:
    """Crawl ``url`` with SiteOne and return the parsed report.

    Raises :class:`SiteOneError` on any launch/timeout/parse failure so callers
    can fall back to the normal crawl. Writes the JSON to a throwaway temp dir
    (no HTML/TXT artifacts) that is removed on return.
    """
    binary = resolve_binary()
    if not binary:
        raise SiteOneError(
            "SiteOne binary not found — set SITEONE_CRAWLER_BIN or add "
            "siteone-crawler to PATH."
        )
    with tempfile.TemporaryDirectory(prefix="siteone_") as tmp:
        out_json = os.path.join(tmp, "report.json")
        cmd = [
            binary,
            f"--url={url}",
            "--output=json",
            f"--output-json-file={out_json}",
            "--output-html-report=",  # disable extra artifacts
            "--output-text-file=",
            f"--workers={int(workers)}",
            f"--timeout={int(request_timeout)}",
            f"--max-visited-urls={int(max_urls)}",
        ]
        _run_cli(cmd, url)
        if not os.path.isfile(out_json):
            raise SiteOneError(f"SiteOne produced no JSON report for {url}")
        try:
            with open(out_json, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise SiteOneError(f"Could not read SiteOne report for {url}: {exc}") from exc
    return _parse(url, data)


def _run_cli(cmd: list[str], url: str) -> None:
    """Invoke the SiteOne CLI, translating launch/timeout errors to SiteOneError."""
    try:
        subprocess.run(cmd, capture_output=True, timeout=DEFAULT_RUN_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SiteOneError(f"SiteOne timed out after {DEFAULT_RUN_TIMEOUT}s for {url}") from exc
    except OSError as exc:
        raise SiteOneError(f"Failed to launch SiteOne for {url}: {exc}") from exc


def _parse(url: str, data: dict) -> SiteOneReport:
    """Adapt SiteOne's raw JSON into a :class:`SiteOneReport` (tolerant of gaps)."""
    quality = data.get("qualityScores") or {}
    categories = [_category(c) for c in quality.get("categories", []) if isinstance(c, dict)]
    items = [i for i in (data.get("summary") or {}).get("items", []) if isinstance(i, dict)]
    by_sev: dict[str, int] = {}
    for it in items:
        sev = str(it.get("status", "")).upper()
        by_sev[sev] = by_sev.get(sev, 0) + 1
    tables = data.get("tables") or {}
    stats = data.get("stats") or {}
    return SiteOneReport(
        url=url,
        overall_score=_overall(quality.get("overall")),
        categories=categories,
        summary_by_severity=by_sev,
        summary_items=items,
        broken_links=_rows(tables.get("404")),
        redirects=_rows(tables.get("redirects")),
        security_findings=_rows(tables.get("security")),
        seo_pages=_rows(tables.get("seo")),
        total_urls=int(stats.get("totalUrls", 0) or 0),
        request_ms_avg=_ms(stats.get("totalRequestsTimesAvg")),
        request_ms_p90=_ms(stats.get("totalRequestsTimesP90")),
        count_by_status={str(k): int(v) for k, v in (stats.get("countByStatus") or {}).items()},
    )


def _category(raw: dict) -> CategoryScore:
    return CategoryScore(
        code=str(raw.get("code", "")),
        name=str(raw.get("name", "")),
        score=_num(raw.get("score")),
        weight=_num(raw.get("weight")),
        label=str(raw.get("label", "")),
        deductions=[
            Deduction(
                reason=str(d.get("reason", "")),
                fix=str(d.get("fix", "")),
                points=_num(d.get("points")),
            )
            for d in raw.get("deductions", [])
            if isinstance(d, dict)
        ],
    )


def _overall(value: object) -> float | None:
    """SiteOne's overall score — a number, a ``{"score": …}`` dict, or absent."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("score"), (int, float)):
        return float(value["score"])
    return None


def _rows(table: object) -> list[dict]:
    """Extract the ``rows`` list from a SiteOne table dict (empty if none)."""
    if isinstance(table, dict) and isinstance(table.get("rows"), list):
        return [r for r in table["rows"] if isinstance(r, dict)]
    return []


def to_check_payload(report: SiteOneReport) -> dict:
    """Serialise a report into the ``details["checks"]["siteone"]`` payload.

    Pure data (JSON-serialisable) for the technical/content pillars to embed —
    category scores, each category's deductions (reason + fix), issue counts by
    severity, and crawl performance. Does not alter any numeric pillar score.
    """
    return {
        "overall_score": report.overall_score,
        "categories": [
            {
                "code": c.code,
                "name": c.name,
                "score": c.score,
                "weight": c.weight,
                "label": c.label,
                "deductions": [
                    {"reason": d.reason, "fix": d.fix, "points": d.points} for d in c.deductions
                ],
            }
            for c in report.categories
        ],
        "severity_counts": report.summary_by_severity,
        "counts": {
            "broken_links": len(report.broken_links),
            "redirects": len(report.redirects),
            "security_findings": len(report.security_findings),
            "pages_crawled": report.total_urls,
        },
        "performance": {
            "request_ms_avg": report.request_ms_avg,
            "request_ms_p90": report.request_ms_p90,
        },
    }


def _num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _ms(seconds: object) -> float:
    """Convert SiteOne's seconds float to rounded milliseconds."""
    return round(_num(seconds) * 1000.0, 1)
