"""The analysis run lifecycle: start, read, list, export, schedule."""

from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.subscription_utils import (
    analysis_allowed_for_email,
    plan_limit_error_response_dict,
    project_limit_reached,
    prompt_batch_would_exceed,
)
from apps.organizations.models import Organization
from core.permissions.middleware import _client_ip
from core.permissions.throttling import (
    AuthSendThrottle,
    ExpensiveThrottle,
    PollingThrottle,
)

from ..models import (
    AnalysisRun,
    Recommendation,
)
from ..onboarding_security import (
    gate_onboarding_endpoint as _gate_onboarding_endpoint,
)
from ..onboarding_security import (
    mint_token as _mint_onboarding_token,
)
from ..onboarding_security import (
    turnstile_enabled as _turnstile_enabled,
)
from ..onboarding_security import (
    verify_turnstile as _verify_turnstile,
)
from ..serializers import (
    AnalysisRunDetailSerializer,
    AnalysisRunListSerializer,
    StartAnalysisSerializer,
)
from ..tasks import start_analysis_task
from ._shared import (
    logger,
)


class OnboardingStartView(APIView):
    """
    POST /api/analyzer/onboarding-start/

    Mints a short-lived signed token (~15 min) that downstream public AI
    endpoints (currently /generate-prompts/) require in the
    ``X-Onboarding-Token`` header.

    Body (optional):
      { "turnstile_token": "<cf turnstile response>" }  # accepted, not verified

    **Turnstile is currently disabled.** ``onboarding_security.turnstile_enabled``
    returns False unconditionally - the Cloudflare check was removed - so
    ``turnstile_token`` is accepted and ignored, and ``turnstile_enabled`` in the
    response is always False. Setting ``TURNSTILE_SECRET`` does *not* re-enable
    it; that function has to change first. The docstring previously claimed the
    opposite, which read as bot protection that is not actually running.

    What does defend this endpoint today: the global IP middleware plus this
    throttle. That still breaks rotating-IP wallet-drain on /generate-prompts,
    because each fresh IP must round-trip here first (heavily throttled) - but
    it is IP-based only, with no proof-of-humanity.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthSendThrottle]

    def post(self, request):
        client_ip = _client_ip(request)
        turnstile_token = (request.data.get("turnstile_token") or "").strip()
        if _turnstile_enabled():
            ok, reason = _verify_turnstile(turnstile_token, client_ip)
            if not ok:
                logger.warning("onboarding_start turnstile fail ip=%s reason=%s", client_ip, reason)
                return Response(
                    {"detail": "Bot check failed. Refresh and try again.", "reason": reason},
                    status=status.HTTP_403_FORBIDDEN,
                )
        token = _mint_onboarding_token(client_ip)
        return Response(
            {
                "token": token,
                "expires_in": 900,
                "turnstile_enabled": _turnstile_enabled(),
            }
        )

class StartAnalysisView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request):
        serializer = StartAnalysisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = dict(serializer.validated_data)
        verify_workspace = data.pop("verify_org_workspace", False)
        cleaned_prompts = data.pop("_cleaned_prompts", None)
        if cleaned_prompts is None:
            cleaned_prompts = []
        data.pop("prompts", None)

        email = data.get("email", "")
        org_id = data.get("org_id")

        # Onboarding gate: anon / free callers must hold a token minted by
        # /onboarding-start/ (which is Turnstile-gated). Active subscribers
        # bypass — the dashboard "run new analysis" button has no Turnstile.
        ok, reason = _gate_onboarding_endpoint(request, email)
        if not ok:
            logger.info("start_analysis token_reject ip=%s reason=%s", _client_ip(request), reason)
            return Response(
                {
                    "detail": "Onboarding token required. POST /api/analyzer/onboarding-start/ first.",
                    "reason": reason,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # An ``org_id`` the caller does not own was accepted verbatim below: the run
        # was created under that org (cross-tenant write), the project cap was
        # skipped because it only fires when org_id is absent, and an in-flight run
        # in that org came back with its ``slug`` in the response. Anonymous scans
        # (no org_id) are still allowed — that is the onboarding flow.
        if org_id:
            from ..access import resolve_scoped_org

            _org, org_err = resolve_scoped_org(email, org_id)
            if org_err:
                return org_err

        allowed, sub_err = analysis_allowed_for_email(email)
        if not allowed:
            return Response({"error": sub_err}, status=status.HTTP_403_FORBIDDEN)

        # Project cap: if the caller is at their plan's project limit AND this
        # analysis would land in a fresh org (no existing org for this email),
        # block it. Without this, free-tier users could create unlimited
        # orphan AnalysisRuns and burn AI calls without ever creating an org.
        if email and not org_id:
            existing_org = Organization.objects.filter(owner_email=email).first()
            if not existing_org:
                reached, msg = project_limit_reached(email)
                if reached:
                    return Response(
                        plan_limit_error_response_dict(msg),
                        status=status.HTTP_403_FORBIDDEN,
                    )

        # Plan cap: each completed analysis adds up to 10 prompt tracks.
        # Anonymous (no email) requests are free-tool scans — no account to cap.
        if email:
            batch_exceeds, batch_msg = prompt_batch_would_exceed(email, 10)
            if batch_exceeds:
                return Response(
                    plan_limit_error_response_dict(batch_msg),
                    status=status.HTTP_403_FORBIDDEN,
                )

        # One analysis at a time per brand: if this org already has a run in
        # flight (on ANY url), return it instead of starting a second. Falls back
        # to the per-(email, url) check for anonymous / no-org submissions.
        from ..run_guard import IN_FLIGHT_STATUSES, active_run_for

        submitted_url = data["url"]
        if org_id:
            existing = active_run_for(Organization.objects.filter(pk=org_id).first())
        elif email:
            existing = AnalysisRun.objects.filter(
                email=email,
                url=submitted_url,
                status__in=IN_FLIGHT_STATUSES,
            ).first()
        else:
            existing = None

        if existing:
            same_url = existing.url == submitted_url
            return Response(
                {
                    "id": existing.id,
                    "slug": existing.slug,
                    "url": existing.url,
                    "status": existing.status,
                    "message": (
                        "Analysis already in progress for this URL"
                        if same_url
                        else "An analysis is already running for this brand. Only one runs at a time."
                    ),
                },
                status=status.HTTP_200_OK,
            )

        # Resolve organization
        org = None
        if org_id:
            org = Organization.objects.filter(pk=org_id).first()
        elif email:
            org = Organization.objects.filter(owner_email=email).first()

        # 24h cooldown: one completed analysis per brand per day. A cheap gate on
        # top of the in-flight guard above — re-running unchanged data just burns
        # LLM / DataForSEO spend. Skipped for anonymous free-tool scans (no org).
        from ..run_guard import cooldown_until

        ready_at = cooldown_until(org)
        if ready_at is not None:
            return Response(
                {
                    "error": (
                        "This brand was analyzed in the last 24 hours. "
                        "A new analysis can run once per day."
                    ),
                    "next_allowed_at": ready_at.isoformat(),
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        run = AnalysisRun.objects.create(
            organization=org,
            url=data["url"],
            brand_name=data.get("brand_name", ""),
            country=data.get("country", ""),
            email=email,
            run_type=data["run_type"],
            status=AnalysisRun.Status.PENDING,
            onboarding_prompts=list(cleaned_prompts) if verify_workspace else [],
            storefront_password=data.get("storefront_password", ""),
        )

        # Start background task
        start_analysis_task(run.id)

        return Response(
            {
                "id": run.id,
                "slug": run.slug,
                "url": run.url,
                "status": run.status,
                "message": "Analysis started",
            },
            status=status.HTTP_201_CREATED,
        )

class AnalysisRunBySlugView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.db.models import Prefetch

        try:
            # RecommendationSerializer.get_can_auto_fix() reads obj.analysis_run.url,
            # so chain select_related to avoid one query per recommendation (this is
            # the public /dashboard/<slug> path — mirror AnalysisRunDetailView).
            recs_qs = Recommendation.objects.select_related("analysis_run").defer(
                "analysis_run__llm_logs", "analysis_run__onboarding_prompts"
            )
            run = (
                AnalysisRun.objects.select_related("brand_visibility", "organization")
                .prefetch_related(
                    "page_scores",
                    "competitors",  # remove this line to stop loading competitors
                    Prefetch("recommendations", queryset=recs_qs),
                    "ai_probes",
                )
                .get(slug=slug)
            )
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Project not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Self-heal orphaned runs whose background worker died, so the frontend
        # recovers instead of polling a dead run forever (see run_guard).
        from ..run_guard import maybe_fail_stale

        maybe_fail_stale(run)

        serializer = AnalysisRunDetailSerializer(run)
        return Response(serializer.data)

class AnalysisRunListView(APIView):
    # The analysing screen polls this every 3.5s to drive the progress bar, which
    # is ~1000 requests/hour for one user watching one run. Without an explicit
    # scope it inherited DEFAULT_THROTTLE_RATES["anon"] = 60/hour and ran out of
    # budget about three minutes in: the bar froze at whatever checkpoint it had
    # reached, and the 429s spilled onto every other anon-keyed endpoint for the
    # rest of the hour because that bucket is shared per IP.
    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        # Scope from the resolved caller, never from a raw query parameter. This
        # response carries ``slug`` — the capability every other run endpoint
        # accepts — so an unscoped ``org_id`` here handed out access to whichever
        # tenant happened to be at that integer PK.
        from core.auth.identity import resolve_request_email

        from ..access import resolve_scoped_org

        email, err = resolve_request_email(request)
        if err:
            return err

        org_id = request.query_params.get("org_id")
        if org_id:
            org, err = resolve_scoped_org(email, org_id)
            if err:
                return err
            runs = AnalysisRun.objects.filter(organization=org)
        else:
            runs = AnalysisRun.objects.filter(email=email)

        # Heavy JSONFields (llm_logs, onboarding_prompts) aren't in the list
        # serializer — defer them so we don't ship hundreds of KB per row.
        runs = runs.defer("llm_logs", "onboarding_prompts").order_by("-created_at")
        # Backstop against unbounded response growth (see shared.pagination).
        from shared.pagination import bounded_slice

        from ..run_guard import maybe_fail_stale

        page = list(bounded_slice(request, runs))
        # The loading screen polls this endpoint, so heal orphaned runs here too
        # (not just in the slug detail view) — otherwise a dead run reports its
        # last progress forever and the bar never recovers.
        for run in page:
            maybe_fail_stale(run)

        serializer = AnalysisRunListSerializer(page, many=True)
        return Response(serializer.data)

class LatestRunProgressView(APIView):
    """GET /runs/progress/?email= — just enough to drive the progress bar.

    The analysing screen used to poll the full run *list* every 3.5s: a page of
    up to 20 rows, each passed through ``maybe_fail_stale`` (which can write) and
    a nine-field serializer, to read one integer off ``[0]``. That is the most
    frequently hit query in the product doing ~20x the work it needs.

    This reads one row, five columns, and heals only that row. Same polling
    cadence costs a fraction of the database time and a fraction of the payload.

    Still the wrong shape long-term - the run emits ~10 discrete checkpoints, so
    a push (SSE) would deliver them in ~10 messages instead of ~85 requests - but
    that needs an ASGI server, and this one is WSGI with 8 request slots total.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        from core.auth.identity import resolve_request_email

        from ..run_guard import maybe_fail_stale

        # This endpoint returns ``slug``, so an unscoped email turned "I know your
        # address" into full access to the run behind it.
        email, err = resolve_request_email(request)
        if err:
            return err

        run = (
            AnalysisRun.objects.filter(email=email)
            .only("id", "slug", "status", "progress", "phase", "updated_at")
            .order_by("-created_at")
            .first()
        )
        if run is None:
            return Response({"found": False})

        # Heal a silently-orphaned run here too: this is now the endpoint the
        # loading screen polls, so without it a dead run reports its last
        # progress forever and the bar never recovers.
        run = maybe_fail_stale(run)
        return Response(
            {
                "found": True,
                "slug": run.slug,
                "status": run.status,
                "progress": run.progress or 0,
                # What the pipeline is doing right now. Two stages account for
                # most of the wall-clock, so a bare percentage reads as frozen.
                "phase": run.phase or "",
            }
        )

class AnalysisRunDetailView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]  # frequently loaded by analyzer pages

    def get(self, request, run_id):
        from django.db.models import Prefetch

        try:
            # Pre-load every related collection the serializer touches so the
            # response is one batch of queries instead of five sequential
            # cross-region round trips. brand_visibility is a OneToOne —
            # select_related. Reverse-FKs use prefetch_related.
            #
            # RecommendationSerializer.get_can_auto_fix() reads obj.analysis_run.url,
            # so we chain a select_related to avoid one query per recommendation.
            # We also defer llm_logs (~128 KB JSONField) on that join.
            recs_qs = Recommendation.objects.select_related("analysis_run").defer(
                "analysis_run__llm_logs", "analysis_run__onboarding_prompts"
            )
            run = (
                AnalysisRun.objects.select_related("brand_visibility", "organization")
                .prefetch_related(
                    "page_scores",
                    "competitors",
                    Prefetch("recommendations", queryset=recs_qs),
                    "ai_probes",
                )
                .get(pk=run_id)
            )
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AnalysisRunDetailSerializer(run)
        return Response(serializer.data)

class AnalysisRunStatusView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]  # No throttling — this is a polling endpoint

    def get(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "id": run.id,
                "status": run.status,
                "progress": run.progress,
                "composite_score": run.composite_score,
            }
        )

class ExportPDFView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, run_id):
        return self.post(request, run_id)

    def post(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if run.status != AnalysisRun.Status.COMPLETE:
            return Response(
                {"error": "Analysis must be complete before exporting."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            import io

            from django.template.loader import render_to_string
            from xhtml2pdf import pisa

            main_page = run.page_scores.filter(url=run.url).first()
            recommendations = run.recommendations.all()
            competitors = run.competitors.filter(scored=True)

            main_page_pillars = []
            if main_page:
                pillar_defs = [
                    ("Content Structure", main_page.content_score),
                    ("Schema Markup", main_page.schema_score),
                    ("E-E-A-T Signals", main_page.eeat_score),
                    ("Technical GEO", main_page.technical_score),
                    ("Entity Authority", main_page.entity_score),
                    ("AI Visibility", main_page.ai_visibility_score),
                ]
                for label, score in pillar_defs:
                    s = float(score or 0)
                    s = max(0.0, min(100.0, s))
                    main_page_pillars.append(
                        {
                            "label": label,
                            "score": s,
                            "remainder": 100.0 - s,
                        }
                    )

            # Sort recommendations: critical → high → medium → low.
            priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            recommendations = sorted(
                recommendations,
                key=lambda r: (priority_order.get(getattr(r, "priority", "low"), 4), r.id),
            )

            context = {
                "run": run,
                "main_page": main_page,
                "main_page_pillars": main_page_pillars,
                "recommendations": recommendations,
                "competitors": competitors,
                "ai_probes": run.ai_probes.all(),
            }

            html_string = render_to_string("analyzer/report.html", context)

            pdf_buffer = io.BytesIO()
            pisa_status = pisa.CreatePDF(html_string, dest=pdf_buffer, encoding="utf-8")

            if pisa_status.err:
                logger.error("PDF generation error for run %d: %s", run_id, pisa_status.err)
                return Response(
                    {"error": "PDF generation failed."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            pdf_buffer.seek(0)
            response = HttpResponse(pdf_buffer.read(), content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="geo-analysis-{run.id}.pdf"'
            return response

        except ImportError as exc:
            logger.error("PDF export import error: %s", exc)
            return Response(
                {"error": "PDF export library not available."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except Exception as exc:
            logger.error("PDF export failed for run %d: %s", run_id, exc, exc_info=True)
            return Response(
                {"error": "PDF generation failed.", "detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

class ScoreHistoryView(APIView):
    """GET /api/analyzer/runs/history/?email=&org_id="""

    permission_classes = [AllowAny]

    def get(self, request):
        # Same leak as AnalysisRunListView: ``org_id`` is a sequential PK and every
        # row below carries ``slug``, which unlocks the whole runs/s/<slug>/ family.
        from core.auth.identity import resolve_request_email

        from ..access import resolve_scoped_org

        email, err = resolve_request_email(request)
        if err:
            return err

        org_id = request.query_params.get("org_id")
        if org_id:
            org, err = resolve_scoped_org(email, org_id)
            if err:
                return err
            qs = AnalysisRun.objects.filter(organization=org, status="complete")
        else:
            qs = AnalysisRun.objects.filter(email=email, status="complete")

        # Order by when the run finished updating (score finalized); expose that as `date`
        # so the chart shows distinct points per analysis, not just calendar day.
        # Cap to the most recent N points (backstop) but keep ascending order so the
        # delta computation below is correct.
        from shared.pagination import MAX_LIST_LIMIT

        recent = list(
            qs.order_by("-updated_at").values(
                "id", "created_at", "updated_at", "composite_score", "slug"
            )[:MAX_LIST_LIMIT]
        )
        data = list(reversed(recent))
        result = []
        prev_score = None
        for row in data:
            score = round(row["composite_score"] or 0, 1)
            delta = None
            pct = None
            if prev_score is not None:
                delta = round(score - prev_score, 1)
                if prev_score != 0:
                    pct = round((score - prev_score) / prev_score * 100, 1)
            result.append(
                {
                    "id": row["id"],
                    "date": row["updated_at"].isoformat(),
                    "created_at": row["created_at"].isoformat(),
                    "composite_score": score,
                    "slug": row["slug"],
                    "delta_from_previous": delta,
                    "percent_change_from_previous": pct,
                }
            )
            prev_score = score
        return Response(result)

class ScheduledAnalysisView(APIView):
    """GET/POST /api/analyzer/schedule/

    Both halves resolve the caller's agency and require that the caller actually
    owns ``org_id``. Without that check ``org_id`` is a sequential integer and
    ``email`` is unauthenticated, so anyone could (a) read a brand's
    ``last_run_slug`` — which unlocks the whole ``runs/s/<slug>/`` family — and
    (b) enroll any brand into a recurring analysis whose digest emails land in
    their own inbox.
    """

    permission_classes = [AllowAny]

    @staticmethod
    def _owned_org(email: str, org_id) -> Organization | None:
        """The org ``email`` may act on, or None.

        Delegates to ``access.resolve_scoped_org`` so there is one implementation of
        "which orgs may this caller touch" rather than a private copy that drifts.
        """
        from ..access import resolve_scoped_org

        org, err = resolve_scoped_org(email, org_id)
        return None if err else org

    def get(self, request):
        from core.auth.identity import resolve_request_email

        email, err = resolve_request_email(request)
        if err:
            return err
        org_id = request.query_params.get("org_id")
        if not org_id:
            return Response({"error": "org_id required."}, status=status.HTTP_400_BAD_REQUEST)

        if self._owned_org(email, org_id) is None:
            return Response(
                {"detail": "Brand not found for this account.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        from ..models import ScheduledAnalysis

        schedule = ScheduledAnalysis.objects.filter(organization_id=org_id, email=email).first()
        if schedule is None:
            return Response(None, status=status.HTTP_200_OK)

        from ..serializers import ScheduledAnalysisSerializer

        return Response(ScheduledAnalysisSerializer(schedule).data)

    def post(self, request):
        from core.auth.identity import resolve_request_email

        email, err = resolve_request_email(request)
        if err:
            return err
        org_id = request.data.get("org_id")
        url = request.data.get("url", "").strip()
        brand_name = request.data.get("brand_name", "").strip()
        frequency = request.data.get("frequency", "weekly")
        is_active = request.data.get("is_active", True)
        run_at_raw = request.data.get("run_at")  # optional ISO datetime

        if not org_id or not url:
            return Response({"error": "org_id and url required."}, status=status.HTTP_400_BAD_REQUEST)

        if frequency not in ("once", "weekly", "monthly"):
            return Response(
                {"error": "frequency must be once/weekly/monthly."}, status=status.HTTP_400_BAD_REQUEST
            )

        org = self._owned_org(email, org_id)
        if org is None:
            return Response(
                {"detail": "Brand not found for this account.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Parse explicit run_at when provided, else derive from frequency
        next_run_at = None
        if run_at_raw:
            from django.utils.dateparse import parse_datetime

            parsed = parse_datetime(str(run_at_raw))
            if not parsed:
                return Response(
                    {"error": "run_at must be an ISO datetime."}, status=status.HTTP_400_BAD_REQUEST
                )
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            if parsed <= timezone.now():
                return Response(
                    {"error": "run_at must be in the future."}, status=status.HTTP_400_BAD_REQUEST
                )
            next_run_at = parsed
        else:
            if frequency == "once":
                return Response(
                    {"error": "run_at is required when frequency=once."}, status=status.HTTP_400_BAD_REQUEST
                )
            delta = timedelta(days=7) if frequency == "weekly" else timedelta(days=30)
            next_run_at = timezone.now() + delta

        from ..models import ScheduledAnalysis

        schedule, created = ScheduledAnalysis.objects.update_or_create(
            organization=org,
            email=email,
            defaults={
                "url": url,
                "brand_name": brand_name,
                "frequency": frequency,
                "is_active": is_active,
                "next_run_at": next_run_at,
            },
        )
        from ..serializers import ScheduledAnalysisSerializer

        return Response(
            ScheduledAnalysisSerializer(schedule).data,
            status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED,
        )

class AgentLogView(APIView):
    """GET /runs/s/<slug>/agent-log/  — stub; returns empty entries + integration slots."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import AgentLogEntry
        from ..serializers import AgentLogEntrySerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        entries = AgentLogEntry.objects.filter(analysis_run=run).order_by("-ts")[:100]
        return Response(
            {
                "entries": AgentLogEntrySerializer(entries, many=True).data,
                "integrations": [
                    {
                        "name": "Cloudflare Logpush",
                        "key": "cloudflare",
                        "connected": False,
                        "status": "coming_soon",
                    },
                    {
                        "name": "Vercel Edge Logs",
                        "key": "vercel",
                        "connected": False,
                        "status": "coming_soon",
                    },
                ],
            }
        )
