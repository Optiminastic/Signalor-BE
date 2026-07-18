"""One analysis at a time per brand.

Run:
    python manage.py test apps.analyzer.tests.test_run_guard
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.analyzer.models import AnalysisRun
from apps.analyzer.run_guard import active_run_for
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
