"""Technical audits: sitemap, schema, rank, crawler access."""

from datetime import timedelta

import requests
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Organization
from core.permissions.throttling import (
    AuditStartThrottle,
    ExpensiveThrottle,
    PollingThrottle,
)

from ..models import (
    AnalysisRun,
    CrawlerHit,
)
from ..serializers import (
    IndexNowSubmitSerializer,
)
from ._shared import (
    CRAWL_CHECK_USER_AGENT,
    _crawler_ingest_signer,
    _evaluate_crawl_file,
    _normalize_origin,
    _resolve_crawl_site,
    _scoped_run,
    crawler_ingest_token,
    logger,
)


class CrawlEssentialsStatusView(APIView):
    """Get llms.txt/robots.txt/sitemap.xml status for Actions submenu."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]  # sidebar/actions open frequently

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        run_id_param = request.query_params.get("run_id")
        analyzed_url = request.query_params.get("analyzed_url", "").strip()
        run_id = None
        if run_id_param:
            try:
                run_id = int(run_id_param)
            except ValueError:
                run_id = None

        try:
            site_url, source = _resolve_crawl_site(email, run_id, analyzed_url)
        except Exception:
            # Never fail hard for this diagnostics endpoint; use URL fallback.
            logger.exception("Crawl essentials: unexpected site-resolution error.")
            site_url = _normalize_origin(analyzed_url)
            source = "analyzed_url" if site_url else "unknown"

        if not site_url:
            return Response(
                {"error": "Could not resolve site URL from integrations or analysis run."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        checks = [
            ("llms", "llms.txt", "/llms.txt"),
            ("robots", "robots.txt", "/robots.txt"),
            ("sitemap", "sitemap.xml", "/sitemap.xml"),
        ]

        files = []
        for key, label, path in checks:
            target_url = f"{site_url}{path}"
            try:
                resp = requests.get(
                    target_url,
                    headers={"User-Agent": CRAWL_CHECK_USER_AGENT},
                    timeout=8,
                    allow_redirects=True,
                )
                files.append(
                    _evaluate_crawl_file(
                        key=key,
                        label=label,
                        target_url=target_url,
                        content=resp.text,
                        status_code=resp.status_code,
                    )
                )
            except requests.RequestException:
                files.append(
                    _evaluate_crawl_file(
                        key=key,
                        label=label,
                        target_url=target_url,
                        content="",
                        status_code=None,
                    )
                )

        overall_score = round(sum(item["score"] for item in files) / len(files), 1) if files else 0.0

        return Response(
            {
                "submenu_key": "ai-crawl-essentials",
                "submenu_name": "AI Crawl Essentials",
                "site_url": site_url,
                "source": source,
                "overall_score": overall_score,
                "files": files,
            }
        )

class SitemapAuditStartView(APIView):
    """POST /runs/s/<slug>/sitemap/  — kick off an async sitemap audit."""

    permission_classes = [AllowAny]
    throttle_classes = [AuditStartThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from core import queue

        from ..models import SitemapAudit
        from ..pipeline.sitemap_audit import HARD_URL_CAP
        from ..serializers import SitemapAuditSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = SitemapAudit.objects.create(
            analysis_run=run,
            status=SitemapAudit.Status.QUEUED,
            crawl_limit=HARD_URL_CAP,
        )
        queue.send(queue.SITEMAP_AUDIT, audit.id, broker="default")

        return Response(
            SitemapAuditSerializer(audit).data,
            status=status.HTTP_202_ACCEPTED,
        )

class SitemapAuditDetailView(APIView):
    """GET /runs/s/<slug>/sitemap/  — latest audit summary + paginated pages."""

    permission_classes = [AllowAny]

    ALLOWED_SORTS = {
        "url": "url",
        "-url": "-url",
        "status": "status_code",
        "-status": "-status_code",
        "ai_score": "ai_score",
        "-ai_score": "-ai_score",
        "words": "word_count",
        "-words": "-word_count",
        "lcp": "lcp_ms",
        "-lcp": "-lcp_ms",
        "fcp": "fcp_ms",
        "-fcp": "-fcp_ms",
        "ttfb": "ttfb_ms",
        "-ttfb": "-ttfb_ms",
    }

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import SitemapAudit
        from ..serializers import SitemapAuditPageSerializer, SitemapAuditSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = SitemapAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
        if audit is None:
            return Response({"audit": None, "pages": [], "total": 0})

        qs = audit.pages.all()
        state = request.GET.get("state")
        if state:
            qs = qs.filter(state=state)
        severity = request.GET.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        q = (request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(url__icontains=q)

        sort = request.GET.get("sort", "-ai_score")
        qs = qs.order_by(self.ALLOWED_SORTS.get(sort, "-ai_score"), "id")

        total = qs.count()
        try:
            page_size = min(max(int(request.GET.get("page_size", 50)), 1), 200)
            page = max(int(request.GET.get("page", 1)), 1)
        except ValueError:
            page_size, page = 50, 1
        start_idx = (page - 1) * page_size
        rows = list(qs[start_idx : start_idx + page_size])

        return Response(
            {
                "audit": SitemapAuditSerializer(audit).data,
                "pages": SitemapAuditPageSerializer(rows, many=True).data,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )

class SchemaWatchStartView(APIView):
    """POST /runs/s/<slug>/schema-watch/  — kick off a schema validation run."""

    permission_classes = [AllowAny]
    throttle_classes = [AuditStartThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import SchemaWatch
        from ..pipeline.schema_watch import run_schema_watch
        from ..serializers import SchemaWatchSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        watch = SchemaWatch.objects.create(
            analysis_run=run,
            status=SchemaWatch.Status.QUEUED,
        )

        from .._thread_safety import run_in_background_with_status

        run_in_background_with_status(
            model_cls=SchemaWatch,
            instance_id=watch.id,
            status_field="status",
            failure_value=SchemaWatch.Status.FAILED,
            work=lambda: run_schema_watch(watch.id),
            log_label="run_schema_watch",
        )

        return Response(
            SchemaWatchSerializer(watch).data,
            status=status.HTTP_202_ACCEPTED,
        )

class SchemaWatchDetailView(APIView):
    """GET /runs/s/<slug>/schema-watch/  — latest watch summary + pages."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import SchemaWatch
        from ..serializers import SchemaWatchPageSerializer, SchemaWatchSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        watch = SchemaWatch.objects.filter(analysis_run=run).order_by("-created_at").first()
        if watch is None:
            return Response({"watch": None, "pages": [], "total": 0})

        qs = watch.pages.all()
        severity = request.GET.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        kind = request.GET.get("kind")
        if kind:
            qs = qs.filter(page_kind=kind)
        q = (request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(url__icontains=q)

        # Sort fail first, then warn, then ok; within each, by URL
        qs = qs.extra(
            select={"_sev_rank": "CASE severity WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END"},
        ).order_by("_sev_rank", "url")

        total = qs.count()
        try:
            page_size = min(max(int(request.GET.get("page_size", 100)), 1), 200)
            page = max(int(request.GET.get("page", 1)), 1)
        except ValueError:
            page_size, page = 100, 1
        start_idx = (page - 1) * page_size
        rows = list(qs[start_idx : start_idx + page_size])

        return Response(
            {
                "watch": SchemaWatchSerializer(watch).data,
                "pages": SchemaWatchPageSerializer(rows, many=True).data,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )

class RankAuditStartView(APIView):
    """POST /runs/s/<slug>/rank/start/ — kick off an async rank audit."""

    permission_classes = [AllowAny]
    throttle_classes = [AuditStartThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import RankAudit
        from ..pipeline.rank_tracker import run_rank_audit
        from ..serializers import RankAuditSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)

        already = RankAudit.objects.filter(
            analysis_run=run, status__in=[RankAudit.Status.QUEUED, RankAudit.Status.RUNNING]
        ).first()
        if already is not None:
            return Response(
                {
                    "detail": "An audit is already running for this run.",
                    "audit": RankAuditSerializer(already).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

        audit = RankAudit.objects.create(
            analysis_run=run,
            status=RankAudit.Status.QUEUED,
        )

        from .._thread_safety import run_in_background_with_status

        run_in_background_with_status(
            model_cls=RankAudit,
            instance_id=audit.id,
            status_field="status",
            failure_value=RankAudit.Status.FAILED,
            work=lambda: run_rank_audit(audit.id),
            log_label="run_rank_audit",
        )

        return Response(
            RankAuditSerializer(audit).data,
            status=status.HTTP_202_ACCEPTED,
        )

class RankAuditDetailView(APIView):
    """GET /runs/s/<slug>/rank/ — latest audit summary + queries + their results."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import RankAudit
        from ..serializers import RankAuditSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = RankAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
        if audit is None:
            return Response({"audit": None, "queries": []})

        queries_qs = audit.queries.all().prefetch_related("results")

        surface = request.GET.get("surface")
        query_id = request.GET.get("query_id")
        q_substr = (request.GET.get("q") or "").strip()
        only_brand = request.GET.get("only_brand") in ("1", "true", "True")

        if query_id:
            try:
                queries_qs = queries_qs.filter(id=int(query_id))
            except (TypeError, ValueError):
                pass

        if q_substr:
            queries_qs = queries_qs.filter(prompt_text__icontains=q_substr)

        queries = list(queries_qs.order_by("rank", "id"))

        for q in queries:
            results = list(q.results.all())
            if surface:
                results = [r for r in results if r.surface == surface]
            if only_brand:
                results = [r for r in results if r.is_brand_mentioned]
            results.sort(key=lambda r: (r.surface, r.position))
            q._prefetched_results = results  # type: ignore[attr-defined]

        serialized = []
        for q in queries:
            payload = {
                "id": q.id,
                "prompt_text": q.prompt_text,
                "rank": q.rank,
                "brand_mention_count": q.brand_mention_count,
                "status": q.status,
                "error_message": q.error_message,
                "results": [
                    {
                        "id": r.id,
                        "surface": r.surface,
                        "position": r.position,
                        "url": r.url,
                        "domain": r.domain,
                        "title": r.title,
                        "snippet": r.snippet,
                        "engine": r.engine,
                        "response_text": r.response_text,
                        "sentiment": r.sentiment,
                        "is_brand_mentioned": r.is_brand_mentioned,
                        "competitors_mentioned": r.competitors_mentioned,
                        "upvotes": r.upvotes,
                        "subreddit": r.subreddit,
                        "checked_at": r.checked_at.isoformat() if r.checked_at else None,
                    }
                    for r in getattr(q, "_prefetched_results", q.results.all())
                ],
            }
            serialized.append(payload)

        return Response(
            {
                "audit": RankAuditSerializer(audit).data,
                "queries": serialized,
            }
        )

class RankAuditRefreshQueryView(APIView):
    """POST /runs/s/<slug>/rank/query/<query_id>/refresh/ — re-fetch one query across all surfaces."""

    permission_classes = [AllowAny]
    throttle_classes = [AuditStartThrottle]

    def post(self, request, slug, query_id):
        from django.shortcuts import get_object_or_404

        from ..models import RankAudit, RankQuery, RankResult
        from ..pipeline.rank_tracker import audit_query
        from ..serializers import RankQuerySerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = RankAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
        if audit is None:
            return Response({"detail": "No audit exists for this run."}, status=status.HTTP_404_NOT_FOUND)

        query = get_object_or_404(RankQuery, id=query_id, audit=audit)

        RankResult.objects.filter(query=query).delete()
        query.status = RankQuery.Status.QUEUED
        query.brand_mention_count = 0
        query.error_message = ""
        query.save(update_fields=["status", "brand_mention_count", "error_message"])

        from urllib.parse import urlparse as _urlparse

        from ..pipeline.rank_tracker import _derive_geo

        brand_names = [n for n in (run.brand_name,) if n]
        try:
            brand_domain = _urlparse(run.url or "").netloc.lower().replace("www.", "")
        except Exception:
            brand_domain = ""
        if brand_domain:
            brand_names.append(brand_domain)
        try:
            competitor_names = [c for c in run.competitors.values_list("name", flat=True) if c]
        except Exception:
            competitor_names = []
        gl = _derive_geo(run).get("gl") or ""

        from .._thread_safety import run_in_background_with_status
        from ..models import RankQuery as _RQ

        def _refresh():
            q = _RQ.objects.get(pk=query.id)
            audit_query(q, brand_names, competitor_names, brand_domain=brand_domain, gl=gl)

        run_in_background_with_status(
            model_cls=_RQ,
            instance_id=query.id,
            status_field="status",
            failure_value=_RQ.Status.FAILED,
            work=_refresh,
            log_label="refresh_rank_query",
        )

        return Response(RankQuerySerializer(query).data, status=status.HTTP_202_ACCEPTED)

class CrawlerIngestView(APIView):
    """POST /api/analyzer/crawler/ingest/ — receive AI-crawler hits from a site.

    Authenticated by the org-scoped signed token shown on the Crawler Logs
    page. The bot identity is always re-derived server-side from the user
    agent; requests that don't match a known AI crawler are ignored, so the
    endpoint never stores anything about human visitors.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    _MAX_BATCH = 500

    @staticmethod
    def _rows(org_id: int, hits: list) -> tuple[list, int]:
        from django.utils.dateparse import parse_datetime

        from ..crawler_bots import detect_bot

        now = timezone.now()
        rows: list[CrawlerHit] = []
        ignored = 0
        for h in hits:
            ua = str(h.get("user_agent") or "")[:300] if isinstance(h, dict) else ""
            bot = detect_bot(ua)
            if not bot:
                ignored += 1
                continue
            ts = parse_datetime(str(h.get("ts"))) if h.get("ts") else None
            if ts is not None and ts.tzinfo is None:
                ts = None  # naive client clocks are not trusted; stamp server time
            rows.append(
                CrawlerHit(
                    organization_id=org_id,
                    bot=bot,
                    path=str(h.get("path") or "/")[:512],
                    user_agent=ua,
                    hit_at=ts or now,
                )
            )
        return rows, ignored

    def post(self, request):
        from django.core import signing

        token = str(request.data.get("token") or "")
        try:
            org_id = int(_crawler_ingest_signer().unsign(token))
        except (signing.BadSignature, ValueError):
            return Response({"detail": "Invalid ingest token."}, status=status.HTTP_403_FORBIDDEN)
        if not Organization.objects.filter(id=org_id).exists():
            return Response({"detail": "Unknown organization."}, status=status.HTTP_403_FORBIDDEN)

        hits = request.data.get("hits")
        if not isinstance(hits, list) or not hits:
            return Response(
                {"detail": "hits must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST
            )

        rows, ignored = self._rows(org_id, hits[: self._MAX_BATCH])
        CrawlerHit.objects.bulk_create(rows)
        return Response({"stored": len(rows), "ignored": ignored})

class IndexNowView(APIView):
    """GET/POST /runs/s/<slug>/indexnow/ — push pages into Bing's index.

    GET returns the key, where to host it, and whether it is currently
    reachable. POST submits the run's pages.

    POST rather than GET for the submission because it changes external state
    and is rate-limited by the receiving engines.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        from ..services.indexnow import setup_for_run

        # The response carries the org's IndexNow key, which is a submission
        # capability for that host: anyone holding it can push URLs to
        # Bing/Yandex/Seznam/Naver as the customer.
        run, err = _scoped_run(request, slug)
        if err is not None:
            return err
        return Response(setup_for_run(run))

    def post(self, request, slug):
        from ..services.indexnow import submit_run_pages

        # Pushes URLs to an external index under the customer's key, and the
        # engines rate-limit that key itself.
        run, err = _scoped_run(request, slug)
        if err is not None:
            return err
        body = IndexNowSubmitSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(submit_run_pages(run, body.validated_data.get("urls") or None))

class CrawlerAccessView(APIView):
    """GET /runs/s/<slug>/crawler-access/ — can AI engines crawl this site?

    Joins robots.txt policy with observed CrawlerHit telemetry and answers it per
    engine. Distinct from ``crawler-logs``, which reports raw activity: this
    reports the *verdict* (blocked / never crawled / stale / active / unknown)
    and why it matters.

    robots.txt is fetched live rather than read from the run, so a customer who
    fixes their configuration sees it reflected on refresh instead of waiting for
    the next analysis.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from ..services.crawler_access import report_for_run

        run, err = _scoped_run(request, slug)
        if err is not None:
            return err
        if run.organization is None:
            return Response(
                {"detail": "Run has no organization."}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(report_for_run(run))

class CrawlerLogsView(APIView):
    """GET /runs/s/<slug>/crawler-logs/ — AI crawler activity for the brand:
    daily per-bot counts (last 30 days), top crawlers, top pages, and the
    ingest credentials for wiring the site up."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from collections import defaultdict

        from django.db.models import Count
        from django.db.models.functions import TruncDate
        from django.shortcuts import get_object_or_404
        from django.utils import timezone

        from ..crawler_bots import BOT_LABELS

        run = get_object_or_404(AnalysisRun, slug=slug)
        org = run.organization
        if org is None:
            return Response(
                {"detail": "Run has no organization."}, status=status.HTTP_400_BAD_REQUEST
            )

        since = timezone.now() - timedelta(days=30)
        qs = CrawlerHit.objects.filter(organization=org, hit_at__gte=since)

        by_day: dict[str, dict[str, int]] = defaultdict(dict)
        daily = qs.annotate(day=TruncDate("hit_at")).values("day", "bot").annotate(n=Count("id"))
        for row in daily:
            by_day[row["day"].isoformat()][row["bot"]] = row["n"]

        bots = [
            {"bot": r["bot"], "label": BOT_LABELS.get(r["bot"], r["bot"]), "hits": r["n"]}
            for r in qs.values("bot").annotate(n=Count("id")).order_by("-n")
        ]
        pages = [
            {"path": r["path"], "hits": r["n"]}
            for r in qs.values("path").annotate(n=Count("id")).order_by("-n")[:10]
        ]
        return Response(
            {
                "ingest_token": crawler_ingest_token(org.id),
                "total_hits": qs.count(),
                "days": [{"date": d, "bots": b} for d, b in sorted(by_day.items())],
                "bots": bots,
                "pages": pages,
            }
        )
