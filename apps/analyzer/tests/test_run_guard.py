"""One analysis at a time per brand.

Run:
    python manage.py test apps.analyzer.tests.test_run_guard
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.analyzer.models import AnalysisRun
from apps.analyzer.run_guard import (
    STALE_RUNNING_TIMEOUT,
    active_run_for,
    maybe_fail_stale,
)
from apps.organizations.models import Organization

OWNER = "owner@example.com"


class ActiveRunForTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.example", owner_email=OWNER)

    def _run(self, status, url="https://acme.example", **kw):
        return AnalysisRun.objects.create(
            organization=self.org, url=url, email=OWNER, run_type="single_page", status=status, **kw
        )

    def test_none_when_no_active_run(self):
        self._run(AnalysisRun.Status.COMPLETE)
        self.assertIsNone(active_run_for(self.org))

    def test_finds_in_flight_run_on_any_url(self):
        # A different URL for the same brand must still count — the old guard only
        # matched the same URL, which is exactly the gap being closed.
        self._run(AnalysisRun.Status.CRAWLING, url="https://acme.example/other")
        self.assertIsNotNone(active_run_for(self.org))

    def test_each_in_flight_status_blocks(self):
        for st in (
            AnalysisRun.Status.PENDING,
            AnalysisRun.Status.CRAWLING,
            AnalysisRun.Status.ANALYZING,
            AnalysisRun.Status.SCORING,
        ):
            AnalysisRun.objects.filter(organization=self.org).delete()
            self._run(st)
            self.assertIsNotNone(active_run_for(self.org), f"{st} should block")

    def test_stale_run_does_not_block(self):
        """A crash that never marked FAILED must not wedge the brand forever."""
        run = self._run(AnalysisRun.Status.CRAWLING)
        AnalysisRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(hours=2))
        self.assertIsNone(active_run_for(self.org))

    def test_none_for_anonymous_run(self):
        self.assertIsNone(active_run_for(None))

    def test_scoped_to_the_brand(self):
        other = Organization.objects.create(
            name="Other", url="https://other.example", owner_email="x@example.com"
        )
        self._run(AnalysisRun.Status.CRAWLING)
        self.assertIsNone(active_run_for(other))


class MaybeFailStaleTests(TestCase):
    """A silently-orphaned run must self-heal to FAILED so the loading screen,
    which polls the runs list, recovers instead of spinning on it forever.
    """

    def setUp(self):
        self.org = Organization.objects.create(
            name="Acme", url="https://acme.example", owner_email=OWNER
        )

    def _run(self, status):
        return AnalysisRun.objects.create(
            organization=self.org,
            url="https://acme.example",
            email=OWNER,
            run_type="single_page",
            status=status,
        )

    def _age(self, run, delta):
        # updated_at is auto_now, so bypass the ORM save hook to backdate it.
        AnalysisRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - delta)
        run.refresh_from_db()

    def test_silent_running_run_is_failed(self):
        run = self._run(AnalysisRun.Status.ANALYZING)
        self._age(run, STALE_RUNNING_TIMEOUT + timedelta(minutes=1))
        maybe_fail_stale(run)
        run.refresh_from_db()
        self.assertEqual(run.status, AnalysisRun.Status.FAILED)
        self.assertTrue(run.error_message)

    def test_fresh_running_run_is_left_alone(self):
        run = self._run(AnalysisRun.Status.ANALYZING)
        self._age(run, timedelta(seconds=30))
        maybe_fail_stale(run)
        run.refresh_from_db()
        self.assertEqual(run.status, AnalysisRun.Status.ANALYZING)

    def test_pending_run_gets_the_longer_grace(self):
        # Past the running timeout but within the pending grace — must NOT fail.
        run = self._run(AnalysisRun.Status.PENDING)
        self._age(run, STALE_RUNNING_TIMEOUT + timedelta(minutes=1))
        maybe_fail_stale(run)
        run.refresh_from_db()
        self.assertEqual(run.status, AnalysisRun.Status.PENDING)

    def test_terminal_run_is_untouched(self):
        run = self._run(AnalysisRun.Status.COMPLETE)
        self._age(run, timedelta(hours=2))
        maybe_fail_stale(run)
        run.refresh_from_db()
        self.assertEqual(run.status, AnalysisRun.Status.COMPLETE)
