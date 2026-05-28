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
