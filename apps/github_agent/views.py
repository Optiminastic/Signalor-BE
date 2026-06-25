"""
GitHub Agent API.

Run-scoped endpoints live under ``/api/github/runs/s/<slug>/...`` (resolved by
the public AnalysisRun slug, same convention as the analyzer/blog routes). Two
global endpoints are unscoped: the App install ``callback/`` and the
``webhook/``.
"""

import json
import logging

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analyzer.models import AnalysisRun
from apps.organizations.models import Organization
from core.throttling import ExpensiveThrottle, PollingThrottle

from . import tasks
from .models import GithubFixJob, GithubInstallation
from .services import auth, fixable, fixers, webhook  # noqa: F401  (fixers used in helpers)

logger = logging.getLogger("apps")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _get_run(slug: str) -> AnalysisRun | None:
    return AnalysisRun.objects.filter(slug=slug).select_related("organization").first()


def _org_for_email(email: str) -> Organization | None:
    """Resolve the org that owns this email (mirrors integrations' _get_org_or_400)."""
    email = (email or "").strip().lower()
    if not email:
        return None
    return Organization.objects.filter(owner_email=email).first()


def _active_installation_for_org(org_id) -> GithubInstallation | None:
    if not org_id:
        return None
    return (
        GithubInstallation.objects.filter(is_active=True, organization_id=org_id)
        .order_by("-created_at")
        .first()
    )


def _active_installation_for(run: AnalysisRun) -> GithubInstallation | None:
    qs = GithubInstallation.objects.filter(is_active=True)
    if run.organization_id:
        inst = qs.filter(organization_id=run.organization_id).order_by("-created_at").first()
        if inst:
            return inst
    return qs.filter(connect_slug=run.slug).order_by("-created_at").first()


def _run_fixable_findings(run) -> dict:
    """{finding_code: title} for this run's recommendations the agent can fix."""
    out: dict[str, str] = {}
    for rec in run.recommendations.all():
        code = rec.finding_code
        if fixable.is_agent_fixable(code) and code not in out:
            out[code] = rec.title or code
    return out


def _job_dict(job: GithubFixJob) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "finding_codes": job.finding_codes,
        "pr_number": job.pr_number,
        "pr_url": job.pr_url,
        "files_changed": job.files_changed,
        "reasoning": job.reasoning,
        "error_message": job.error_message,
        "score_before": job.score_before,
        "score_after": job.score_after,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


# --------------------------------------------------------------------------- #
# connect / install
# --------------------------------------------------------------------------- #
class GithubInstallURLView(APIView):
    """GET runs/s/<slug>/install-url/ — URL to send the user to GitHub's install page."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        run = _get_run(slug)
        if not run:
            return Response({"error": "Run not found."}, status=status.HTTP_404_NOT_FOUND)
        if not auth.is_configured() or not settings.GITHUB_APP_SLUG:
            return Response(
                {"error": "GitHub App is not configured on the server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        url = f"https://github.com/apps/{settings.GITHUB_APP_SLUG}/installations/new?state={run.slug}"
        return Response({"install_url": url})


class GithubOrgInstallURLView(APIView):
    """GET install-url/?email=<e> — org-scoped install URL (used during onboarding,
    before any AnalysisRun exists). State is ``org_<orgId>`` so the callback links the
    install to the organization rather than a single run."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        if not auth.is_configured() or not settings.GITHUB_APP_SLUG:
            return Response(
                {"error": "GitHub App is not configured on the server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        org = _org_for_email(request.query_params.get("email", ""))
        if not org:
            return Response(
                {"error": "No organization for this email."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        url = f"https://github.com/apps/{settings.GITHUB_APP_SLUG}/installations/new?state=org_{org.id}"
        return Response({"install_url": url})


class GithubOrgStatusView(APIView):
    """GET status/?email=<e> — org-scoped connection state (onboarding)."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        org = _org_for_email(request.query_params.get("email", ""))
        inst = _active_installation_for_org(org.id) if org else None
        return Response(
            {
                "configured": auth.is_configured(),
                "connected": bool(inst),
                "repo_full_name": inst.repo_full_name if inst else "",
                "repositories": inst.repositories if inst else [],
            }
        )


class GithubCallbackView(APIView):
    """GET callback/ — GitHub redirects here after the user installs the App."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        installation_id = request.query_params.get("installation_id")
        # state is either a run slug (Fixes page) or "org_<id>" (onboarding).
        state = request.query_params.get("state", "")
        frontend = settings.FRONTEND_BASE_URL

        if not installation_id:
            return self._redirect(frontend, state, "error")

        try:
            installation_id = int(installation_id)
            repos = auth.list_installation_repos(installation_id)
        except (ValueError, TypeError) as exc:
            logger.error("GitHub callback failed: %s", exc)
            return self._redirect(frontend, state, "error")

        repo_names = [r.get("full_name", "") for r in repos if r.get("full_name")]
        primary = repo_names[0] if repo_names else ""
        owner = repos[0].get("owner", {}) if repos else {}
        default_branch = repos[0].get("default_branch", "main") if repos else "main"

        # Resolve which org/run this install belongs to from the state.
        if state.startswith("org_"):
            try:
                org_id = int(state[4:])
            except (ValueError, TypeError):
                org_id = None
            connect_slug = ""
        else:
            run = _get_run(state) if state else None
            org_id = run.organization_id if run else None
            connect_slug = state

        GithubInstallation.objects.update_or_create(
            installation_id=installation_id,
            defaults={
                "organization_id": org_id,
                "connect_slug": connect_slug,
                "account_login": owner.get("login", ""),
                "account_type": owner.get("type", ""),
                "repo_full_name": primary,
                "repositories": repo_names,
                "default_branch": default_branch,
                "is_active": True,
            },
        )
        return self._redirect(frontend, state, "connected")

    @staticmethod
    def _redirect(frontend: str, state: str, result: str):
        from django.shortcuts import redirect

        if state.startswith("org_"):
            target = f"{frontend}/onboarding/company-info?github={result}"
        elif state:
            target = f"{frontend}/dashboard/{state}?github={result}"
        else:
            target = f"{frontend}/dashboard?github={result}"
        return redirect(target)


class GithubDisconnectView(APIView):
    """POST runs/s/<slug>/disconnect/ — deactivate the linked installation."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        run = _get_run(slug)
        if not run:
            return Response({"error": "Run not found."}, status=status.HTTP_404_NOT_FOUND)
        inst = _active_installation_for(run)
        if inst:
            inst.is_active = False
            inst.save(update_fields=["is_active", "updated_at"])
        return Response({"disconnected": True})


# --------------------------------------------------------------------------- #
# status / fix / jobs
# --------------------------------------------------------------------------- #
class GithubStatusView(APIView):
    """GET runs/s/<slug>/status/ — connection state + recent fix jobs."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        run = _get_run(slug)
        if not run:
            return Response({"error": "Run not found."}, status=status.HTTP_404_NOT_FOUND)
        inst = _active_installation_for(run)
        jobs = GithubFixJob.objects.filter(analysis_run=run).order_by("-created_at")[:20]

        # AI fixability triage — only when a repo is connected (the label is only
        # actionable then). Cached per run, so this LLM call is rare.
        fixability: dict = {}
        if inst:
            from .services.fixability import classify_fixability

            findings = [
                {"finding_code": r.finding_code, "title": r.title, "description": r.description}
                for r in run.recommendations.all()
                if r.finding_code
            ]
            fixability = classify_fixability(slug, inst.repo_profile or {}, findings)

        return Response(
            {
                "configured": auth.is_configured(),
                "connected": bool(inst),
                "repo_full_name": inst.repo_full_name if inst else "",
                "repositories": inst.repositories if inst else [],
                "supported_findings": _run_fixable_findings(run),
                "fixability": fixability,
                "jobs": [_job_dict(j) for j in jobs],
            }
        )


class GithubFixView(APIView):
    """POST runs/s/<slug>/fix/ {finding_codes:[...]} — open a fix PR."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        run = _get_run(slug)
        if not run:
            return Response({"error": "Run not found."}, status=status.HTTP_404_NOT_FOUND)

        inst = _active_installation_for(run)
        if not inst:
            return Response(
                {"error": "No GitHub repo connected for this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_codes = request.data.get("finding_codes") or []
        if not isinstance(raw_codes, list):
            return Response({"error": "finding_codes must be a list."}, status=status.HTTP_400_BAD_REQUEST)
        codes = [c for c in raw_codes if fixable.is_agent_fixable(c)]
        if not codes:
            return Response(
                {"error": "No auto-fixable findings in request."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Dedup: drop codes already covered by an in-flight (pending/running/open) job.
        inflight = GithubFixJob.objects.filter(
            analysis_run=run,
            status__in=[
                GithubFixJob.Status.PENDING,
                GithubFixJob.Status.RUNNING,
                GithubFixJob.Status.OPEN,
            ],
        )
        busy = {c for j in inflight for c in (j.finding_codes or [])}
        fresh = [c for c in codes if c not in busy]
        if not fresh:
            return Response(
                {"error": "A pull request for these findings is already open."},
                status=status.HTTP_409_CONFLICT,
            )

        # One PR per finding: a job (and its own branch/PR) per code.
        created = []
        for code in fresh:
            job = GithubFixJob.objects.create(
                installation=inst,
                analysis_run=run,
                finding_codes=[code],
                score_before=run.composite_score,
                status=GithubFixJob.Status.PENDING,
            )
            tasks.start_fix_job(job.id)
            created.append({"finding_code": code, "job_id": job.id, "status": job.status})
        return Response({"jobs": created}, status=status.HTTP_202_ACCEPTED)


class GithubJobsView(APIView):
    """GET runs/s/<slug>/jobs/ and jobs/<id>/ — poll fix-job status."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug, job_id=None):
        run = _get_run(slug)
        if not run:
            return Response({"error": "Run not found."}, status=status.HTTP_404_NOT_FOUND)
        if job_id is not None:
            job = GithubFixJob.objects.filter(analysis_run=run, pk=job_id).first()
            if not job:
                return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
            return Response(_job_dict(job))
        jobs = GithubFixJob.objects.filter(analysis_run=run).order_by("-created_at")[:50]
        return Response({"jobs": [_job_dict(j) for j in jobs]})


# --------------------------------------------------------------------------- #
# webhook
# --------------------------------------------------------------------------- #
@method_decorator(csrf_exempt, name="dispatch")
class GithubWebhookView(APIView):
    """POST webhook/ — GitHub events. Signature-verified, no auth/throttle."""

    permission_classes = [AllowAny]

    def post(self, request):
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not webhook.verify_signature(request.body, signature):
            return Response({"error": "Invalid signature."}, status=status.HTTP_401_UNAUTHORIZED)
        event = request.headers.get("X-GitHub-Event", "")
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return Response({"error": "Invalid payload."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            webhook.handle_event(event, payload)
        except Exception as exc:  # noqa: BLE001
            logger.error("Webhook handling error (%s): %s", event, exc)
        return Response({"ok": True})
