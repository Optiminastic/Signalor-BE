"""Tracked GEO prompts: generation, firing, results, coverage."""

from django.db import close_old_connections
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.subscription_utils import (
    plan_limit_error_response_dict,
    prompt_limit_reached,
)
from core.permissions.middleware import _client_ip
from core.permissions.throttling import (
    DataForSEOThrottle,
    ExpensiveThrottle,
    PollingThrottle,
)

from ..models import (
    AnalysisRun,
    Competitor,
    PromptCitation,
    PromptResult,
    PromptTrack,
)
from ..onboarding_security import (
    verify_token as _verify_onboarding_token,
)
from ..serializers import (
    AddPromptSerializer,
    PromptTrackSerializer,
)
from ._shared import (
    _budget_denied,
    _competitor_host,
    _fire_and_save_prompt,
    _opportunity_service,
    _scoped_run,
    logger,
)


class GeneratePromptsView(APIView):
    """POST /api/analyzer/generate-prompts/ — AI-generate brand-relevant prompts for onboarding.

    Gated by an onboarding token (see OnboardingStartView). This forces every
    fresh IP through a throttled (+ optionally Turnstile-gated) round-trip
    before they can spend a Gemini call here — defeats rotating-IP wallet
    drain that the per-IP DRF throttle alone can't stop.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request):
        client_ip = _client_ip(request)
        token = request.headers.get("X-Onboarding-Token", "")
        ok, reason = _verify_onboarding_token(token, client_ip)
        if not ok:
            logger.info("generate_prompts token_reject ip=%s reason=%s", client_ip, reason)
            return Response(
                {
                    "detail": "Onboarding token required. POST /api/analyzer/onboarding-start/ first.",
                    "reason": reason,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        brand_name = request.data.get("brand_name", "").strip()
        brand_url = request.data.get("brand_url", "").strip()

        if not brand_name:
            return Response({"error": "brand_name required."}, status=status.HTTP_400_BAD_REQUEST)

        from ..pipeline.prompt_tracker import generate_brand_prompts

        # Try to fetch page content for better context
        page_content = ""
        meta_desc = ""
        try:
            from ..pipeline.crawler import crawl_page

            if brand_url:
                quick_crawl = crawl_page(brand_url)
                if quick_crawl.ok:
                    page_content = quick_crawl.text[:2000]
                    md = quick_crawl.soup.find("meta", attrs={"name": "description"})
                    meta_desc = md["content"].strip() if md and md.get("content") else ""
        except Exception:
            pass

        try:
            prompts = generate_brand_prompts(
                brand_name=brand_name,
                brand_url=brand_url,
                page_content=page_content,
                meta_description=meta_desc,
                count=10,
            )
            return Response({"prompts": prompts})
        except Exception as exc:
            logger.warning("Generate prompts failed: %s", exc)
            # Not branded. A prompt containing the brand name scores ~100%
            # visibility and measures nothing — an engine handed the name repeats
            # it. GEO tracking is about the reverse: a buyer who has never heard
            # of this brand describes their problem, and either it gets cited or
            # it does not. Keep one branded prompt as an entity-resolution
            # baseline, and nothing more.
            return Response(
                {
                    "prompts": [
                        "What are the best tools for this in 2026?",
                        "How do I choose between the options in this category?",
                        "What should I look for when evaluating a provider?",
                        "Which platform do experts recommend for a small team?",
                        f"What is {brand_name}?",
                    ]
                }
            )

class CompetitorPromptGenerateView(APIView):
    """POST /runs/s/<slug>/competitor-prompts/generate/ — build and fire them.

    Explicit because it is the single most expensive optional thing an analysis
    can do: 10 generated prompts x 4 engines, ~$0.75, which was **39% of the
    entire cost of every analysis**. It used to run automatically at the end of
    every run, for a page that no shipped UI displays - the frontend's
    ``generateCompetitorPrompts`` even pointed at this route before it existed.

    Now nothing fires it unless something asks. The work itself is unchanged, so
    a caller gets exactly the same prompts and the same engine answers as before.

    Returns 202: generation is dispatched to a background thread so the request
    does not hold a worker for the duration of ~40 engine calls.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from ..tasks import _generate_and_fire_competitive_prompts

        run, err = _scoped_run(request, slug)
        if err is not None:
            return err
        denied = _budget_denied(run)
        if denied is not None:
            return denied

        existing = run.prompt_tracks.filter(
            prompt_type=PromptTrack.PromptSurfaceType.COMPETITIVE,
            is_custom=False,
            deleted_at__isnull=True,
        ).count()
        # The generator is already idempotent at >=10 (see its own docstring),
        # but reporting it here means a second click is a cheap, honest no-op
        # rather than a silent one.
        if existing >= 10:
            return Response(
                {"status": "ready", "detail": "Competitive prompts already generated.",
                 "count": existing},
                status=status.HTTP_200_OK,
            )

        _generate_and_fire_competitive_prompts(run)
        return Response(
            {"status": "generating",
             "detail": "Generating competitive prompts; results appear as engines answer."},
            status=status.HTTP_202_ACCEPTED,
        )

class CompetitorPromptListView(APIView):
    """GET /runs/s/<slug>/competitor-prompts/ — prompts for this run where
    competitor brands surfaced in the AI response set. Decorates each row
    with `mentioned_competitors_detail` derived from existing PromptCitation
    rows (no new model, no generation pipeline)."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.db.models import Q
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)

        # Prompts whose AI responses cited a competitor domain — the signal
        # is recorded on PromptCitation.is_competitor at scoring time.
        competitive_prompt_ids = (
            PromptCitation.objects.filter(
                prompt_result__prompt_track__analysis_run=run,
                is_competitor=True,
            )
            .values_list("prompt_result__prompt_track_id", flat=True)
            .distinct()
        )

        # Include prompts explicitly typed as COMPETITIVE too (handles cases
        # where the prompt was classified but its AI results haven't been
        # scored yet, so no PromptCitation rows exist).
        tracks = (
            run.prompt_tracks.filter(deleted_at__isnull=True)
            .filter(
                Q(id__in=competitive_prompt_ids) | Q(prompt_type=PromptTrack.PromptSurfaceType.COMPETITIVE)
            )
            .select_related("analysis_run")
            .prefetch_related("results", "results__citations")
            .order_by("-score", "-created_at")
        )

        # Build a {domain -> competitor payload} map once for cheap lookup
        # during the per-prompt decoration loop below.
        competitor_by_domain: dict[str, dict] = {}
        for c in Competitor.objects.filter(analysis_run=run):
            host = _competitor_host(c.url)
            if host:
                competitor_by_domain[host] = {"id": c.id, "name": c.name, "url": c.url}

        serialized = PromptTrackSerializer(tracks, many=True).data
        # Decorate each serialized prompt with the distinct competitors its
        # AI citations actually surfaced. Keyed by id to dedupe across engines.
        for payload, track in zip(serialized, tracks, strict=True):
            mentioned: dict[int, dict] = {}
            for pr in track.results.all():
                for cit in pr.citations.all():
                    if not cit.is_competitor:
                        continue
                    info = competitor_by_domain.get((cit.domain or "").lower())
                    if info:
                        mentioned[info["id"]] = info
            payload["mentioned_competitors_detail"] = list(mentioned.values())

        return Response(serialized)

class PromptListCreateView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        tracks = (
            run.prompt_tracks.filter(deleted_at__isnull=True)
            .select_related("analysis_run")
            # AnalysisRun.llm_logs / onboarding_prompts are large JSONField
            # blobs we don't need here — defer them to keep the join cheap.
            .defer("analysis_run__llm_logs", "analysis_run__onboarding_prompts")
            .prefetch_related("results", "results__citations")
            .order_by("-score", "-created_at")
        )
        serializer = PromptTrackSerializer(tracks, many=True)
        return Response(serializer.data)

    def post(self, request, slug):
        import threading

        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        ser = AddPromptSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

        email = (run.email or "").strip().lower()
        reached, pl_msg = prompt_limit_reached(email)
        if reached:
            return Response(
                plan_limit_error_response_dict(pl_msg),
                status=status.HTTP_403_FORBIDDEN,
            )

        from ..pipeline.prompt_tracker import classify_prompt_intent_and_type

        brand_ctx = (run.brand_name or "").strip()
        intent, prompt_type = classify_prompt_intent_and_type(
            ser.validated_data["prompt_text"],
            brand_ctx,
            (run.url or "").strip(),
        )
        track = PromptTrack.objects.create(
            analysis_run=run,
            prompt_text=ser.validated_data["prompt_text"],
            is_custom=True,
            intent=intent,
            prompt_type=prompt_type,
        )

        brand_name = run.brand_name or run.url
        brand_url = run.url
        t = threading.Thread(
            target=_fire_and_save_prompt,
            args=(track, brand_name, brand_url),
            daemon=True,
        )
        t.start()

        return Response(PromptTrackSerializer(track).data, status=status.HTTP_202_ACCEPTED)

class PromptResultDetailView(APIView):
    """GET /runs/s/<slug>/prompts/<track_id>/results/<result_id>/ — full response_text."""

    permission_classes = [AllowAny]

    def get(self, request, slug, track_id, result_id):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, pk=track_id, analysis_run=run)
        result = get_object_or_404(PromptResult, pk=result_id, prompt_track=track)
        from ..serializers import PromptResultFullSerializer

        return Response(PromptResultFullSerializer(result).data)

def _resync_geo_tasks(run) -> None:
    """Rebuild the run's prompt-driven tasks after its results change.

    Prompt answers arrive asynchronously, long after the analysis that first
    generated these tasks. Without this the "win this prompt" tasks only ever
    reflected the state at analysis time, so a newly lost prompt never produced
    a task. Best-effort: a failure here must not break the recheck.
    """
    try:
        from ..services.geo_tasks import sync_geo_signal_tasks

        sync_geo_signal_tasks(run)
    except Exception:
        logger.exception("Run %s: GEO task resync after recheck failed", getattr(run, "pk", "?"))


class RecheckPromptView(APIView):
    """POST /runs/s/<slug>/prompts/<track_id>/recheck/ — re-fire one prompt now."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug, track_id):
        import threading

        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, pk=track_id, analysis_run=run, deleted_at__isnull=True)

        brand_name = run.brand_name or run.url
        brand_url = run.url

        def _do():

            from ..pipeline.prompt_tracker import recheck_track

            close_old_connections()
            recheck_track(track, brand_name, brand_url)
            _resync_geo_tasks(run)

        threading.Thread(target=_do, daemon=True).start()
        return Response({"status": "rechecking"}, status=status.HTTP_202_ACCEPTED)

class PromptBacklinksView(APIView):
    """GET /runs/s/<slug>/prompts/<track_id>/backlinks/ — Citation Authority panel.

    Thin HTTP layer — delegates all work to ``BacklinkAuthorityService``.
    Translates ``ProviderNotConfigured`` into a structured 503 the frontend
    can recognize and surface as "Backlink provider not configured".
    """

    permission_classes = [AllowAny]
    throttle_classes = [DataForSEOThrottle]

    def get(self, request, slug, track_id):
        from django.shortcuts import get_object_or_404

        from ..services.backlink_authority import (
            BacklinkAuthorityService,
            ProviderNotConfigured,
        )

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(
            PromptTrack,
            pk=track_id,
            analysis_run=run,
            deleted_at__isnull=True,
        )

        try:
            payload = BacklinkAuthorityService(track=track).build()
        except ProviderNotConfigured as exc:
            return Response(
                {"detail": str(exc), "code": "dataforseo_not_configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(payload)

class PromptOpportunitiesView(APIView):
    """GET / POST /runs/s/<slug>/prompts/<track_id>/opportunities/

    Thin HTTP layer — delegates to ``OpportunityService`` for all logic.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug, track_id):
        service = _opportunity_service(slug, track_id)
        return Response(service.list())

    def post(self, request, slug, track_id):
        service = _opportunity_service(slug, track_id)
        from ..services.opportunities import OpportunityServiceError

        try:
            return Response(service.regenerate())
        except OpportunityServiceError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

class PromptOpportunityDetailView(APIView):
    """PATCH / DELETE /runs/s/<slug>/prompts/<track_id>/opportunities/<opp_id>/"""

    permission_classes = [AllowAny]

    def patch(self, request, slug, track_id, opp_id):
        service = _opportunity_service(slug, track_id)
        from ..services.opportunities import OpportunityServiceError

        try:
            payload = service.update_status(
                opp_id,
                new_status=request.data.get("status"),
                live_url=request.data.get("live_url"),
            )
        except OpportunityServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)

    def delete(self, request, slug, track_id, opp_id):
        service = _opportunity_service(slug, track_id)
        from ..services.opportunities import OpportunityServiceError

        try:
            service.delete(opp_id)
        except OpportunityServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

class PromptDeleteView(APIView):
    """DELETE /runs/s/<slug>/prompts/<track_id>/ — soft-delete a tracked prompt.

    The row is retained (flagged with `deleted_at`) so the user's historical
    count still applies toward their plan's `max_prompts`. This prevents
    deleting-and-re-adding to bypass plan limits. Usage/billing endpoints
    also count soft-deleted rows.
    """

    permission_classes = [AllowAny]

    def delete(self, request, slug, track_id):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, pk=track_id, analysis_run=run, deleted_at__isnull=True)
        track.deleted_at = timezone.now()
        track.save(update_fields=["deleted_at"])
        from core.cache.keys import invalidate_run_aggregates

        invalidate_run_aggregates(slug)
        return Response(status=status.HTTP_204_NO_CONTENT)

class RecheckAllPromptsView(APIView):
    """POST /runs/s/<slug>/recheck-all/ — re-fire every prompt for this run."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        import threading

        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        tracks = list(run.prompt_tracks.filter(deleted_at__isnull=True))

        if not tracks:
            return Response({"status": "no_tracks", "count": 0})

        brand_name = run.brand_name or run.url
        brand_url = run.url

        def _do_all():

            from ..pipeline.prompt_tracker import recheck_track

            close_old_connections()
            for track in tracks:
                try:
                    recheck_track(track, brand_name, brand_url)
                except Exception as exc:
                    logger.warning("recheck_all: track #%d failed: %s", track.pk, exc)
            _resync_geo_tasks(run)

        threading.Thread(target=_do_all, daemon=True).start()
        return Response(
            {"status": "rechecking", "count": len(tracks)},
            status=status.HTTP_202_ACCEPTED,
        )

class PromptRankView(APIView):
    """
    GET/POST /runs/s/<slug>/prompts/<int:track_id>/rank/

    Returns top-3 web ranking (Google / Reddit / Quora) for the tracked
    prompt's text. Lazily creates a RankQuery on the latest audit and runs
    only the web-surface fetchers synchronously so the prompt expands with
    real data the first time it's opened.
    """

    permission_classes = [AllowAny]
    throttle_classes = [DataForSEOThrottle]

    def _serialize(self, query):
        results = list(
            query.results.filter(surface__in=["google", "reddit", "quora"]).order_by("surface", "position")
        )
        return {
            "id": query.id,
            "prompt_text": query.prompt_text,
            "rank": query.rank,
            "brand_mention_count": query.brand_mention_count,
            "status": query.status,
            "error_message": query.error_message,
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
                for r in results
            ],
        }

    def _ensure_audit(self, run):
        from ..models import RankAudit

        audit = RankAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
        if audit is None:
            audit = RankAudit.objects.create(
                analysis_run=run,
                status=RankAudit.Status.COMPLETE,
            )
        return audit

    def _get_or_create_query(self, audit, prompt_text):
        from django.db.models import Max

        from ..models import RankQuery

        query = RankQuery.objects.filter(audit=audit, prompt_text=prompt_text).order_by("-id").first()
        if query is None:
            next_rank = (RankQuery.objects.filter(audit=audit).aggregate(m=Max("rank"))["m"] or 0) + 1
            query = RankQuery.objects.create(
                audit=audit,
                prompt_text=prompt_text,
                rank=next_rank,
                status=RankQuery.Status.QUEUED,
            )
        return query

    def _run_web_fetch(self, query, run):
        from urllib.parse import urlparse as _urlparse

        from ..models import RankQuery, RankResult
        from ..pipeline.rank_tracker import (
            _derive_geo,
            compute_sentiment,
            detect_brand_mentions,
            fetch_quora,
            fetch_reddit,
            fetch_serper,
        )

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

        # Clear any prior web-surface results so re-runs don't pile up.
        RankResult.objects.filter(query=query, surface__in=["google", "reddit", "quora"]).delete()

        fetchers = (
            ("google", lambda q: fetch_serper(q, gl=gl)),
            ("reddit", lambda q: fetch_reddit(q, gl=gl)),
            ("quora", lambda q: fetch_quora(q, gl=gl)),
        )

        to_create = []
        brand_hits = 0
        for surface, fn in fetchers:
            try:
                rows = fn(query.prompt_text) or []
            except Exception as exc:
                logger.warning(
                    "PromptRankView surface=%s prompt=%r error: %s",
                    surface,
                    query.prompt_text[:80],
                    exc,
                )
                rows = []
            # Keep only the top 3 per surface — that's all we display.
            rows = rows[:3]
            for row in rows:
                snippet = row.get("snippet", "") or ""
                is_brand, comps = detect_brand_mentions(
                    row.get("title", ""),
                    snippet,
                    brand_names,
                    competitor_names,
                    result_domain=row.get("domain", ""),
                    result_url=row.get("url", ""),
                    brand_domain=brand_domain,
                )
                if is_brand:
                    brand_hits += 1
                sentiment = compute_sentiment(f"{row.get('title') or ''} {snippet}")
                to_create.append(
                    RankResult(
                        query=query,
                        surface=surface,
                        position=int(row.get("position") or 0),
                        url=(row.get("url") or "")[:2048],
                        domain=(row.get("domain") or "")[:255],
                        title=(row.get("title") or "")[:300],
                        snippet=(row.get("snippet") or "")[:4000],
                        engine="",
                        response_text="",
                        sentiment=sentiment,
                        is_brand_mentioned=is_brand,
                        competitors_mentioned=comps,
                        upvotes=row.get("upvotes"),
                        subreddit=(row.get("subreddit") or "")[:120],
                    )
                )

        if to_create:
            RankResult.objects.bulk_create(to_create)

        query.brand_mention_count = brand_hits
        query.status = RankQuery.Status.DONE
        query.error_message = ""
        query.save(update_fields=["brand_mention_count", "status", "error_message"])

    def _resolve(self, slug, track_id, *, force_refresh=False):
        from django.shortcuts import get_object_or_404

        from ..models import PromptTrack

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, id=track_id, analysis_run=run)

        prompt_text = (track.prompt_text or "").strip()
        if not prompt_text:
            return None, Response(
                {"detail": "Prompt has no text."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit = self._ensure_audit(run)
        query = self._get_or_create_query(audit, prompt_text)

        has_web_results = query.results.filter(surface__in=["google", "reddit", "quora"]).exists()

        if force_refresh or not has_web_results:
            try:
                self._run_web_fetch(query, run)
            except Exception as exc:
                logger.exception("PromptRankView fetch failed: %s", exc)
                query.status = "failed"
                query.error_message = str(exc)[:500]
                query.save(update_fields=["status", "error_message"])

        # Re-load to reflect any saved results.
        query.refresh_from_db()
        return query, None

    def get(self, request, slug, track_id):
        query, err = self._resolve(slug, track_id, force_refresh=False)
        if err is not None:
            return err
        return Response(self._serialize(query))

    def post(self, request, slug, track_id):
        force = str(request.data.get("refresh", "")).lower() in ("1", "true", "yes")
        query, err = self._resolve(slug, track_id, force_refresh=force)
        if err is not None:
            return err
        return Response(self._serialize(query))

class PromptWikipediaDraftView(APIView):
    """
    GET  /runs/s/<slug>/prompts/<int:track_id>/wikipedia/draft/
        Returns the saved draft if one exists, else 404.

    POST /runs/s/<slug>/prompts/<int:track_id>/wikipedia/draft/
        Body: { "force": bool? }
        Generates and persists the Wikipedia kit. Returns the cached version
        if one exists and force is false.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug, track_id):
        from django.shortcuts import get_object_or_404

        from ..models import PromptTrack, PromptWikipediaDraft

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, id=track_id, analysis_run=run)
        existing = PromptWikipediaDraft.objects.filter(prompt_track=track).first()
        if not existing:
            return Response({"detail": "No saved draft yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response({**existing.payload, "cached": True})

    def post(self, request, slug, track_id):

        from django.shortcuts import get_object_or_404

        from core.llm.client import ask_llm

        from ..models import PromptTrack, PromptWikipediaDraft

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, id=track_id, analysis_run=run)

        force = bool(request.data.get("force"))
        if not force:
            existing = PromptWikipediaDraft.objects.filter(prompt_track=track).first()
            if existing and existing.payload:
                return Response({**existing.payload, "cached": True})

        brand = (run.brand_name or "").strip() or "the brand"
        url = (run.url or "").strip()
        prompt_text = (track.prompt_text or "").strip()

        # Pull a few signals from existing data the LLM can ground the draft on.
        try:
            competitors = list(run.competitors.values_list("name", flat=True)[:5])
        except Exception:
            competitors = []

        prompt = f"""You are an experienced Wikipedia editor helping a brand build a notable, neutral, well-sourced presence on Wikipedia.

BRAND NAME: {brand}
BRAND URL: {url or "(none)"}
USER QUERY (the prompt they want to be cited for): {prompt_text}
KNOWN COMPETITORS: {", ".join(competitors) or "(none)"}

Produce a JSON object that helps the user actually post to Wikipedia. Cover three things:

1) Notability verdict — does this brand currently meet Wikipedia's notability bar (significant, sustained, independent secondary sources)?
2) A draft article — neutral encyclopedic tone, no marketing language, with placeholder citations as [1], [2], etc.
3) Edit targets — 3-5 EXISTING Wikipedia articles where this brand could plausibly be added as a relevant citation, with the exact one-sentence edit to suggest.

CRITICAL CONSTRAINTS:
- Tone must be neutral, encyclopedic, factual. NEVER use words like "leading", "innovative", "best", "trusted", "premier", "cutting-edge".
- Every claim of fact must reference a citation [n]. References list real, plausible source types (TechCrunch article, Forbes profile, peer-reviewed paper, government registry).
- If the brand likely lacks notability, say so honestly in the verdict — don't fabricate.
- Sections should follow Wikipedia conventions: Lead, History, Products / Services, Reception, References.
- Edit targets must be real existing Wikipedia article titles related to the BRAND or the USER QUERY topic.

Return ONLY valid JSON. No markdown fences. Schema:
{{
  "notability": {{
    "verdict": "qualifies" | "borderline" | "needs_more_coverage",
    "score": <0 to 100>,
    "summary": "Two-sentence summary of why.",
    "missing_evidence": ["Specific gap 1", "Specific gap 2"]
  }},
  "draft": {{
    "title": "Article title",
    "lead": "Markdown lead paragraph (2-4 sentences) with [1] style citations.",
    "sections": [
      {{"heading": "History", "body_markdown": "..."}},
      {{"heading": "Products and services", "body_markdown": "..."}},
      {{"heading": "Reception", "body_markdown": "..."}}
    ],
    "infobox": {{
      "type": "Company",
      "founded": "Year if known, else 'TBD'",
      "headquarters": "City, Country if known",
      "industry": "Industry name",
      "website": "{url or "TBD"}"
    }},
    "references_markdown": "1. Citation 1 source.\\n2. Citation 2 source.\\n3. ..."
  }},
  "edit_targets": [
    {{
      "title": "Existing Wikipedia article title",
      "url": "https://en.wikipedia.org/wiki/Article_Title",
      "suggested_edit": "One sentence to add at a specific section, with [citation needed] placeholder"
    }}
  ],
  "submit_instructions_markdown": "Step-by-step markdown instructions for submitting via Articles for Creation, including the exact AfC link and what to do if the article gets declined."
}}"""

        raw = ask_llm(
            prompt,
            max_tokens=4096,
            temperature=0.2,
            purpose="Wikipedia draft generator",
        )
        if not raw:
            return Response(
                {"detail": "LLM did not respond. Try again in a moment."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Epic 8: shared extractor -- it already handles fences and prose around the JSON.
        from core.llm.structured import extract_json

        payload = extract_json(raw, expect=dict)
        if not isinstance(payload, dict):
            payload = None

        if payload is None:
            logger.warning(
                "Wikipedia draft JSON parse failed for slug=%s track=%s. Raw: %s",
                slug,
                track_id,
                (raw or "")[:300],
            )
            return Response(
                {
                    "detail": "The AI returned an unexpected response. Please retry.",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        PromptWikipediaDraft.objects.update_or_create(prompt_track=track, defaults={"payload": payload})

        return Response({**payload, "cached": False})

class PromptSchemaView(APIView):
    """
    GET  /runs/s/<slug>/prompts/<int:track_id>/schema/
        Returns all previously-generated artifacts for this prompt.

    POST /runs/s/<slug>/prompts/<int:track_id>/schema/
        Body: { "schema_type": "faq"|"article"|"person"|"organization"|"answer",
                "force": bool? }
        If an artifact for this (prompt, schema_type) already exists and force is
        false, it is returned without re-calling the LLM. Set force=true to
        regenerate.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    SCHEMA_TYPES = {"faq", "article", "person", "organization", "answer"}

    def get(self, request, slug, track_id):
        from django.shortcuts import get_object_or_404

        from ..models import PromptSchemaArtifact, PromptTrack

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, id=track_id, analysis_run=run)
        artifacts = PromptSchemaArtifact.objects.filter(prompt_track=track)
        return Response(
            {
                "artifacts": [
                    {
                        "schema_type": a.schema_type,
                        "output": a.output,
                        "explanation": a.explanation,
                        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
                    }
                    for a in artifacts
                ]
            }
        )

    def post(self, request, slug, track_id):
        import re

        from django.shortcuts import get_object_or_404

        from core.llm.client import ask_llm

        from ..models import PromptSchemaArtifact, PromptTrack

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, id=track_id, analysis_run=run)

        schema_type = (request.data.get("schema_type") or "").strip().lower()
        if schema_type not in self.SCHEMA_TYPES:
            return Response(
                {"detail": f"schema_type must be one of {sorted(self.SCHEMA_TYPES)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        force = bool(request.data.get("force"))
        if not force:
            cached = PromptSchemaArtifact.objects.filter(prompt_track=track, schema_type=schema_type).first()
            if cached:
                return Response(
                    {
                        "schema_type": cached.schema_type,
                        "output": cached.output,
                        "explanation": cached.explanation,
                        "cached": True,
                    }
                )

        brand = (run.brand_name or "").strip() or "the brand"
        url = (run.url or "").strip() or "https://example.com"
        prompt_text = (track.prompt_text or "").strip()

        instructions = {
            "faq": (
                "Output ONLY a single FAQPage JSON-LD with one Question whose `name` is the user prompt verbatim, "
                "and an Answer in 2-4 sentences in a neutral, brand voice. Do not invent statistics. "
                "Wrap in `mainEntity` correctly per schema.org."
            ),
            "article": (
                "Output ONLY an Article JSON-LD for a hypothetical page on the brand's domain that answers "
                "the user prompt. Include headline, description, datePublished (today), dateModified (today), "
                "author (Person stub), publisher (Organization with the brand name and logo URL placeholder), "
                "mainEntityOfPage with a plausible URL slug derived from the prompt."
            ),
            "person": (
                "Output ONLY a Person JSON-LD for an author writing about the user prompt's topic for this brand. "
                "Include name (placeholder 'Author Name'), jobTitle, worksFor (the brand), description with their "
                "domain expertise tied to the prompt's topic, sameAs array with placeholder LinkedIn / Twitter "
                "URLs, and knowsAbout array of 3-5 topics relevant to the prompt."
            ),
            "organization": (
                "Output ONLY an Organization JSON-LD for the brand. Include name, url, logo URL placeholder, "
                "description tied to the prompt's topic so the brand reads as a primary source for it, "
                "knowsAbout array, sameAs array with placeholder profile URLs."
            ),
            "answer": (
                "Do NOT output JSON-LD. Output a 2-paragraph direct answer to the user prompt, written in the "
                "brand's voice. Each paragraph 2-3 sentences. Neutral, factual, useful — no marketing words. "
                f"REQUIRED: mention '{brand}' by name in BOTH paragraphs (at least twice total), framed as a "
                f"credible source/practitioner — e.g. 'According to {brand}…', \"{brand}'s formulations…\", "
                f"'In {brand}'s experience…', '{brand} recommends…'. {brand} must read as a domain authority on "
                "this topic, not as a sponsor or marketer. Open with a direct factual answer to the prompt; "
                "use the brand mentions to ground specific claims. End with one practical action the reader can take "
                "(no sales CTA)."
            ),
        }[schema_type]

        prompt = f"""You are generating production-ready content for a brand to paste into their website.

BRAND: {brand}
BRAND URL: {url}
USER PROMPT (the AI search query they want to rank for): {prompt_text}

TASK: {instructions}

CRITICAL:
- For schema types: output ONLY valid JSON-LD wrapped in <script type="application/ld+json">…</script>. No prose.
- For 'answer': output ONLY the 2-paragraph answer. No headings, no bullets, no JSON.
- Use the brand name and URL as given. Use placeholder URLs for logo / images / sameAs profiles.
- Stay neutral. Never use the words "leading", "best", "innovative", "premier", "trusted".
"""

        raw = ask_llm(
            prompt,
            max_tokens=2048,
            temperature=0.2,
            purpose=f"Per-prompt schema generator ({schema_type})",
        )
        if not raw:
            return Response(
                {"detail": "LLM did not respond. Try again in a moment."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json|html|markdown)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        explanation_map = {
            "faq": "Paste this inside the <head> or near the relevant Q&A on the page targeting this prompt. AI engines lift verbatim Q→A pairs at the highest extraction rate.",
            "article": "Paste this inside the <head> of the article page that targets this prompt. Replace placeholder URLs and the author stub with real values.",
            "person": "Paste this on the author's profile page (e.g. /author/<slug>). Fill in real name, photo URL, LinkedIn, and credentials. Person schema is a direct E-E-A-T signal.",
            "organization": "Paste this in the <head> of your homepage. Replace placeholder logo and sameAs URLs. Organization schema makes you the canonical source for queries about your brand.",
            "answer": "Paste this paragraph directly on the page targeting this prompt — ideally near the top, with the prompt text as an H2 above it. AI engines often lift this verbatim.",
        }

        explanation = explanation_map[schema_type]

        PromptSchemaArtifact.objects.update_or_create(
            prompt_track=track,
            schema_type=schema_type,
            defaults={"output": cleaned, "explanation": explanation},
        )

        return Response(
            {
                "schema_type": schema_type,
                "output": cleaned,
                "explanation": explanation,
                "cached": False,
            }
        )

class PromptCoverageView(APIView):
    """GET /runs/s/<slug>/prompt-coverage/ — does a page answer each tracked prompt?

    Semantic search of every tracked prompt against the brand's own indexed
    pages. Answers the question that comes before any on-page or off-page work:
    a prompt with no answering content cannot be fixed by improving a page that
    does not exist.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from ..services.prompt_coverage import report_for_run

        run, err = _scoped_run(request, slug)
        if err is not None:
            return err
        return Response(report_for_run(run))

class PromptAnswerBlockView(APIView):
    """POST /runs/s/<slug>/prompts/<track_id>/answer-block/ — draft the passage.

    POST rather than GET because it is generative and costs money on every call.
    Drafted on demand rather than for every prompt on every run, so nobody pays
    for drafts they never read.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug, track_id):
        from django.shortcuts import get_object_or_404

        from ..services.answer_block import generate_for_prompt

        # Generative, and billed on every call.
        run, err = _scoped_run(request, slug)
        if err is not None:
            return err
        denied = _budget_denied(run)
        if denied is not None:
            return denied
        # Scoped to the run, so a track id from another brand cannot be reached.
        track = get_object_or_404(PromptTrack, pk=track_id, analysis_run=run, deleted_at__isnull=True)

        draft = generate_for_prompt(track)
        if draft is None:
            return Response(
                {"detail": "Could not draft an answer for this prompt. Try again shortly."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(draft)
