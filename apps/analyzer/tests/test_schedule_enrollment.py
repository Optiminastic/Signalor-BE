"""Weekly analysis enrollment + schedule endpoint authorization.

Run:
    python manage.py test apps.analyzer.tests.test_schedule_enrollment
(dotted path — ``apps`` is a namespace package, so directory-label discovery
fails repo-wide.)
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.analyzer.enrollment import enroll_organization, initial_next_run_at
from apps.analyzer.models import ScheduledAnalysis
from apps.organizations.models import Organization

OWNER = "owner@example.com"
ATTACKER = "attacker@evil.com"


class ScheduleAuthorizationTests(TestCase):
    """``org_id`` is a sequential int and ``email`` is unauthenticated, so without
    an ownership check anyone could read a brand's ``last_run_slug`` — which
    unlocks the whole ``runs/s/<slug>/`` family — or point its recurring analysis
    at a URL of their choosing and receive the digests."""

    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.example", owner_email=OWNER)
        self.url = reverse("analyzer:schedule")

    def test_get_rejects_non_owner(self):
        res = self.client.get(self.url, {"email": ATTACKER, "org_id": self.org.id})
        self.assertEqual(res.status_code, 404)

    def test_get_does_not_leak_run_slug_to_non_owner(self):
        ScheduledAnalysis.objects.filter(organization=self.org).update(last_run_slug="s3cr3tslug")
        res = self.client.get(self.url, {"email": ATTACKER, "org_id": self.org.id})
        self.assertNotIn("s3cr3tslug", res.content.decode())

    def test_post_rejects_non_owner(self):
        res = self.client.post(
            self.url,
            {"email": ATTACKER, "org_id": self.org.id, "url": "https://evil.example", "frequency": "weekly"},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 404)
        # The attacker must not have redirected the brand's analysis at their URL.
        self.assertFalse(ScheduledAnalysis.objects.filter(organization=self.org, email=ATTACKER).exists())
        self.assertEqual(ScheduledAnalysis.objects.get(organization=self.org).url, "https://acme.example")

    def test_owner_can_read_and_write(self):
        res = self.client.get(self.url, {"email": OWNER, "org_id": self.org.id})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["frequency"], "weekly")

        res = self.client.post(
            self.url,
            {"email": OWNER, "org_id": self.org.id, "url": "https://acme.example", "frequency": "monthly"},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(ScheduledAnalysis.objects.get(organization=self.org).frequency, "monthly")

    def test_missing_params_rejected(self):
        self.assertEqual(self.client.get(self.url, {"email": OWNER}).status_code, 400)


class AutoEnrollmentTests(TestCase):
    """Nothing enrolled brands before this, so the weekly engine never had work."""

    def test_new_org_with_url_is_enrolled_weekly(self):
        org = Organization.objects.create(name="A", url="https://a.example", owner_email=OWNER)
        schedule = ScheduledAnalysis.objects.get(organization=org)
        self.assertEqual(schedule.email, OWNER)
        self.assertEqual(schedule.frequency, ScheduledAnalysis.Frequency.WEEKLY)
        self.assertTrue(schedule.is_active)

    def test_new_org_without_url_is_skipped(self):
        org = Organization.objects.create(name="B", url="", owner_email=OWNER)
        self.assertFalse(ScheduledAnalysis.objects.filter(organization=org).exists())

    def test_saving_existing_org_does_not_duplicate(self):
        org = Organization.objects.create(name="C", url="https://c.example", owner_email=OWNER)
        org.name = "C renamed"
        org.save()
        self.assertEqual(ScheduledAnalysis.objects.filter(organization=org).count(), 1)

    def test_enroll_is_idempotent_and_preserves_user_cadence(self):
        org = Organization.objects.create(name="D", url="https://d.example", owner_email=OWNER)
        ScheduledAnalysis.objects.filter(organization=org).update(frequency="monthly")

        enroll_organization(org)

        self.assertEqual(ScheduledAnalysis.objects.filter(organization=org).count(), 1)
        self.assertEqual(ScheduledAnalysis.objects.get(organization=org).frequency, "monthly")

    def test_first_runs_are_staggered(self):
        """Without jitter every backfilled brand fires in one cron tick, starting
        N concurrent LLM-backed analyses."""
        times = {initial_next_run_at(i, spread_days=7) for i in range(1, 25)}
        self.assertEqual(len(times), 24)
