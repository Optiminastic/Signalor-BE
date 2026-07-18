"""Backfill weekly analysis schedules for brands created before auto-enrollment.

New brands are enrolled by the ``Organization`` post_save receiver
(``apps/analyzer/signals.py``). This command covers everything that predates it —
which, until now, was every brand: ``ScheduledAnalysis`` rows were only ever
written by ``ScheduledAnalysisView``, and no client calls it.

Usage:
    python manage.py enroll_scheduled_analyses --dry-run     # always look first
    python manage.py enroll_scheduled_analyses --limit 10    # then a small cohort
    python manage.py enroll_scheduled_analyses

Enrolling turns on real recurring work: each schedule runs a full LLM-backed
analysis every week. Start with --limit and confirm the queue and spend look sane
before enrolling everyone.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef

from apps.analyzer.enrollment import enroll_organization, initial_next_run_at
from apps.analyzer.models import ScheduledAnalysis
from apps.organizations.models import Organization

# Spread first runs across a full week. These brands have no expectation of a
# particular first-run time, and bunching them into one cron tick would fire N
# concurrent analyses.
BACKFILL_SPREAD_DAYS = 7


class Command(BaseCommand):
    help = "Enroll organizations without a weekly analysis schedule."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
        parser.add_argument("--limit", type=int, default=0, help="Cap brands enrolled (0 = all).")

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]
        limit = opts["limit"] or 0

        orgs = self._unenrolled()
        if limit > 0:
            orgs = orgs[:limit]
        orgs = list(orgs)

        if not orgs:
            self.stdout.write("No unenrolled brands with a URL. Nothing to do.")
            return

        if dry_run:
            for org in orgs:
                when = initial_next_run_at(org.id, spread_days=BACKFILL_SPREAD_DAYS)
                self.stdout.write(f"  would enroll org {org.id} ({org.url}) → {when:%Y-%m-%d %H:%M} UTC")
            self.stdout.write(self.style.SUCCESS(f"Would enroll {len(orgs)} brand(s). No changes written."))
            return

        enrolled = 0
        for org in orgs:
            schedule = enroll_organization(org, spread_days=BACKFILL_SPREAD_DAYS)
            if schedule is None:
                continue
            enrolled += 1
            self.stdout.write(f"  org {org.id} ({org.url}) → {schedule.next_run_at:%Y-%m-%d %H:%M} UTC")

        self.stdout.write(self.style.SUCCESS(f"Enrolled {enrolled} brand(s)."))

    @staticmethod
    def _unenrolled():
        """Brands with a URL and no schedule for their owner."""
        has_schedule = ScheduledAnalysis.objects.filter(
            organization=OuterRef("pk"),
            email=OuterRef("owner_email"),
        )
        return Organization.objects.exclude(url="").filter(~Exists(has_schedule)).order_by("pk")
