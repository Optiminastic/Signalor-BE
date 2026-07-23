"""
Management command: send weekly analytics emails to all users with completed runs.
Cron (production):  0 9 * * 5  /path/to/venv/python manage.py send_weekly_emails
"""
import datetime
import logging
import urllib.parse

from django.core.management.base import BaseCommand
from django.db.models import Case, IntegerField, Max, When

from apps.analyzer.email_utils import send_weekly_email
from apps.analyzer.models import AnalysisRun, SitemapAudit

logger = logging.getLogger("apps")


def _domain(url: str) -> str:
    try:
        u = url if url.startswith("http") else f"https://{url}"
        return urllib.parse.urlparse(u).netloc.removeprefix("www.")
    except Exception:
        return url


def _page_issue(page):
    sc = page.status_code or 0
    if sc >= 500:
        return ("FAIL", "Server Error", f"HTTP {sc} — page returning a server error")
    if sc >= 400:
        return ("FAIL", "Page Not Found", f"HTTP {sc} — page is unreachable")
    ai_blocked = (
        not page.robots_allows_gptbot
        and not page.robots_allows_claudebot
        and not page.robots_allows_perplexitybot
    )
    if ai_blocked:
        return ("FAIL", "AI Crawlers Blocked", "robots.txt blocks ChatGPT, Claude & Perplexity")
    if page.is_noindex:
        return ("FAIL", "Excluded from Search", "noindex prevents this page from search results")
    if page.ai_score is not None and page.ai_score < 30:
        return ("FAIL", "Critical AI Visibility Gap", f"AI score {page.ai_score}/100")
    if not page.jsonld_count:
        return ("WARN", "No Structured Data", "Missing JSON-LD schema")
    if not page.robots_allows_gptbot or not page.robots_allows_claudebot:
        return ("WARN", "Partial AI Crawl Block", "Some AI engines are blocked")
    if not page.has_canonical:
        return ("WARN", "Missing Canonical Tag", "No canonical URL declared")
    if not page.has_og:
        return ("WARN", "Missing Open Graph Tags", "OG tags absent")
    if page.ai_score is not None and page.ai_score < 60:
        return ("WARN", "Low AI Visibility Score", f"AI score {page.ai_score}/100")
    findings = page.findings if isinstance(page.findings, list) else []
    if findings:
        f = findings[0]
        return (
            (f.get("severity") or page.severity or "WARN").upper(),
            f.get("title") or f.get("name") or "Issue Detected",
            f.get("description") or f.get("message") or "Unresolved issue",
        )
    return ("WARN", "Low AI Visibility", f"AI score {page.ai_score or 0}/100")


def build_context_for_run(run) -> dict:
    competitors = [
        {
            "name": c.name or "",
            "url": c.url or "",
            "domain": _domain(c.url or ""),
            "composite_score": c.composite_score,
        }
        for c in run.competitors.order_by("-relevance_score")[:6]
    ]

    prompts = list(
        run.prompt_tracks.filter(deleted_at__isnull=True).order_by("-score")[:5]
    )

    recommendations = list(
        run.recommendations.filter(priority__in=["critical", "high"]).order_by("priority")[:5]
    )

    brand_vis = getattr(run, "brand_visibility", None)

    sitemap = SitemapAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
    critical_pages = []
    if sitemap:
        severity_order = Case(
            When(severity="FAIL", then=0),
            When(severity="WARN", then=1),
            default=2,
            output_field=IntegerField(),
        )
        raw_pages = sitemap.pages.exclude(severity="OK").order_by(severity_order, "ai_score")[:5]
        for page in raw_pages:
            sev, title, _ = _page_issue(page)
            path = page.path or page.url or ""
            critical_pages.append({
                "severity": sev,
                "title": title,
                "description": "",
                "path": path if len(path) <= 65 else path[:62] + "...",
                "url": page.url or "",
            })

    return {
        "brand_name": run.brand_name or "",
        "url": run.url or "",
        "brand_domain": _domain(run.url or ""),
        "slug": run.slug,
        "score": round(run.composite_score or 0),
        "competitors": competitors,
        "prompts": prompts,
        "recommendations": recommendations,
        "brand_visibility": brand_vis,
        "critical_pages": critical_pages,
        "report_date": datetime.date.today().strftime("%B %d, %Y"),
    }


class Command(BaseCommand):
    help = "Send weekly analytics emails to all users with completed analysis runs"

    def handle(self, *args, **options):
        today = datetime.date.today()
        self.stdout.write(f"[send_weekly_emails] Starting — {today}")

        # Get the latest completed run per unique email
        latest_ids = (
            AnalysisRun.objects
            .filter(status="complete", email__isnull=False)
            .exclude(email="")
            .values("email")
            .annotate(latest_id=Max("id"))
            .values_list("latest_id", flat=True)
        )

        runs = AnalysisRun.objects.filter(id__in=latest_ids).select_related(
            "brand_visibility"
        )

        total = runs.count()
        sent = 0
        failed = 0

        self.stdout.write(f"  Found {total} users to email.")

        for run in runs:
            try:
                context = build_context_for_run(run)
                result = send_weekly_email(run.email, context)
                if result:
                    sent += 1
                    logger.info(
                        "Weekly email sent to %s (run=%s brand=%s)",
                        run.email, run.slug, run.brand_name,
                    )
                else:
                    failed += 1
                    logger.warning("Weekly email failed (no API key?) for %s", run.email)
            except Exception:
                failed += 1
                logger.exception("Weekly email error for %s (run=%s)", run.email, run.slug)

        self.stdout.write(
            self.style.SUCCESS(
                f"[send_weekly_emails] Done — {sent} sent, {failed} failed out of {total}"
            )
        )
