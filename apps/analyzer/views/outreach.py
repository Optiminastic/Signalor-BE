"""Public endpoints for the sales outreach benchmark.

Deliberately outside the dashboard and behind no login: the people using this
are doing outbound, not managing an account, and a sign-in wall would make it
useless for them.

"No login" is not "no gate", though. Generating a benchmark spends real LLM
credits on every call, so an unauthenticated endpoint that anyone can find is
an invitation to burn the OpenRouter budget. Access is a single shared key held
in ``OUTREACH_BENCHMARK_KEY``: the founder pastes it once, nobody manages
accounts, and a crawler that stumbles onto the URL gets a 403. Reads are open,
so a finished report can be shared by link with a prospect.

Leaving the key unset disables creation entirely, which is the right default
for any environment that has not deliberately turned this on.
"""

from __future__ import annotations

import hmac
import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import ExpensiveThrottle, PollingThrottle

from ..models import AnalysisRun
from ..url_guard import SSRFValidationError, validate_public_url

logger = logging.getLogger("apps")

_KEY_HEADER = "X-Outreach-Key"


def _public_url(raw: str) -> tuple[str, str]:
    """(url, "") for a usable public address, else ("", reason).

    A founder pastes "acme.com", not a scheme, so the scheme is added the same
    way the analyze serializer does it. The SSRF check is the important half:
    this endpoint takes an arbitrary URL from the internet and then fetches it
    server-side, which is exactly the shape that reaches internal addresses and
    cloud metadata endpoints if left unguarded.
    """
    url = raw.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    try:
        return validate_public_url(url), ""
    except SSRFValidationError:
        return "", "That URL can't be analyzed — enter a public website address."


def _configured_key() -> str:
    return (getattr(settings, "OUTREACH_BENCHMARK_KEY", "") or "").strip()


def _key_ok(request) -> bool:
    """Constant-time comparison so the key can't be recovered by timing."""
    expected = _configured_key()
    if not expected:
        return False
    supplied = (request.headers.get(_KEY_HEADER) or "").strip()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def _serialize(run: AnalysisRun) -> dict:
    return {
        "slug": run.slug,
        "url": run.url,
        "brand_name": run.brand_name,
        "status": run.status,
        "progress": run.progress,
        "phase": run.phase,
        "error_message": run.error_message,
        "report": run.outreach_report or {},
    }


class OutreachBenchmarkCreateView(APIView):
    """POST /api/analyzer/outreach/ {url} — start a benchmark for one domain."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request):
        if not _key_ok(request):
            # Same response whether the key is wrong or the feature is off: a
            # probe should not learn which.
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

        raw_url = str(request.data.get("url") or "").strip()
        if not raw_url:
            return Response({"detail": "A url is required."}, status=status.HTTP_400_BAD_REQUEST)

        url, err = _public_url(raw_url)
        if err:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        prompts = request.data.get("prompts")
        pinned = (
            [str(p).strip() for p in prompts if str(p).strip()][:12]
            if isinstance(prompts, list)
            else []
        )

        run = AnalysisRun.objects.create(
            url=url,
            run_type=AnalysisRun.RunType.OUTREACH,
            status=AnalysisRun.Status.PENDING,
            onboarding_prompts=pinned,
        )

        from core import queue

        if queue.is_eager():
            # No broker (local dev): run inline rather than silently never running.
            from ..services.outreach_benchmark import run_outreach_benchmark

            run_outreach_benchmark(run.id)
            run.refresh_from_db()
        else:
            queue.send(queue.OUTREACH_BENCHMARK, run.id)

        return Response(_serialize(run), status=status.HTTP_201_CREATED)


class OutreachBenchmarkDetailView(APIView):
    """GET /api/analyzer/outreach/<slug>/ — poll progress, then read the report.

    Open by design: the slug is an unguessable 8-byte token, and a finished
    benchmark is meant to be sent to the prospect it describes.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        run = (
            AnalysisRun.objects.filter(slug=slug, run_type=AnalysisRun.RunType.OUTREACH)
            .only(
                "slug",
                "url",
                "brand_name",
                "status",
                "progress",
                "phase",
                "error_message",
                "outreach_report",
                "updated_at",
            )
            .first()
        )
        if run is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # Same self-heal as the dashboard's pollers: a worker that dies mid-run
        # would otherwise leave this page spinning forever.
        from ..run_guard import maybe_fail_stale

        run = maybe_fail_stale(run)
        return Response(_serialize(run))
