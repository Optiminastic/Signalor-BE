import logging
import os

from django.apps import AppConfig

logger = logging.getLogger("apps")


class AnalyzerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.analyzer"
    verbose_name = "GEO Analyzer"

    def ready(self):
        # Register receivers FIRST — everything below this point returns early in
        # most processes (management commands, tests, non-gunicorn), and signal
        # registration must happen in all of them.
        from . import signals  # noqa: F401

        # Start the weekly-email scheduler in exactly ONE process.
        # - Dev (runserver): Django sets RUN_MAIN=true only in the reloaded child.
        # - Production: gunicorn runs N worker processes, each of which imports the
        #   app and calls ready(). Without --preload every worker would start its
        #   OWN BackgroundScheduler, so the weekly report would be sent N times.
        #   A per-worker in-process cron is the wrong home for this — the correct
        #   long-term fix is a single host/compose cron running
        #   `python manage.py send_weekly_emails` (like run_scheduled_analyses).
        #   Until then we dedupe across workers with a shared-cache (Redis) lock so
        #   only the first worker to boot owns the scheduler.
        # Skip during migrations, tests, shell, and other management commands.
        if os.environ.get("DISABLE_WEEKLY_SCHEDULER") == "true":
            return

        run_main = os.environ.get("RUN_MAIN")
        is_gunicorn = "gunicorn" in os.environ.get("SERVER_SOFTWARE", "")
        if run_main != "true" and not is_gunicorn:
            return

        if not self._acquire_scheduler_lock():
            return

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            from django.core.management import call_command

            scheduler = BackgroundScheduler(timezone="UTC")
            scheduler.add_job(
                lambda: call_command("send_weekly_emails"),
                trigger=CronTrigger(day_of_week="fri", hour=9, minute=0),
                id="weekly_email_report",
                replace_existing=True,
                misfire_grace_time=3600,
            )
            scheduler.start()
            logger.info("Weekly email scheduler started — fires every Friday at 09:00 UTC")
        except Exception:
            logger.exception("Failed to start weekly email scheduler")

    @staticmethod
    def _acquire_scheduler_lock() -> bool:
        """Return True for exactly one process, using an atomic shared-cache add.

        ``cache.add`` only succeeds when the key is absent, so across gunicorn
        workers sharing one Redis cache exactly one call wins. The TTL only needs
        to outlast the multi-worker boot window (all workers call ready() within
        seconds of each other); it then expires harmlessly, and a fresh
        deploy/restart re-acquires cleanly. On cache failure we fail closed (skip)
        rather than risk every worker starting a scheduler and sending duplicates.
        """
        from django.core.cache import cache

        try:
            return bool(cache.add("weekly_email_scheduler_owner", "1", 300))
        except Exception:
            logger.warning("scheduler lock unavailable; skipping scheduler start", exc_info=True)
            return False
