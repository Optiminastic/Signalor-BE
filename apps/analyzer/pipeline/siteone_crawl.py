"""SiteOne Crawler integration - run the SiteOne CLI and parse its JSON report.

An optional *technical/SEO* data source for the analyzer. SiteOne runs a large
set of analyzers and emits per-category quality scores (performance / SEO /
security / accessibility / best-practices) plus ~27 detail tables (SEO metadata,
duplicate titles/descriptions, security headers, SSL/TLS, DNS, accessibility,
best practices, Open Graph, fastest/slowest URLs, caching, HTTP headers, content
types, 404s, redirects, external URLs, ...) and a flat list of scored findings.

We shell out to its CLI, capture the JSON report (``--output-json-file``), and
adapt the *whole* thing into a typed :class:`SiteOneReport`: the category scores,
every detail table (generically, as columns + rows, so new SiteOne tables are
captured automatically), the findings list, and crawl stats. Nothing SiteOne
produces is dropped.

Gated behind two conditions so production is untouched until explicitly enabled:
  * ``SIGNALOR_USE_SITEONE`` truthy (default off), and
  * a resolvable binary - ``SITEONE_CRAWLER_BIN`` (absolute path) or
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

# SiteOne CLI knobs (kept small - this runs inside a user-facing analysis).
DEFAULT_MAX_URLS = 30
DEFAULT_WORKERS = 3
DEFAULT_REQUEST_TIMEOUT = 8  # per-request seconds (SiteOne --timeout)
DEFAULT_RUN_TIMEOUT = 180  # overall subprocess budget, seconds
# Defensive cap so a pathological table (e.g. thousands of URLs) can't bloat the
# stored payload. No real SiteOne table on a small crawl approaches this.
MAX_ROWS_PER_TABLE = 250

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
class Column:
    """A detail-table column: the row key (``field``) and its display ``label``."""

    field: str
    label: str


@dataclass
class DetailTable:
    """One SiteOne analyzer table (columns + rows), captured generically."""

    key: str  # e.g. "seo", "security", "certificate-info"
    title: str  # human title, e.g. "SEO metadata"
    position: str  # SiteOne's position hint (int-like or a keyword)
    columns: list[Column]
    rows: list[dict]


@dataclass
class Finding:
    """One scored finding from SiteOne's summary (severity + message)."""

    code: str
    status: str  # CRITICAL | WARNING | NOTICE | OK | INFO
    text: str


@dataclass
class SiteOneReport:
    """Parsed, scorer-friendly view of a SiteOne JSON report (full detail)."""

    url: str
    overall_score: float | None  # 0-10 when present
    categories: list[CategoryScore]
    findings: list[Finding]  # summary items, one per scored check
    summary_by_severity: dict[str, int]  # CRITICAL/WARNING/NOTICE/OK/INFO -> count
    tables: list[DetailTable]  # every SiteOne analyzer table
    total_urls: int
    total_size: int
    total_size_formatted: str
    execution_time_s: float
    request_ms_avg: float
    request_ms_p90: float
    request_ms_max: float
    count_by_status: dict[str, int]

    def category(self, code: str) -> CategoryScore | None:
        """Return the category with ``code`` (e.g. ``"security"``) or ``None``."""
        return next((c for c in self.categories if c.code == code), None)

    def table(self, key: str) -> DetailTable | None:
        """Return the detail table with ``key`` (e.g. ``"seo"``) or ``None``."""
        return next((t for t in self.tables if t.key == key), None)

    def table_row_count(self, key: str) -> int:
        """Number of rows in the table with ``key`` (0 if absent)."""
        tbl = self.table(key)
        return len(tbl.rows) if tbl else 0


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
            "SiteOne binary not found - set SITEONE_CRAWLER_BIN or add siteone-crawler to PATH."
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
    findings = _findings((data.get("summary") or {}).get("items", []))
    by_sev: dict[str, int] = {}
    for fnd in findings:
        by_sev[fnd.status] = by_sev.get(fnd.status, 0) + 1
    stats = data.get("stats") or {}
    return SiteOneReport(
        url=url,
        overall_score=_overall(quality.get("overall")),
        categories=categories,
        findings=findings,
        summary_by_severity=by_sev,
        tables=_tables(data.get("tables") or {}),
        total_urls=int(stats.get("totalUrls", 0) or 0),
        total_size=int(stats.get("totalSize", 0) or 0),
        total_size_formatted=str(stats.get("totalSizeFormatted", "")),
        execution_time_s=_num(stats.get("totalExecutionTime")),
        request_ms_avg=_ms(stats.get("totalRequestsTimesAvg")),
        request_ms_p90=_ms(stats.get("totalRequestsTimesP90")),
        request_ms_max=_ms(stats.get("totalRequestsTimesMax")),
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


def _findings(items: object) -> list[Finding]:
    """Adapt ``summary.items`` into typed findings (drops malformed entries)."""
    if not isinstance(items, list):
        return []
    return [
        Finding(
            code=str(i.get("aplCode", "")),
            status=str(i.get("status", "")).upper(),
            text=str(i.get("text", "")),
        )
        for i in items
        if isinstance(i, dict)
    ]


def _tables(raw_tables: object) -> list[DetailTable]:
    """Adapt every SiteOne table generically (columns + rows).

    Preserves SiteOne's own table order (JSON insertion order); ``position`` is
    kept as a raw string hint since SiteOne uses both ints and keywords there.
    """
    if not isinstance(raw_tables, dict):
        return []
    out: list[DetailTable] = []
    for key, tbl in raw_tables.items():
        if not isinstance(tbl, dict):
            continue
        rows = tbl.get("rows")
        rows = rows if isinstance(rows, list) else []
        out.append(
            DetailTable(
                key=str(key),
                title=str(tbl.get("title") or key),
                position=str(tbl.get("position", "")),
                columns=_columns(tbl.get("columns")),
                rows=[r for r in rows[:MAX_ROWS_PER_TABLE] if isinstance(r, dict)],
            )
        )
    return out


def _columns(raw: object) -> list[Column]:
    """Extract ``{field, label}`` pairs from a SiteOne column map (order-preserving)."""
    if not isinstance(raw, dict):
        return []
    cols: list[Column] = []
    for fld, meta in raw.items():
        label = str(meta["name"]) if isinstance(meta, dict) and meta.get("name") else str(fld)
        cols.append(Column(field=str(fld), label=label))
    return cols


def _overall(value: object) -> float | None:
    """SiteOne's overall score - a number, a ``{"score": ...}`` dict, or absent."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("score"), (int, float)):
        return float(value["score"])
    return None


def to_check_payload(report: SiteOneReport) -> dict:
    """Serialise a report into the ``details["checks"]["siteone"]`` payload.

    Pure data (JSON-serialisable) for the technical/content pillars to embed -
    the full detail: category scores + deductions, the findings list, every
    analyzer table (columns + rows), issue counts, and crawl stats. Does not
    alter any numeric pillar score.
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
                "deductions": [{"reason": d.reason, "fix": d.fix, "points": d.points} for d in c.deductions],
            }
            for c in report.categories
        ],
        "findings": [{"code": f.code, "status": f.status, "text": f.text} for f in report.findings],
        "severity_counts": report.summary_by_severity,
        "tables": [
            {
                "key": t.key,
                "title": t.title,
                "columns": [{"field": c.field, "label": c.label} for c in t.columns],
                "rows": t.rows,
            }
            for t in report.tables
        ],
        "counts": {
            "broken_links": report.table_row_count("404"),
            "redirects": report.table_row_count("redirects"),
            "security_findings": report.table_row_count("security"),
            "pages_crawled": report.total_urls,
        },
        "stats": {
            "total_urls": report.total_urls,
            "total_size": report.total_size,
            "total_size_formatted": report.total_size_formatted,
            "execution_time_s": report.execution_time_s,
            "count_by_status": report.count_by_status,
        },
        "performance": {
            "request_ms_avg": report.request_ms_avg,
            "request_ms_p90": report.request_ms_p90,
            "request_ms_max": report.request_ms_max,
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
