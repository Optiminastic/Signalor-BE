"""Weekly loop correctness: no drift, no burst after an outage, no double run.

Run:
    python manage.py test apps.analyzer.tests.test_scheduled_runs
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.analyzer.models import AnalysisRun, ScheduledAnalysis
from apps.analyzer.scheduled_runs import (
    claim_due_schedule,
    execute_scheduled_analysis,
    next_run_after,
)
from apps.organizations.models import Organization

OWNER = "owner@example.com"


def _utc(y, m, d, hh=0, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


class NextRunAfterTests(TestCase):
    def test_anchors_to_the_scheduled_time_not_now(self):
        """The drift bug: `now + 7d` compounds cron latency + run duration into
        every cycle, walking a weekly scan across the clock (~26h/year)."""
        scheduled = _utc(2026, 7, 6, 3, 0)  # a Monday, 03:00
        # The cron fired 12 minutes late and the analysis took 20 minutes.
        now = _utc(2026, 7, 6, 3, 32)

        nxt = next_run_after(scheduled, ScheduledAnalysis.Frequency.WEEKLY, now)

        self.assertEqual(nxt, _utc(2026, 7, 13, 3, 0))  # exactly next Monday 03:00

    def test_catches_up_past_an_outage_without_bursting(self):
        scheduled = _utc(2026, 7, 6, 3, 0)
        now = _utc(2026, 7, 27, 9, 0)  # three weeks later

        nxt = next_run_after(scheduled, ScheduledAnalysis.Frequency.WEEKLY, now)

        self.assertGreater(nxt, now)
        self.assertEqual(nxt, _utc(2026, 8, 3, 3, 0))  # one slot ahead, not three runs

    def test_monthly(self):
        nxt = next_run_after(
            _utc(2026, 7, 6, 3, 0), ScheduledAnalysis.Frequency.MONTHLY, _utc(2026, 7, 6, 3, 5)
        )
        self.assertEqual(nxt, _utc(2026, 8, 5, 3, 0))


class ClaimTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.example", owner_email=OWNER)
        # The org post_save receiver already enrolled it; make it due.
        self.schedule = ScheduledAnalysis.objects.get(organization=self.org)
        self.schedule.next_run_at = timezone.now() - timedelta(minutes=1)
        self.schedule.save(update_fields=["next_run_at"])

    def test_claim_reschedules_into_the_future(self):
        claimed = claim_due_schedule(self.schedule.id)

        self.assertIsNotNone(claimed)
        self.schedule.refresh_from_db()
        self.assertGreater(self.schedule.next_run_at, timezone.now())

    def test_second_claim_loses(self):
        """RabbitMQ is at-least-once and the cron can overlap itself, so the same
        schedule can be dispatched twice."""
        self.assertIsNotNone(claim_due_schedule(self.schedule.id))
        self.assertIsNone(claim_due_schedule(self.schedule.id))

    def test_not_due_is_not_claimable(self):
        self.schedule.next_run_at = timezone.now() + timedelta(days=1)
        self.schedule.save(update_fields=["next_run_at"])
        self.assertIsNone(claim_due_schedule(self.schedule.id))

    def test_inactive_is_not_claimable(self):
        self.schedule.is_active = False
        self.schedule.save(update_fields=["is_active"])
        self.assertIsNone(claim_due_schedule(self.schedule.id))

    def test_once_deactivates_instead_of_rescheduling(self):
        self.schedule.frequency = ScheduledAnalysis.Frequency.ONCE
        self.schedule.save(update_fields=["frequency"])

        claim_due_schedule(self.schedule.id)

        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.is_active)


class ExecuteTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.example", owner_email=OWNER)
        self.schedule = ScheduledAnalysis.objects.get(organization=self.org)
        self.schedule.next_run_at = timezone.now() - timedelta(minutes=1)
        self.schedule.save(update_fields=["next_run_at"])

    @patch("apps.analyzer.tasks.run_single_page_analysis")
    @patch("apps.analyzer.tasks._kickoff_sitemap_audit")
    def test_runs_once_and_only_once(self, _audit, _analyze):
        self.assertTrue(execute_scheduled_analysis(self.schedule.id))
        # A duplicate delivery must not start a second analysis.
        self.assertFalse(execute_scheduled_analysis(self.schedule.id))

        self.assertEqual(AnalysisRun.objects.filter(organization=self.org).count(), 1)

    @patch("apps.analyzer.tasks.run_single_page_analysis")
    @patch("apps.analyzer.tasks._kickoff_sitemap_audit")
    def test_records_the_run_slug(self, _audit, _analyze):
        execute_scheduled_analysis(self.schedule.id)

        run = AnalysisRun.objects.get(organization=self.org)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.last_run_slug, run.slug)
        self.assertIsNotNone(self.schedule.last_run_at)

    @patch("apps.analyzer.tasks._kickoff_sitemap_audit")
    def test_a_crashing_analysis_leaves_the_schedule_in_the_future(self, _audit):
        """Reschedule-before-run: otherwise a crash leaves next_run_at in the past
        and the next cron tick re-fires the same brand every 30 minutes."""
        with patch("apps.analyzer.tasks.run_single_page_analysis", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                execute_scheduled_analysis(self.schedule.id)

        self.schedule.refresh_from_db()
        self.assertGreater(self.schedule.next_run_at, timezone.now())
