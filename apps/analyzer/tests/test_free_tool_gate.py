"""Regression tests for the free audit tool's anonymous analyze path.

The URL-analyzer marketing tool submits ``/api/analyzer/analyze/`` with NO
email — "no sign-up required" is the product promise — holding only an
onboarding token. With ``SUBSCRIPTION_REQUIRED=true`` (production) the
subscription gate used to 403 every such scan with "Email is required",
which the tool can never satisfy. Anonymous scans must pass the subscription
gate; identified callers must still be held to it.
"""

import os
from unittest.mock import patch

from django.test import TestCase

from apps.analyzer.models import AnalysisRun
from apps.analyzer.onboarding_security import mint_token

SUB_REQUIRED_ENV = {"SUBSCRIPTION_REQUIRED": "true"}

# The Django test client's REMOTE_ADDR; onboarding tokens are IP-bound.
TEST_CLIENT_IP = "127.0.0.1"


@patch.dict(os.environ, SUB_REQUIRED_ENV)
class AnonymousFreeToolTests(TestCase):
    def _analyze(self, payload):
        return self.client.post(
            "/api/analyzer/analyze/",
            payload,
            content_type="application/json",
            headers={"x-onboarding-token": mint_token(TEST_CLIENT_IP)},
        )

    def test_anonymous_scan_with_token_starts_a_run(self):
        with patch("apps.analyzer.views.runs.start_analysis_task") as task:
            resp = self._analyze({"url": "https://example.com", "run_type": "single_page"})
        self.assertEqual(resp.status_code, 201, resp.content)
        run = AnalysisRun.objects.get(pk=resp.json()["id"])
        self.assertEqual(run.email, "")
        self.assertIsNone(run.organization)
        task.assert_called_once_with(run.id)

    def test_identified_caller_without_subscription_is_still_blocked(self):
        resp = self._analyze(
            {
                "url": "https://example.com",
                "run_type": "single_page",
                "email": "nosub@example.com",
            }
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("subscription", resp.json()["error"].lower())

    def test_anonymous_scan_without_token_is_rejected(self):
        resp = self.client.post(
            "/api/analyzer/analyze/",
            {"url": "https://example.com", "run_type": "single_page"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)
