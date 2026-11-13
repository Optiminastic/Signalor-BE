"""Tests for POST /api/growth-leads/ (public website-growth lead form)."""

import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from config.settings.base import REST_FRAMEWORK as BASE_REST_FRAMEWORK
from core.permissions.throttling import AuthSendThrottle

from .growth_leads import expand_services, normalize_website
from .models import GrowthLead

URL = "/api/growth-leads/"
BASE_RATES = BASE_REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]

VALID = {
    "full_name": "Ada Lovelace",
    "email": "Ada@Acme.com",
    "company_name": "Acme",
    "website": "acme.com",
    "service": "geo",
    "goals": "Show up in ChatGPT answers for our category.",
}


class GrowthLeadCreateTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @patch("apps.accounts.growth_leads.EmailMessage")
    def test_creates_lead_with_server_stamped_fields(self, mock_email):
        res = self.client.post(URL, VALID, format="json")

        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data["ok"])
        self.assertEqual(res.data["status"], "new")
        lead = GrowthLead.objects.get()
        self.assertEqual(str(lead.lead_id), str(res.data["lead_id"]))
        self.assertEqual(lead.email, "ada@acme.com")
        self.assertEqual(lead.website, "https://acme.com")
        self.assertEqual(lead.services, ["geo"])
        self.assertEqual(lead.status, GrowthLead.Status.NEW)
        self.assertIsNotNone(lead.created_at)
        mock_email.return_value.send.assert_called_once()

    @patch("apps.accounts.growth_leads.EmailMessage")
    def test_all_of_the_above_expands_services(self, _):
        res = self.client.post(URL, {**VALID, "service": "all"}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(GrowthLead.objects.get().services, ["seo", "geo", "aeo"])

    @patch("apps.accounts.growth_leads.EmailMessage")
    def test_goals_optional(self, _):
        payload = {k: v for k, v in VALID.items() if k != "goals"}
        res = self.client.post(URL, payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(GrowthLead.objects.get().goals, "")

    def test_rejects_missing_required_fields(self):
        res = self.client.post(URL, {}, format="json")
        self.assertEqual(res.status_code, 400)
        for field in ("full_name", "email", "company_name", "website", "service"):
            self.assertIn(field, res.data)
        self.assertFalse(GrowthLead.objects.exists())

    def test_rejects_bad_email_url_and_service(self):
        res = self.client.post(
            URL,
            {**VALID, "email": "nope", "website": "not a url", "service": "ppc"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("email", res.data)
        self.assertIn("website", res.data)
        self.assertIn("service", res.data)

    @patch("apps.accounts.growth_leads.EmailMessage")
    def test_client_cannot_set_status_or_lead_id(self, _):
        forged = str(uuid.uuid4())
        res = self.client.post(
            URL, {**VALID, "status": "won", "lead_id": forged, "services": ["x"]}, format="json"
        )
        self.assertEqual(res.status_code, 201)
        lead = GrowthLead.objects.get()
        self.assertEqual(lead.status, GrowthLead.Status.NEW)
        self.assertNotEqual(str(lead.lead_id), forged)
        self.assertEqual(lead.services, ["geo"])

    @patch("apps.accounts.growth_leads.EmailMessage")
    def test_email_failure_still_saves_lead(self, mock_email):
        mock_email.return_value.send.side_effect = RuntimeError("smtp down")
        res = self.client.post(URL, VALID, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(GrowthLead.objects.count(), 1)

    # development/test settings null every throttle rate, so pin the production
    # rate for auth_send (config.settings.base) on the class for this test.
    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
    @patch.object(AuthSendThrottle, "THROTTLE_RATES", {"auth_send": BASE_RATES["auth_send"]})
    @patch("apps.accounts.growth_leads.EmailMessage")
    def test_rate_limited_per_ip(self, _):
        cache.clear()
        codes = [self.client.post(URL, VALID, format="json").status_code for _ in range(11)]
        self.assertEqual(codes[:10], [201] * 10)
        self.assertEqual(codes[10], 429)
        cache.clear()


class HelperTests(TestCase):
    def test_normalize_website(self):
        self.assertEqual(normalize_website("acme.com"), "https://acme.com")
        self.assertEqual(normalize_website(" http://acme.co.uk/x "), "http://acme.co.uk/x")
        for bad in ("", "localhost", "http://127.0.0.1", "ftp://acme.com", "acme"):
            with self.assertRaises(DjangoValidationError):
                normalize_website(bad)

    def test_expand_services(self):
        self.assertEqual(expand_services("seo"), ["seo"])
        self.assertEqual(expand_services("all"), ["seo", "geo", "aeo"])
