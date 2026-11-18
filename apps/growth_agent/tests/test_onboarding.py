"""
"Paste a link" onboarding: brand reuse, plan limit, analysis reuse, scoping.

Run: python manage.py test apps.growth_agent.tests.test_onboarding --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.utils import timezone

from apps.accounts.models import Subscription
from apps.analyzer.models import AnalysisRun
from apps.organizations.models import Organization

from ..services.onboarding import FRESH_ANALYSIS_WINDOW, canonical_url
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase

START_TASK = "apps.growth_agent.services.onboarding.start_analysis_task"
NEW_PRO_EMAIL = "founder@fresh-brand.com"


@mock.patch(START_TASK)
class OnboardTests(GrowthApiTestCase):
    def onboard(self, email, url):
        self._body = {"url": url}
        return self.signed("POST", "/api/growth/brands/", email)

    def make_run(self, org, run_status, age=timedelta(0)):
        run = AnalysisRun.objects.create(
            organization=org, url=org.url, email=org.owner_email, status=run_status
        )
        AnalysisRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - age)
        return run

    def test_rejects_non_url(self, start_task):
        response = self.onboard(PRO_EMAIL, "not a link")
        self.assertEqual(response.status_code, 400)
        start_task.assert_not_called()

    def test_existing_brand_without_analysis_starts_one(self, start_task):
        response = self.onboard(PRO_EMAIL, "https://www.pro.example/pricing")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["created_brand"])
        self.assertEqual(response.data["brand"]["slug"], self.pro_org.slug)
        self.assertEqual(response.data["analysis"]["status"], AnalysisRun.Status.PENDING)
        start_task.assert_called_once()

    def test_reuses_in_flight_analysis(self, start_task):
        run = self.make_run(self.pro_org, AnalysisRun.Status.CRAWLING)
        response = self.onboard(PRO_EMAIL, "pro.example")
        self.assertEqual(response.data["analysis"]["slug"], run.slug)
        start_task.assert_not_called()

    def test_reuses_recent_complete_analysis(self, start_task):
        run = self.make_run(self.pro_org, AnalysisRun.Status.COMPLETE, age=timedelta(days=1))
        response = self.onboard(PRO_EMAIL, "pro.example")
        self.assertEqual(response.data["analysis"]["slug"], run.slug)
        start_task.assert_not_called()

    def test_stale_analysis_triggers_a_new_one(self, start_task):
        stale = FRESH_ANALYSIS_WINDOW + timedelta(days=1)
        old = self.make_run(self.pro_org, AnalysisRun.Status.COMPLETE, age=stale)
        response = self.onboard(PRO_EMAIL, "pro.example")
        self.assertNotEqual(response.data["analysis"]["slug"], old.slug)
        start_task.assert_called_once()

    def test_new_domain_over_plan_limit_is_blocked_without_creating_brand(self, start_task):
        response = self.onboard(PRO_EMAIL, "https://second-brand.example")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Organization.objects.filter(normalized_url="second-brand.example").exists())
        start_task.assert_not_called()

    def test_first_brand_is_created_and_analysed(self, start_task):
        Subscription.objects.create(email=NEW_PRO_EMAIL, plan="pro", status="active")
        response = self.onboard(NEW_PRO_EMAIL, "fresh-brand.com")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["created_brand"])
        org = Organization.objects.get(owner_email=NEW_PRO_EMAIL)
        self.assertEqual(org.url, "https://fresh-brand.com")
        start_task.assert_called_once()

    @mock.patch(
        "apps.growth_agent.services.onboarding.analysis_allowed_for_email", return_value=(False, "No sub.")
    )
    def test_blocked_analysis_never_leaves_an_orphan_brand(self, _allowed, start_task):
        Subscription.objects.create(email=NEW_PRO_EMAIL, plan="pro", status="active")
        response = self.onboard(NEW_PRO_EMAIL, "fresh-brand.com")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Organization.objects.filter(owner_email=NEW_PRO_EMAIL).exists())

    def test_someone_elses_domain_is_not_reused(self, start_task):
        # PRO_EMAIL pasting OTHER_EMAIL's domain must not attach to their brand.
        response = self.onboard(PRO_EMAIL, "other.example")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Organization.objects.filter(normalized_url="other.example").count(), 1)


class AnalysisStatusTests(GrowthApiTestCase):
    def test_reads_latest_run_for_own_brand(self):
        AnalysisRun.objects.create(
            organization=self.pro_org, url=self.pro_org.url, status="analyzing", progress=40
        )
        response = self.signed("GET", f"/api/growth/brands/{self.pro_org.slug}/analysis/", PRO_EMAIL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["analysis"]["progress"], 40)

    def test_cannot_read_someone_elses_analysis(self):
        AnalysisRun.objects.create(organization=self.other_org, url=self.other_org.url, email=OTHER_EMAIL)
        response = self.signed("GET", f"/api/growth/brands/{self.other_org.slug}/analysis/", PRO_EMAIL)
        self.assertEqual(response.status_code, 404)


class CanonicalUrlTests(GrowthApiTestCase):
    def test_normalises_to_scheme_and_host(self):
        self.assertEqual(canonical_url("WWW.Acme.com/pricing?x=1"), "https://www.acme.com")
        self.assertEqual(canonical_url("http://acme.io"), "http://acme.io")

    def test_rejects_non_web_input(self):
        for raw in ["", "hello", "ftp://acme.com", "javascript:alert(1)"]:
            self.assertIsNone(canonical_url(raw), raw)
