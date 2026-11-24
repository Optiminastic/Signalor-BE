"""Agent plan access: owners, agency members and strangers.

Regression: an owner who also belonged to an agency got 404 ("Brand not found for
this account") on their own brand, because the check compared the brand against
the agency's email. The Actions page showed "Couldn't load data".

Run: python manage.py test apps.analyzer.tests.test_agent_plan_access --settings=config.settings.test
"""

from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from apps.analyzer.models import AnalysisRun
from apps.organizations.models import Organization

OWNER, AGENCY, STRANGER = "owner@example.test", "agency@example.test", "stranger@example.test"


class AgentPlanAccessTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Scrn Studio", url="https://scrnstudio.in/", owner_email=OWNER)
        self.run = AnalysisRun.objects.create(organization=org, url=org.url, email=OWNER, status="complete")
        self.client = APIClient()

    def plan(self, email):
        return self.client.get(f"/api/analyzer/runs/s/{self.run.slug}/agent/plan/", {"email": email})

    def as_agency_member(self, email):
        contexts = [SimpleNamespace(agency_email=AGENCY)] if email == OWNER else []
        return mock.patch("apps.accounts.agency_utils.get_agency_contexts", return_value=contexts)

    def test_owner_sees_their_plan(self):
        self.assertEqual(self.plan(OWNER).status_code, 200)

    def test_owner_who_is_also_in_an_agency_sees_their_plan(self):
        with self.as_agency_member(OWNER):
            self.assertEqual(self.plan(OWNER).status_code, 200)

    def test_stranger_gets_404(self):
        self.assertEqual(self.plan(STRANGER).status_code, 404)
