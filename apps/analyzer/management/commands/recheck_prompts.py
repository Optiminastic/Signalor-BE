"""
Management command: python manage.py recheck_prompts

Re-fires all (or stale) PromptTrack rows across 4 AI engines and appends
new PromptResult rows so the citation trend chart accumulates over time.

Usage:
  python manage.py recheck_prompts               # all tracks older than 24h
  python manage.py recheck_prompts --hours 12    # staleness threshold
  python manage.py recheck_prompts --slug abc123 # one specific run
  python manage.py recheck_prompts --all         # force-recheck every track

Schedule via cron (daily at 02:00):
  0 2 * * * cd /path/to/ranking-be && python manage.py recheck_prompts

Schedule via Windows Task Scheduler:
  Program: python.exe
  Args:    manage.py recheck_prompts
  Start in: C:\\path\\to\\ranking-be
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

logger = logging.getLogger("apps")


class Command(BaseCommand):
    help = "Re-check all tracked prompts across AI engines to build citation trend history"

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Only recheck tracks whose latest result is older than N hours (default: 24)",
        )
        parser.add_argument(
            "--slug",
            type=str,
            default=None,
            help="Limit to a single analysis run by slug",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Recheck every track regardless of age",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=4,
            help="Parallel workers (default: 4)",
        )

    def handle(self, *args, **options):
        from apps.analyzer.models import PromptTrack, AnalysisRun
        from apps.analyzer.pipeline.prompt_tracker import recheck_track

        hours = options["hours"]
        slug = options["slug"]
        force_all = options["all"]
        workers = options["workers"]

        # Build queryset (skip soft-deleted prompts — they shouldn't burn API calls)
        qs = (
            PromptTrack.objects
                .filter(deleted_at__isnull=True)
                .select_related("analysis_run")
                .prefetch_related("results")
        )
        if slug:
            try:
                run = AnalysisRun.objects.get(slug=slug)
                qs = qs.filter(analysis_run=run)
                self.stdout.write(f"Filtering to run slug={slug} ({qs.count()} tracks)")
            except AnalysisRun.DoesNotExist:
                self.stderr.write(f"No run found with slug '{slug}'")
                return

        if not force_all:
            cutoff = timezone.now() - timedelta(hours=hours)
            # Keep tracks where latest result is older than cutoff (or has no results)
            stale_ids = []
            for track in qs:
                latest = track.results.order_by("-checked_at").first()
                if latest is None or latest.checked_at < cutoff:
                    stale_ids.append(track.pk)
            qs = PromptTrack.objects.filter(pk__in=stale_ids).select_related("analysis_run")
            self.stdout.write(f"Found {len(stale_ids)} stale tracks (threshold: {hours}h)")
        else:
            self.stdout.write(f"Force-rechecking all {qs.count()} tracks")

        tracks = list(qs)
        if not tracks:
            self.stdout.write("Nothing to recheck.")
            return

        total_created = 0
        errors = 0

        def _do_recheck(track):
            run = track.analysis_run
            brand_name = run.brand_name or run.url
            brand_url = run.url
            return recheck_track(track, brand_name, brand_url)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_do_recheck, t): t for t in tracks}
            for future in as_completed(futures):
                track = futures[future]
                try:
                    created = future.result()
                    total_created += created
                    self.stdout.write(
                        f"  ✓ Track #{track.pk} [{track.prompt_text[:50]}] → {created} results"
                    )
                except Exception as exc:
                    errors += 1
                    self.stderr.write(f"  ✗ Track #{track.pk} failed: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {total_created} new PromptResult rows across {len(tracks)} tracks "
                f"({errors} errors)."
            )
        )
