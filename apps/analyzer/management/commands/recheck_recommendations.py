"""
Daily re-check of suggested fixes (Tasks).

For each project's latest completed analysis run, re-verify every still-open,
on-page recommendation against the live site (reusing the same verifier the
manual "Verify" button uses), record the result as an AutoFixJob so the Tasks
page shows it as Done/open, then re-prioritize the remaining open fixes and flag
a single "priority fix of the day".

Light re-check only — it does NOT re-run prompts / AI visibility / a full
analysis. Off-page pillars (entity, ai_visibility) and findings that can't be
re-verified by re-crawl are skipped. The job only flags & ranks; it never writes
fixes to the user's site.

Usage:
    python manage.py recheck_recommendations [--slug <run_slug>] [--limit N]
Cron (daily, off-peak):
    0 3 * * * cd /path/to/project && python manage.py recheck_recommendations
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.analyzer.models import AnalysisRun, AutoFixJob, Recommendation
from apps.analyzer.pipeline.recommendations import reprioritize_run_recommendations
from apps.analyzer.pipeline.verify import SKIP_RECRAWL
from apps.analyzer.recommendation_verify import (
    begin_html_cache,
    end_html_cache,
    verify_recommendation_fix,
)

logger = logging.getLogger("apps")

OFF_PAGE_PILLARS = {"entity", "ai_visibility"}


class Command(BaseCommand):
    help = "Daily re-check of open recommendations + re-prioritize the Tasks list."

    def add_arguments(self, parser):
        parser.add_argument("--slug", type=str, default="", help="Re-check a single run by slug.")
        parser.add_argument("--limit", type=int, default=0, help="Cap number of projects (0 = all).")

    def handle(self, *args, **options):
        runs = self._select_runs(options.get("slug") or "", options.get("limit") or 0)
        self.stdout.write(f"Re-checking {len(runs)} run(s).")
        total_checked = total_done = 0
        for run in runs:
            try:
                checked, done = self._recheck_run(run)
                total_checked += checked
                total_done += done
            except Exception:
                logger.exception("recheck_recommendations failed for run %s", run.id)
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Re-checked {total_checked} recs across {len(runs)} runs; {total_done} now verified."
            )
        )

    def _select_runs(self, slug: str, limit: int) -> list[AnalysisRun]:
        if slug:
            run = AnalysisRun.objects.filter(slug=slug).first()
            return [run] if run else []
        # Latest COMPLETE run per project (organization). Skip anonymous/free-tool
        # runs with no organization — those have no persistent dashboard Tasks.
        org_ids = list(
            AnalysisRun.objects.filter(status="complete", organization_id__isnull=False)
            .values_list("organization_id", flat=True)
            .distinct()
        )
        runs: list[AnalysisRun] = []
        for org_id in org_ids:
            run = (
                AnalysisRun.objects.filter(organization_id=org_id, status="complete")
                .order_by("-created_at")
                .first()
            )
            if run:
                runs.append(run)
        if limit > 0:
            runs = runs[:limit]
        return runs

    def _recheck_run(self, run: AnalysisRun) -> tuple[int, int]:
        # Already-done recs (latest fix outcome verified/success) are skipped.
        done_ids = set(
            AutoFixJob.objects.filter(
                analysis_run=run,
                status__in=[AutoFixJob.Status.VERIFIED, AutoFixJob.Status.SUCCESS],
            ).values_list("recommendation_id", flat=True)
        )
        open_recs = [
            r
            for r in run.recommendations.all()
            if r.id not in done_ids
            and r.pillar not in OFF_PAGE_PILLARS
            and (r.finding_code or r.finding_key or "") not in SKIP_RECRAWL
        ]

        checked = 0
        newly_done = 0
        updated: list[Recommendation] = []
        now = timezone.now()

        begin_html_cache()
        try:
            for rec in open_recs:
                try:
                    result = verify_recommendation_fix(run, rec)
                except Exception:
                    logger.exception("verify failed (run=%s rec=%s)", run.id, rec.id)
                    continue
                st = result.get("status")
                if st == "verified":
                    job_status = AutoFixJob.Status.VERIFIED
                    newly_done += 1
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
                    logger.exception("AutoFixJob create failed (run=%s rec=%s)", run.id, rec.id)
                rec.last_checked_at = now
                updated.append(rec)
                checked += 1
        finally:
            end_html_cache()

        if updated:
            Recommendation.objects.bulk_update(updated, ["last_checked_at"])

        # Re-rank the still-open recs and flag the top fix.
        reprioritize_run_recommendations(run)
        return checked, newly_done
