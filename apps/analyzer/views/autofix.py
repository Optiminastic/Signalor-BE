"""Automated and semi-automated fixes applied to the site."""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.subscription_utils import (
    analysis_allowed_for_email,
    analysis_count_limit_reached,
    autofix_limit_reached,
    autofix_regen_limit_reached,
    plan_limit_error_response_dict,
    prompt_batch_would_exceed,
)
from core.permissions.throttling import (
    ExpensiveThrottle,
)

from ..models import (
    AnalysisRun,
    GeoImprovement,
    Recommendation,
)
from ..tasks import start_analysis_task
from ._shared import (
    _budget_denied,
    logger,
)


def _quota_email(run, org) -> str:
    """Server-derived identity the quota is charged to.

    Never the request-body email (untrusted); the run's owner, falling back to
    the brand owner, is what plan limits and the budget fuse key off.
    """
    return (run.email or "").strip().lower() or (getattr(org, "owner_email", "") or "").strip().lower()


class AutoFixView(APIView):
    """GET/POST /api/analyzer/runs/s/<slug>/auto-fix/"""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        """Return fix status for all recommendations in this run, including cross-run fixes."""
        from django.shortcuts import get_object_or_404

        from ..models import AutoFixJob

        run = get_object_or_404(AnalysisRun, slug=slug)

        # 1. Fixes for this specific run
        jobs = AutoFixJob.objects.filter(analysis_run=run).order_by("-created_at")
        seen = {}
        for job in jobs:
            if job.recommendation_id not in seen:
                seen[job.recommendation_id] = {
                    "recommendation_id": job.recommendation_id,
                    "status": job.status,
                    "message": job.response_data.get("message", job.error_message or ""),
                    "fix_type": job.fix_type,
                }

        if run.organization:
            prev_fixes = (
                AutoFixJob.objects.filter(analysis_run__organization=run.organization, status="success")
                .exclude(analysis_run=run)
                .select_related("recommendation")
            )
            fixed_titles = set()
            for job in prev_fixes:
                if job.recommendation and job.recommendation.title:
                    fixed_titles.add(job.recommendation.title.strip().lower())

            # Match current run's recommendations by title
            if fixed_titles:
                for rec in run.recommendations.all():
                    if rec.id not in seen and rec.title.strip().lower() in fixed_titles:
                        seen[rec.id] = {
                            "recommendation_id": rec.id,
                            "status": "success",
                            "message": "Previously fixed in an earlier analysis.",
                            "fix_type": "content_enhance",
                        }

        return Response(list(seen.values()))

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..auto_fix import apply_fixes

        run = get_object_or_404(AnalysisRun, slug=slug)
        recommendation_ids = request.data.get("recommendation_ids", [])
        email = request.data.get("email", "").lower().strip()

        if not recommendation_ids or not email:
            return Response(
                {"error": "recommendation_ids and email are required."}, status=status.HTTP_400_BAD_REQUEST
            )

        # Match Shopify vs WordPress to the analyzed URL (org may have both connected)
        org = run.organization
        if not org:
            return Response(
                {"error": "No organization linked to this run."}, status=status.HTTP_400_BAD_REQUEST
            )

        from ..integration_resolve import resolve_store_integration_for_run

        integration = resolve_store_integration_for_run(org, run.url or "")

        if not integration:
            return Response(
                {"error": "No WordPress or Shopify integration connected. Connect one first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        recommendations = list(Recommendation.objects.filter(id__in=recommendation_ids, analysis_run=run))

        # Every fix in the batch is an LLM generation — gate on the USD fuse
        # and the per-brand count quota before spending anything.
        denied = _budget_denied(run)
        if denied is not None:
            return denied
        reached, quota_msg = autofix_limit_reached(_quota_email(run, org), org, additional=len(recommendations))
        if reached:
            return Response(plan_limit_error_response_dict(quota_msg), status=status.HTTP_403_FORBIDDEN)

        results = apply_fixes(run, integration, recommendations)
        return Response(results)

class AutoFixPreviewView(APIView):
    """POST /api/analyzer/runs/s/<slug>/auto-fix/preview/ — generate fix preview without applying.

    Persists the preview as an AutoFixJob with status='preview'. If a preview
    already exists for the same (run, recommendation), returns it without
    re-running the LLM. Pass force=true to regenerate.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..auto_fix import generate_fix_preview
        from ..models import AutoFixJob

        run = get_object_or_404(AnalysisRun, slug=slug)
        rec_id = request.data.get("recommendation_id")
        email = request.data.get("email", "").lower().strip()
        force = bool(request.data.get("force"))

        if not rec_id or not email:
            return Response(
                {"error": "recommendation_id and email required."}, status=status.HTTP_400_BAD_REQUEST
            )

        org = run.organization
        if not org:
            return Response({"error": "No organization linked."}, status=status.HTTP_400_BAD_REQUEST)

        from ..integration_resolve import resolve_store_integration_for_run

        integration = resolve_store_integration_for_run(org, run.url or "")

        if not integration:
            return Response({"error": "No store integration connected."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rec = Recommendation.objects.get(id=rec_id, analysis_run=run)
        except Recommendation.DoesNotExist:
            return Response({"error": "Recommendation not found."}, status=status.HTTP_404_NOT_FOUND)

        if not force:
            cached = (
                AutoFixJob.objects.filter(
                    analysis_run=run,
                    recommendation=rec,
                    status=AutoFixJob.Status.PREVIEW,
                )
                .order_by("-created_at")
                .first()
            )
            if cached and cached.response_data:
                return Response({**cached.response_data, "cached": True})

        # Past the cache — this call WILL generate. Gate on the USD fuse, the
        # per-recommendation regen cap (the realistic abuse loop), and the
        # per-brand count quota, in that order of specificity.
        denied = _budget_denied(run)
        if denied is not None:
            return denied
        quota_email = _quota_email(run, org)
        reached, quota_msg = autofix_regen_limit_reached(quota_email, rec)
        if reached:
            return Response(plan_limit_error_response_dict(quota_msg), status=status.HTTP_403_FORBIDDEN)
        reached, quota_msg = autofix_limit_reached(quota_email, org)
        if reached:
            return Response(plan_limit_error_response_dict(quota_msg), status=status.HTTP_403_FORBIDDEN)

        preview = generate_fix_preview(run, integration, rec)
        try:
            # One row per actual generation (not update_or_create): each row is
            # the audit unit the regen cap and the 30-day quota count. The
            # cached read above keeps returning the latest row.
            AutoFixJob.objects.create(
                analysis_run=run,
                recommendation=rec,
                status=AutoFixJob.Status.PREVIEW,
                integration=integration,
                fix_type=preview.get("fix_type", "content"),
                response_data=preview,
            )
        except Exception:
            logger.exception("Failed to persist AutoFixJob preview (run=%s rec=%s)", run.id, rec.id)
        return Response({**preview, "cached": False})

class AutoFixApproveView(APIView):
    """POST /api/analyzer/runs/s/<slug>/auto-fix/approve/ — apply a previewed fix via plugin."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..auto_fix import apply_approved_fix
        from ..models import AutoFixJob

        run = get_object_or_404(AnalysisRun, slug=slug)
        rec_id = request.data.get("recommendation_id")
        approved_content = request.data.get("content", "")
        fix_type = request.data.get("fix_type", "content")

        if not rec_id:
            return Response({"error": "recommendation_id required."}, status=status.HTTP_400_BAD_REQUEST)

        org = run.organization
        if not org:
            return Response({"error": "No organization linked."}, status=status.HTTP_400_BAD_REQUEST)

        from ..integration_resolve import resolve_store_integration_for_run

        integration = resolve_store_integration_for_run(org, run.url or "")

        if not integration:
            return Response({"error": "No store integration connected."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rec = Recommendation.objects.get(id=rec_id, analysis_run=run)
        except Recommendation.DoesNotExist:
            return Response({"error": "Recommendation not found."}, status=status.HTTP_404_NOT_FOUND)

        result = apply_approved_fix(run, integration, rec, approved_content, fix_type)

        # Audit row — must include integration FK; failures here must not mask a successful apply
        raw_status = result.get("status") or "failed"
        allowed = {s.value for s in AutoFixJob.Status}
        job_status = raw_status if raw_status in allowed else "failed"
        err_msg = result.get("message", "") if raw_status in ("failed", "error", "skipped") else ""
        try:
            AutoFixJob.objects.create(
                analysis_run=run,
                recommendation=rec,
                integration=integration,
                fix_type=fix_type,
                status=job_status,
                # Marks this row as an apply of already-generated content, so
                # the auto-fix generation quota does not count it a second time
                # (see subscription_utils._autofix_generation_qs).
                payload_sent={"source": "approve"},
                response_data=result,
                error_message=err_msg,
            )
        except Exception:
            logger.exception(
                "Failed to persist AutoFixJob after apply_approved_fix (run=%s rec=%s)",
                run.id,
                rec.id,
            )

        return Response(result)

class AutoFixVerifyView(APIView):
    """POST /api/analyzer/runs/s/<slug>/auto-fix/verify/ — re-fetch the page and verify the fix heuristically."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import AutoFixJob
        from ..recommendation_verify import verify_recommendation_fix

        run = get_object_or_404(AnalysisRun, slug=slug)
        rec_id = request.data.get("recommendation_id")

        if not rec_id:
            return Response({"error": "recommendation_id required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rec = Recommendation.objects.get(id=rec_id, analysis_run=run)
        except Recommendation.DoesNotExist:
            return Response({"error": "Recommendation not found."}, status=status.HTTP_404_NOT_FOUND)

        result = verify_recommendation_fix(run, rec)
        st = result.get("status")
        if st == "verified":
            job_status = AutoFixJob.Status.VERIFIED
        elif st == "manual":
            job_status = AutoFixJob.Status.MANUAL
        else:
            job_status = AutoFixJob.Status.FAILED
        try:
            AutoFixJob.objects.create(
                analysis_run=run,
                recommendation=rec,
                integration=None,
                fix_type=result.get("fix_type") or "verification",
                status=job_status,
                response_data=result,
                error_message="" if st == "verified" else (result.get("message") or "")[:500],
            )
        except Exception:
            logger.exception("Failed to create verify record (run=%s rec=%s)", run.id, rec.id)

        return Response(
            {
                "recommendation_id": rec.id,
                "status": result.get("status", "failed"),
                "message": result.get("message", ""),
                "fix_type": result.get("fix_type", "verification"),
            }
        )

class GeoImprovementsView(APIView):
    """GET /api/analyzer/runs/s/<slug>/geo-improvements/"""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..pipeline.geo_improvement import get_all_recommendations_fix_plan

        run = get_object_or_404(AnalysisRun, slug=slug)
        qs = GeoImprovement.objects.filter(analysis_run=run).order_by("-created_at")
        improvements = []
        for imp in qs:
            improvements.append(
                {
                    "id": imp.id,
                    "provider": imp.provider,
                    "improvement_type": imp.improvement_type,
                    "status": imp.status,
                    "resource_type": imp.resource_type,
                    "resource_id": imp.resource_id,
                    "resource_title": imp.resource_title,
                    "field_name": imp.field_name,
                    "old_value": imp.old_value,
                    "new_value": imp.new_value,
                    "error_message": imp.error_message or "",
                    "applied_at": imp.applied_at.isoformat() if imp.applied_at else None,
                }
            )
        applied_count = sum(1 for i in improvements if i["status"] == "applied")
        failed_count = sum(1 for i in improvements if i["status"] == "failed")
        suggested_fixes = get_all_recommendations_fix_plan(run)
        return Response(
            {
                "total": len(improvements),
                "applied_count": applied_count,
                "failed_count": failed_count,
                "improvements": improvements,
                "suggested_fixes": suggested_fixes,
            }
        )

class ApplyGeoFixesAndReanalyzeView(APIView):
    """POST /api/analyzer/runs/s/<slug>/apply-geo-fixes/"""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..pipeline.geo_improvement import get_all_recommendations_fix_plan, run_geo_improvements

        run = get_object_or_404(AnalysisRun, slug=slug)
        reanalyze = bool(request.data.get("reanalyze", False))

        # GEO improvements are LLM-generated per fix — same fuse as auto-fix.
        denied = _budget_denied(run)
        if denied is not None:
            return denied

        applied = run_geo_improvements(run.id)
        plan_len = len(get_all_recommendations_fix_plan(run))

        next_run_payload = None
        if reanalyze:
            allowed, sub_err = analysis_allowed_for_email(run.email or "")
            if not allowed:
                return Response({"error": sub_err}, status=status.HTTP_403_FORBIDDEN)

            batch_exceeds, batch_msg = prompt_batch_would_exceed(run.email or "", 10)
            if batch_exceeds:
                return Response(
                    plan_limit_error_response_dict(batch_msg),
                    status=status.HTTP_403_FORBIDDEN,
                )

            count_reached, count_msg = analysis_count_limit_reached(run.email or "", run.organization)
            if count_reached:
                return Response(
                    plan_limit_error_response_dict(count_msg),
                    status=status.HTTP_403_FORBIDDEN,
                )

            # One analysis at a time per brand — reuse the in-flight run instead
            # of stacking a second re-analysis on the same org.
            from ..run_guard import active_run_for

            in_flight = active_run_for(run.organization)
            if in_flight is not None:
                next_run_payload = {"id": in_flight.id, "slug": in_flight.slug}
            else:
                new_run = AnalysisRun.objects.create(
                    organization=run.organization,
                    url=run.url,
                    brand_name=run.brand_name or "",
                    country=run.country or "",
                    email=run.email or "",
                    run_type=run.run_type,
                    status=AnalysisRun.Status.PENDING,
                )
                start_analysis_task(new_run.id)
                next_run_payload = {"id": new_run.id, "slug": new_run.slug}

        return Response(
            {
                "message": "GEO fixes applied.",
                "requested_fixes": plan_len,
                "applied_count": applied,
                "next_run": next_run_payload,
            }
        )
