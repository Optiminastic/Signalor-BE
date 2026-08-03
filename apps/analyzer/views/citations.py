"""Who gets cited and how visibility trends over time."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.subscription_utils import (
    get_plan_limits,
    is_plan_limits_enforcement_enabled,
)
from core.permissions.throttling import (
    PollingThrottle,
)

from ..models import (
    AnalysisRun,
    PromptCitation,
    PromptResult,
)
from ..serializers import (
    AiRecommendationSummarySerializer,
    CitationTrendPointSerializer,
    ShareOfVoiceSerializer,
)
from ._shared import (
    _norm_domain,
    _scoped_run,
    _sentiment_case,
)


class ShareOfVoiceView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.db.models import Count, Q
        from django.shortcuts import get_object_or_404

        from core.cache.keys import cached_or_compute

        run = get_object_or_404(AnalysisRun, slug=slug)

        def _compute():
            em = (run.email or "").strip()
            valid_engine_keys = {e[0] for e in PromptResult.Engine.choices}
            if is_plan_limits_enforcement_enabled() and em:
                engines = [e for e in get_plan_limits(em)["engines"] if e in valid_engine_keys]
            else:
                engines = [e[0] for e in PromptResult.Engine.choices]
            # One aggregation query per engine -> engines * 2 round trips. Cached
            # for 10 min so the dashboard's first paint amortizes the work.
            data = []
            for engine in engines:
                qs = PromptResult.objects.filter(prompt_track__analysis_run=run, engine=engine)
                agg = qs.aggregate(
                    total=Count("id"),
                    mentioned=Count("id", filter=Q(brand_mentioned=True)),
                )
                total = agg["total"] or 0
                mentioned = agg["mentioned"] or 0
                sov_pct = round((mentioned / total * 100), 1) if total > 0 else 0.0
                data.append({"engine": engine, "total": total, "mentioned": mentioned, "sov_pct": sov_pct})
            return data

        data = cached_or_compute(f"sov:{slug}", 600, _compute)
        return Response(ShareOfVoiceSerializer(data, many=True).data)

class CitationTrendView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.db.models import Count, Q
        from django.db.models.functions import TruncWeek
        from django.shortcuts import get_object_or_404

        from core.cache.keys import cached_or_compute

        run = get_object_or_404(AnalysisRun, slug=slug)

        def _compute():
            em = (run.email or "").strip()
            valid_engine_keys = {e[0] for e in PromptResult.Engine.choices}
            if is_plan_limits_enforcement_enabled() and em:
                allowed = [e for e in get_plan_limits(em)["engines"] if e in valid_engine_keys]
            else:
                allowed = None

            base = PromptResult.objects.filter(prompt_track__analysis_run=run)
            if allowed is not None:
                base = base.filter(engine__in=allowed)
            qs = (
                base.annotate(week_start=TruncWeek("checked_at"))
                .values("week_start", "engine")
                .annotate(
                    total=Count("id"),
                    mentioned=Count("id", filter=Q(brand_mentioned=True)),
                )
                .order_by("week_start", "engine")
            )

            data = []
            for row in qs:
                total = row["total"]
                mentioned = row["mentioned"]
                data.append(
                    {
                        # Stored as ISO string so Redis serialization round-trips
                        # cleanly (date objects don't pickle through JSON cache).
                        "week_start": row["week_start"].date().isoformat() if row["week_start"] else None,
                        "engine": row["engine"],
                        "rate_pct": round((mentioned / total * 100), 1) if total > 0 else 0.0,
                    }
                )
            return data

        data = cached_or_compute(f"trend:{slug}", 300, _compute)
        return Response(CitationTrendPointSerializer(data, many=True).data)

class AiRecommendationSummaryView(APIView):
    """GET /runs/s/<slug>/ai-recommendation-summary/

    Aggregated answer to "how often does AI recommend this brand?" for the
    Overview citation card. Reads only existing PromptResult / PromptCitation
    rows — never fires a new AI call — so the response is honest and cheap.

    Three honest signals:
      mention_pct        — % of prompt responses that named the brand at all
      recommendation_pct — % that named the brand AND were positive sentiment
      citation_pct       — % that cited a URL on the brand's own domain
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.db.models import Count, Exists, OuterRef, Q
        from django.shortcuts import get_object_or_404

        from core.cache.keys import cached_or_compute

        from ..models import PromptCitation

        run = get_object_or_404(AnalysisRun, slug=slug)

        def _compute():
            em = (run.email or "").strip()
            valid_engine_keys = {e[0] for e in PromptResult.Engine.choices}
            if is_plan_limits_enforcement_enabled() and em:
                allowed = [e for e in get_plan_limits(em)["engines"] if e in valid_engine_keys]
            else:
                allowed = None

            base = PromptResult.objects.filter(
                prompt_track__analysis_run=run,
                prompt_track__deleted_at__isnull=True,
            )
            if allowed is not None:
                base = base.filter(engine__in=allowed)

            brand_cite_exists = PromptCitation.objects.filter(
                prompt_result=OuterRef("pk"),
                is_brand=True,
            )
            annotated = base.annotate(has_brand_citation=Exists(brand_cite_exists))

            totals = annotated.aggregate(
                total=Count("id"),
                mentioned=Count("id", filter=Q(brand_mentioned=True)),
                recommended=Count(
                    "id",
                    filter=Q(brand_mentioned=True, sentiment=PromptResult.Sentiment.POSITIVE),
                ),
                cited=Count("id", filter=Q(has_brand_citation=True)),
            )
            total = totals["total"] or 0
            mentioned = totals["mentioned"] or 0
            recommended = totals["recommended"] or 0
            cited = totals["cited"] or 0

            def _pct(n: int) -> float:
                return round((n / total * 100), 1) if total > 0 else 0.0

            per_engine_rows = (
                annotated.values("engine")
                .annotate(
                    total=Count("id"),
                    mentioned=Count("id", filter=Q(brand_mentioned=True)),
                    recommended=Count(
                        "id",
                        filter=Q(brand_mentioned=True, sentiment=PromptResult.Sentiment.POSITIVE),
                    ),
                    cited=Count("id", filter=Q(has_brand_citation=True)),
                )
                .order_by("engine")
            )
            per_engine = []
            for row in per_engine_rows:
                e_total = row["total"] or 0
                e_rec = row["recommended"] or 0
                per_engine.append(
                    {
                        "engine": row["engine"],
                        "total": e_total,
                        "mentioned": row["mentioned"] or 0,
                        "recommended": e_rec,
                        "cited": row["cited"] or 0,
                        "recommendation_pct": round((e_rec / e_total * 100), 1) if e_total > 0 else 0.0,
                    }
                )

            # Up to 6 sample positive quotes so the user can audit the score.
            sample_qs = (
                annotated.filter(brand_mentioned=True, sentiment=PromptResult.Sentiment.POSITIVE)
                .exclude(response_text="")
                .select_related("prompt_track")
                .order_by("-confidence", "-checked_at")[:6]
            )
            samples = [
                {
                    "engine": pr.engine,
                    "prompt": (pr.prompt_track.prompt_text or "")[:240],
                    "quote": (pr.response_text or "")[:400],
                    "sentiment": pr.sentiment,
                }
                for pr in sample_qs
            ]

            return {
                "total": total,
                "mentioned": mentioned,
                "recommended": recommended,
                "cited": cited,
                "mention_pct": _pct(mentioned),
                "recommendation_pct": _pct(recommended),
                "citation_pct": _pct(cited),
                "per_engine": per_engine,
                "samples": samples,
            }

        data = cached_or_compute(f"ai_rec_summary:{slug}", 600, _compute)
        return Response(AiRecommendationSummarySerializer(data).data)

class CitationSourcesView(APIView):
    """GET /runs/s/<slug>/citations/ — citation source roll-up per run.

    Returns `domains` (top-cited hosts with brand/rival flags), plus convenience
    buckets `your_pages` and `rival_pages` ranked by mention frequency, so the
    frontend can render "pages AI loves" without a second query.
    """

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from collections import defaultdict

        from django.db.models import Count, Q
        from django.shortcuts import get_object_or_404

        from core.cache.keys import cached_or_compute

        from ..models import PromptCitation

        run = get_object_or_404(AnalysisRun, slug=slug)

        def _compute():
            qs = PromptCitation.objects.filter(
                prompt_result__prompt_track__analysis_run=run,
                prompt_result__prompt_track__deleted_at__isnull=True,
            ).exclude(domain="")

            # One aggregate for the three count totals — was 3 round trips.
            counts = qs.aggregate(
                total=Count("id"),
                brand=Count("id", filter=Q(is_brand=True)),
                rival=Count("id", filter=Q(is_competitor=True)),
            )

            # Domain roll-up
            domain_rows = list(qs.values("domain").annotate(total=Count("id")).order_by("-total")[:40])
            top_domains = [r["domain"] for r in domain_rows]

            # Flags per domain (is_brand / is_competitor) — restricted to the
            # top-N we'll actually return so we don't iterate all citations.
            flag_map: dict[str, dict] = {}
            if top_domains:
                for c in qs.filter(domain__in=top_domains).values("domain", "is_brand", "is_competitor"):
                    f = flag_map.setdefault(c["domain"], {"is_brand": False, "is_competitor": False})
                    if c["is_brand"]:
                        f["is_brand"] = True
                    if c["is_competitor"]:
                        f["is_competitor"] = True

            # Per-engine breakdown for top domains
            by_engine: dict[str, dict] = defaultdict(dict)
            if top_domains:
                engine_rows = (
                    qs.filter(domain__in=top_domains)
                    .values("domain", "prompt_result__engine")
                    .annotate(total=Count("id"))
                )
                for r in engine_rows:
                    by_engine[r["domain"]][r["prompt_result__engine"]] = r["total"]

            # Sample URL for each top domain
            sample_map: dict[str, str] = {}
            if top_domains:
                for c in qs.filter(domain__in=top_domains).values("domain", "url")[:500]:
                    sample_map.setdefault(c["domain"], c["url"])

            domains = []
            for row in domain_rows:
                d = row["domain"]
                flags = flag_map.get(d, {"is_brand": False, "is_competitor": False})
                domains.append(
                    {
                        "domain": d,
                        "total": row["total"],
                        "is_brand": flags["is_brand"],
                        "is_competitor": flags["is_competitor"],
                        "by_engine": dict(by_engine.get(d, {})),
                        "sample_url": sample_map.get(d, ""),
                    }
                )

            your_pages = list(
                qs.filter(is_brand=True)
                .values("url", "title")
                .annotate(mentions=Count("id"))
                .order_by("-mentions")[:10]
            )
            rival_pages = list(
                qs.filter(is_competitor=True)
                .values("url", "title", "domain")
                .annotate(mentions=Count("id"))
                .order_by("-mentions")[:10]
            )

            return {
                "total_citations": counts["total"] or 0,
                "brand_citations": counts["brand"] or 0,
                "competitor_citations": counts["rival"] or 0,
                "domains": domains,
                "your_pages": your_pages,
                "rival_pages": rival_pages,
            }

        return Response(cached_or_compute(f"cite:{slug}", 600, _compute))

class VisibilitySeriesView(APIView):
    """GET /runs/s/<slug>/visibility-series/?days=30

    Daily composite-score series for the dashboard header. Draws on the same
    source as ScoreHistoryView — the brand's own *completed* runs (scoped by
    organization when present, else by email) — restricted to the last `days`.
    Each run contributes one point (date = when the run finalized, score =
    composite). No interpolation: one run → one point.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)

        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        if days <= 0:
            days = 30

        cutoff = timezone.now() - timedelta(days=days)
        if run.organization_id:
            qs = AnalysisRun.objects.filter(organization_id=run.organization_id, status="complete")
        elif run.email:
            qs = AnalysisRun.objects.filter(email=run.email, status="complete")
        else:
            qs = AnalysisRun.objects.filter(pk=run.pk, status="complete")

        qs = qs.filter(updated_at__gte=cutoff).order_by("updated_at").values("updated_at", "composite_score")

        points = [
            {"date": row["updated_at"].date().isoformat(), "score": round(row["composite_score"] or 0)}
            for row in qs
        ]

        if points:
            current = points[-1]["score"]
            previous = points[-2]["score"] if len(points) >= 2 else current
        else:
            current = 0
            previous = 0

        delta_pct = round((current - previous) / previous * 100, 1) if previous else 0.0
        direction = "down" if current < previous else "up"

        return Response(
            {
                "points": points,
                "current": current,
                "previous": previous,
                "delta_pct": delta_pct,
                "direction": direction,
            }
        )

class RankingsView(APIView):
    """GET /runs/s/<slug>/rankings/

    One row per competitor plus one row for the brand (is_you=true), ranked by a
    real 0-100 visibility metric (run composite / brand-visibility overall for
    the brand; competitor.composite_score → relevance_score for rivals).
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    _PALETTE = [
        "#7c3aed",
        "#2563eb",
        "#059669",
        "#d97706",
        "#dc2626",
        "#0891b2",
        "#db2777",
        "#65a30d",
    ]

    def get(self, request, slug):
        from django.db.models import Avg
        from django.shortcuts import get_object_or_404

        from apps.analyzer.pipeline.brand_naming import visibility_brand_label

        run = get_object_or_404(
            AnalysisRun.objects.select_related("brand_visibility").prefetch_related("competitors"),
            slug=slug,
        )

        # Brand visibility (0-100)
        brand_vis = getattr(run, "brand_visibility", None)
        brand_visibility = run.composite_score
        if brand_visibility is None and brand_vis is not None:
            brand_visibility = brand_vis.overall_score
        brand_visibility = round(brand_visibility or 0)

        brand_results = PromptResult.objects.filter(
            prompt_track__analysis_run=run,
            prompt_track__deleted_at__isnull=True,
        )

        # Brand sentiment (0-100) over responses that actually named the brand.
        sent_avg = brand_results.filter(brand_mentioned=True).aggregate(avg=Avg(_sentiment_case()))["avg"]
        brand_sentiment = round((sent_avg + 1) / 2 * 100) if sent_avg is not None else None

        # Brand avg position (compute_prompt_score style: inverse-position mean).
        positions = list(
            brand_results.filter(brand_mentioned=True, rank_position__gt=0).values_list(
                "rank_position", flat=True
            )
        )
        brand_avg_position = None
        if positions:
            avg_inv = sum(1.0 / p for p in positions) / len(positions)
            if avg_inv > 0:
                brand_avg_position = f"#{round(1.0 / avg_inv, 1)}"

        brand_company = (
            visibility_brand_label(run.url or "", run.brand_name or "") or run.brand_name or run.url
        )

        # Which AI engines each competitor domain is cited in / the brand is
        # mentioned in — powers the "Models" column (where they actually rank).
        from collections import defaultdict

        from ..pipeline.utils import extract_domain

        eng_by_domain: dict[str, set] = defaultdict(set)
        for cr in (
            PromptCitation.objects.filter(
                prompt_result__prompt_track__analysis_run=run,
                prompt_result__prompt_track__deleted_at__isnull=True,
            )
            .exclude(domain="")
            .values("domain", "prompt_result__engine")
        ):
            eng_by_domain[_norm_domain(cr["domain"])].add(cr["prompt_result__engine"])

        brand_engines = sorted(
            brand_results.filter(brand_mentioned=True).values_list("engine", flat=True).distinct()
        )

        raw_rows = []
        brand_row = {
            "company": brand_company,
            "visibility": brand_visibility,
            "avg_position": brand_avg_position,
            "is_you": True,
            "domain": _norm_domain(run.url or ""),
            "engines": brand_engines,
        }
        if brand_sentiment is not None:
            brand_row["sentiment"] = brand_sentiment
        raw_rows.append(brand_row)

        for c in run.competitors.all():
            vis = c.composite_score
            if vis is None:
                vis = c.relevance_score
            raw_rows.append(
                {
                    "company": c.name or _norm_domain(c.url) or "Competitor",
                    "visibility": round(vis or 0),
                    "avg_position": None,
                    "is_you": False,
                    "domain": _norm_domain(c.url or ""),
                    "engines": sorted(eng_by_domain.get(_norm_domain(extract_domain(c.url or "")), set())),
                }
            )

        raw_rows.sort(key=lambda r: r["visibility"], reverse=True)

        rows = []
        your_rank = None
        for i, r in enumerate(raw_rows):
            rank = i + 1
            row = {
                "rank": rank,
                "company": r["company"],
                "visibility": r["visibility"],
                "avg_position": r["avg_position"] or f"#{rank}",
                "is_you": r["is_you"],
                "color": self._PALETTE[i % len(self._PALETTE)],
                "domain": r.get("domain") or "",
                "engines": r.get("engines") or [],
            }
            if "sentiment" in r:
                row["sentiment"] = r["sentiment"]
            if r["is_you"]:
                your_rank = rank
            rows.append(row)

        return Response({"your_rank": your_rank, "rows": rows})

class ShareOfVoiceCompetitorsView(APIView):
    """GET /runs/s/<slug>/share-of-voice-competitors/

    Brand share of voice (single %, same mention-rate basis as ShareOfVoiceView)
    plus per-competitor share of the competitor citation pie, matched by the
    competitor's domain against PromptCitation rows.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from collections import defaultdict

        from django.db.models import Count, Q
        from django.shortcuts import get_object_or_404

        from ..pipeline.utils import extract_domain

        run = get_object_or_404(AnalysisRun.objects.prefetch_related("competitors"), slug=slug)

        # Brand SoV: mentioned / total across all prompt responses.
        agg = PromptResult.objects.filter(
            prompt_track__analysis_run=run,
            prompt_track__deleted_at__isnull=True,
        ).aggregate(total=Count("id"), mentioned=Count("id", filter=Q(brand_mentioned=True)))
        total = agg["total"] or 0
        value = round((agg["mentioned"] or 0) / total * 100, 1) if total else 0.0

        # Per-competitor SoV: fraction of responses that cite the competitor's
        # domain — the SAME mention-rate basis as the brand headline, so the bars
        # are directly comparable (a lone-cited competitor reads its true rate,
        # e.g. 1%, not 100% of a one-mention pie).
        cite_rows = (
            PromptCitation.objects.filter(
                prompt_result__prompt_track__analysis_run=run,
                prompt_result__prompt_track__deleted_at__isnull=True,
            )
            .exclude(domain="")
            .values("domain", "prompt_result_id")
        )
        resp_by_domain: dict[str, set] = defaultdict(set)
        for r in cite_rows:
            resp_by_domain[_norm_domain(r["domain"])].add(r["prompt_result_id"])

        pairs = []
        for c in run.competitors.all():
            cdom = _norm_domain(extract_domain(c.url or ""))
            n = len(resp_by_domain.get(cdom, ())) if cdom else 0
            pairs.append((c.name or cdom or "Competitor", n))

        competitors = [
            {"name": name, "value": round(n / total * 100, 1) if total else 0.0} for name, n in pairs
        ]
        competitors.sort(key=lambda x: x["value"], reverse=True)

        # No prior period is computable from a single run's data.
        return Response(
            {
                "value": value,
                "delta_pct": 0.0,
                "direction": "up",
                "competitors": competitors,
            }
        )

class CitationGapsView(APIView):
    """GET/PATCH /runs/s/<slug>/citation-gaps/ — the domains cited instead of you.

    GET returns the ranked outreach list. PATCH records outreach state for one
    domain (``{"domain": ..., "status": ..., "note": ...}``).

    ``live`` cannot be PATCHed: it is derived from a presence check, so the
    pipeline stays honest as it ages instead of reflecting stale checkboxes.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from ..services.citation_gaps import report_for_run

        run, err = _scoped_run(request, slug)
        if err is not None:
            return err
        # Verification costs one search per domain; let a caller skip it when
        # only the ranking is needed.
        verify = request.query_params.get("verify", "1") not in {"0", "false", "no"}
        return Response(report_for_run(run, verify=verify))

    def patch(self, request, slug):
        from ..services.citation_gaps import set_status

        # Durably mutates CitationOutreach rows.
        run, err = _scoped_run(request, slug)
        if err is not None:
            return err
        if run.organization is None:
            return Response(
                {"detail": "Run has no organization."}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            return Response(
                set_status(
                    run.organization,
                    request.data.get("domain", ""),
                    request.data.get("status", ""),
                    request.data.get("note", "") or "",
                )
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

class CompetitorVisibilityMatrixView(APIView):
    """GET /runs/s/<slug>/competitor-visibility-matrix/

    Heatmap data: brand + competitors as rows, AI engines as columns. The brand
    cell is its per-engine mention rate; a competitor cell is the share of that
    engine's responses citing the competitor's domain — the same mention-rate
    basis, so cells are directly comparable across rows.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    @staticmethod
    def _engine_stats(run):
        """Per-engine response totals, brand hits, and result-id → engine map."""
        from collections import defaultdict

        totals: dict[str, int] = defaultdict(int)
        brand_hits: dict[str, int] = defaultdict(int)
        engine_of: dict[int, str] = {}
        rows = PromptResult.objects.filter(
            prompt_track__analysis_run=run,
            prompt_track__deleted_at__isnull=True,
        ).values("id", "engine", "brand_mentioned")
        for r in rows:
            eng = (r["engine"] or "").strip().lower()
            if not eng:
                continue
            totals[eng] += 1
            engine_of[r["id"]] = eng
            if r["brand_mentioned"]:
                brand_hits[eng] += 1
        return totals, brand_hits, engine_of

    @staticmethod
    def _citing_results(run, engine_of):
        """domain → engine → set of citing result ids."""
        from collections import defaultdict

        by_domain: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
        cite_rows = (
            PromptCitation.objects.filter(
                prompt_result__prompt_track__analysis_run=run,
                prompt_result__prompt_track__deleted_at__isnull=True,
            )
            .exclude(domain="")
            .values("domain", "prompt_result_id")
        )
        for r in cite_rows:
            eng = engine_of.get(r["prompt_result_id"])
            if eng:
                by_domain[_norm_domain(r["domain"])][eng].add(r["prompt_result_id"])
        return by_domain

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..pipeline.utils import extract_domain

        run = get_object_or_404(AnalysisRun.objects.prefetch_related("competitors"), slug=slug)
        totals, brand_hits, engine_of = self._engine_stats(run)
        citing = self._citing_results(run, engine_of)
        engines = sorted(totals.keys())

        def pct(hits: int, eng: str) -> float:
            total = totals.get(eng) or 0
            return round(hits / total * 100, 1) if total else 0.0

        rows = [
            {
                "name": run.brand_name or run.url,
                "domain": _norm_domain(extract_domain(run.url or "")),
                "is_brand": True,
                "cells": {eng: pct(brand_hits.get(eng, 0), eng) for eng in engines},
            }
        ]
        for c in run.competitors.all():
            cdom = _norm_domain(extract_domain(c.url or ""))
            by_eng = citing.get(cdom, {})
            rows.append(
                {
                    "name": c.name or cdom or "Competitor",
                    "domain": cdom,
                    "is_brand": False,
                    "cells": {eng: pct(len(by_eng.get(eng, ())), eng) for eng in engines},
                }
            )

        return Response({"engines": engines, "rows": rows})

class TopSourcesView(APIView):
    """GET /runs/s/<slug>/top-sources/

    One row per AI engine that has data: brand mention count, average response
    sentiment (0-100), relative impact (thirds of max mentions), and a weekly
    mention-rate sparkline (reuses CitationTrendView's weekly grouping).
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    _ENGINE_LABELS = {
        "chatgpt": "ChatGPT",
        "claude": "Claude",
        "gemini": "Gemini",
        "perplexity": "Perplexity",
        "google": "Google AI Overview",
        "bing": "Bing",
    }

    def get(self, request, slug):
        from collections import defaultdict

        from django.db.models import Avg, Count, Q
        from django.db.models.functions import TruncWeek
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)

        base = PromptResult.objects.filter(
            prompt_track__analysis_run=run,
            prompt_track__deleted_at__isnull=True,
        )

        engine_rows = [
            r
            for r in base.values("engine").annotate(
                total=Count("id"),
                mentions=Count("id", filter=Q(brand_mentioned=True)),
                sent=Avg(_sentiment_case()),
            )
            if r["total"]
        ]

        # Weekly mention-rate sparkline per engine (chronological).
        weekly = (
            base.annotate(week=TruncWeek("checked_at"))
            .values("engine", "week")
            .annotate(total=Count("id"), mentioned=Count("id", filter=Q(brand_mentioned=True)))
            .order_by("week")
        )
        spark_map: dict[str, list] = defaultdict(list)
        for w in weekly:
            rate = round((w["mentioned"] or 0) / w["total"] * 100) if w["total"] else 0
            spark_map[w["engine"]].append(rate)

        max_mentions = max((r["mentions"] for r in engine_rows), default=0)

        def _impact(m: int) -> str:
            if max_mentions <= 0:
                return "low"
            ratio = m / max_mentions
            if ratio > 2 / 3:
                return "high"
            if ratio > 1 / 3:
                return "medium"
            return "low"

        sources = []
        for r in sorted(engine_rows, key=lambda x: x["mentions"], reverse=True):
            eng = r["engine"]
            src = {
                "name": self._ENGINE_LABELS.get(eng, eng.title()),
                "engine": eng,
                "mentions": r["mentions"],
                "impact": _impact(r["mentions"]),
                "spark": spark_map.get(eng, [])[-7:],
            }
            if r["sent"] is not None:
                src["sentiment"] = round((r["sent"] + 1) / 2 * 100)
            sources.append(src)

        return Response({"sources": sources})
