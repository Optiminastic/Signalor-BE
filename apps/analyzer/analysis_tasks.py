"""RabbitMQ-backed Celery task for the full analyze / re-analyze pipeline.

Lives in ``analysis_tasks`` (not ``celery_tasks``) so it is autodiscovered by
the RabbitMQ app (``config.celery_rabbit:analysis_app``) and NOT by the Redis
app — keeping analysis on RabbitMQ and the sitemap audit on Redis.

Bound with ``@analysis_app.task`` (not ``@shared_task``, which would also
register it on the Redis app). On failure the run is marked FAILED and the task
returns WITHOUT re-raising, so Celery does not retry — re-running a partial,
non-idempotent analysis would re-spend LLM / DataForSEO credits.
"""

from __future__ import annotations

import logging

from config.celery_rabbit import analysis_app

logger = logging.getLogger("apps")


@analysis_app.task(name="analyzer.run_analysis", bind=True)
def run_analysis_task(self, run_id: int) -> None:
    """Run the single-page analysis pipeline for ``run_id`` on a worker."""
    from django.db import close_old_connections

    from .models import AnalysisRun
    from .tasks import run_single_page_analysis

    close_old_connections()
    try:
        run_single_page_analysis(run_id)
    except Exception as exc:
        logger.exception("analysis run %d failed on worker: %s", run_id, exc)
        # Backstop: the pipeline marks FAILED in its own except blocks, but if a
        # crash escapes it, make sure the FE doesn't see a permanently-stuck run.
        try:
            AnalysisRun.objects.filter(pk=run_id).update(
                status=AnalysisRun.Status.FAILED,
                error_message=str(exc)[:500],
            )
        except Exception:
            logger.exception("analysis run %d: also failed to mark row as FAILED", run_id)
        # No re-raise → no Celery retry.
