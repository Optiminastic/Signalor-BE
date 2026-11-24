"""Celery-backed tasks for the analyzer app.

Currently only one task lives here — the sitemap audit, which used to run
inside a daemon thread. Migrating it to Celery means:

  - The web process returns immediately (broker hands the job to a worker).
  - Workers can scale horizontally without spinning more web processes.
  - Crashes don't take down a web worker thread.

Other background work in apps.analyzer.tasks still uses threading.Thread;
migrate per-task as needed.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("apps")


@shared_task(name="analyzer.run_sitemap_audit", bind=True, max_retries=2)
def run_sitemap_audit_task(self, audit_id: int) -> None:
    """Run the sitemap audit pipeline for a given SitemapAudit row.

    Mirrors what the old threading-based runner did:
      - close stale DB connections (worker may have idle ones)
      - dispatch to pipeline.sitemap_audit.run_sitemap_audit
      - on crash, mark the row as FAILED so the FE shows an error state
        instead of a permanently-stuck "running" row.
    """
    from django.db import close_old_connections

    from .models import SitemapAudit
    from .pipeline.sitemap_audit import run_sitemap_audit

    close_old_connections()
    try:
        run_sitemap_audit(audit_id)
    except Exception as exc:
        logger.exception("sitemap audit %d failed: %s", audit_id, exc)
        try:
            SitemapAudit.objects.filter(pk=audit_id).update(
                status=SitemapAudit.Status.FAILED,
            )
        except Exception:
            logger.exception("sitemap audit %d: also failed to mark row as FAILED", audit_id)
        raise


# ── Auto-backlinks scheduling (Celery Beat) ──────────────────────────────────
# Celery Beat replaces the hourly `run_backlink_schedules` cron. Beat fires
# `dispatch_due_backlinks` on a fixed interval (see CELERY_BEAT_SCHEDULE); it
# reads the dynamic per-brand BacklinkSchedule rows and fans out one
# `generate_backlinks_for_brand` task per due brand — so batches run in parallel
# on workers with retries, instead of serially in one short-lived cron process.
# The management command `run_backlink_schedules` is kept as a manual fallback.


def _latest_completed_run_for(sched):
    """Latest completed AnalysisRun for the brand (org preferred, else email)."""
    from .models import AnalysisRun

    qs = AnalysisRun.objects.filter(status=AnalysisRun.Status.COMPLETE)
    if sched.organization_id:
        qs = qs.filter(organization_id=sched.organization_id)
    elif sched.email:
        qs = qs.filter(email=sched.email)
    else:
        return None
    return qs.order_by("-created_at").first()


@shared_task(name="analyzer.dispatch_due_backlinks", bind=True)
def dispatch_due_backlinks(self, limit: int = 50) -> dict:
    """Beat entrypoint: enqueue a per-brand generation task for every schedule
    whose ``next_run_at`` is due, claiming each by bumping ``next_run_at`` +24h so
    a later Beat tick can't double-dispatch it before the task finishes."""
    from datetime import timedelta

    from django.db import close_old_connections
    from django.utils import timezone

    from .models import BacklinkSchedule

    close_old_connections()
    now = timezone.now()
    due = list(
        BacklinkSchedule.objects.filter(is_active=True, next_run_at__lte=now).order_by(
            "next_run_at"
        )[:limit]
    )
    dispatched = 0
    for sched in due:
        # Atomic claim — only dispatch if the row is still due, so a racing tick
        # (or a second beat process) can't enqueue the same brand twice.
        claimed = BacklinkSchedule.objects.filter(pk=sched.pk, next_run_at__lte=now).update(
            next_run_at=now + timedelta(days=1)
        )
        if not claimed:
            continue
        generate_backlinks_for_brand.delay(sched.pk)
        dispatched += 1
    logger.info("dispatch_due_backlinks: %d/%d schedule(s) dispatched", dispatched, len(due))
    return {"due": len(due), "dispatched": dispatched}


@shared_task(
    name="analyzer.generate_backlinks_for_brand",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
    acks_late=True,
)
def generate_backlinks_for_brand(self, schedule_id: int) -> dict:
    """Generate + publish the 5-blog batch for one brand's schedule.

    ``run_auto_backlinks`` is idempotent per day (``auto_can_add_today``), so a
    retry after a partial publish won't duplicate. Records the batch result back
    onto the schedule row on success.
    """
    from django.db import close_old_connections
    from django.utils import timezone

    from .models import BacklinkSchedule
    from .services.backlink_engine import run_auto_backlinks

    close_old_connections()
    sched = BacklinkSchedule.objects.filter(pk=schedule_id).first()
    if sched is None or not sched.is_active:
        return {"schedule_id": schedule_id, "skipped": "inactive-or-missing"}

    run = _latest_completed_run_for(sched)
    if run is None:
        logger.warning("backlink schedule %s: no completed AnalysisRun — skipping", sched.email)
        return {"schedule_id": schedule_id, "skipped": "no-run"}

    result = run_auto_backlinks(run)
    created = len(result.get("created", []))
    sched.run_slug = run.slug
    sched.last_run_at = timezone.now()
    sched.last_batch_count = created
    sched.save(update_fields=["run_slug", "last_run_at", "last_batch_count", "updated_at"])
    logger.info(
        "backlink batch for %s: created=%d skipped=%s",
        sched.email,
        created,
        bool(result.get("skipped")),
    )
    return {"email": sched.email, "created": created, "skipped": bool(result.get("skipped"))}
