"""Every polled endpoint declares a read scope instead of inheriting the anon cap.

An `AllowAny` view with no `throttle_classes` falls back to
`DEFAULT_THROTTLE_CLASSES`, whose anon rate is **60/hour, keyed per IP and shared
across every unscoped endpoint**. That default is sized for a casual visitor, not
for a client that polls.

This shipped: the analysing screen polls `runs/` every 3.5s (~1000/hour), drained
the shared bucket in about three minutes, and the resulting 429s froze the
progress bar mid-run *and* took the dashboard down with it, because
`organizations/` — which every page load needs — draws on the same bucket.

The rule these pin: if a client polls it, or the dashboard cannot render without
it, it declares its own scope.
"""

from django.test import SimpleTestCase, TestCase
from rest_framework.throttling import AnonRateThrottle

from apps.analyzer.views import AnalysisRunListView
from apps.organizations.views import (
    CheckOrganizationView,
    OrganizationDetailView,
    OrganizationListView,
)
from core.permissions.throttling import PollingThrottle

# Views a client polls, or that the dashboard blocks on.
POLLED_VIEWS = [
    AnalysisRunListView,
    OrganizationListView,
    OrganizationDetailView,
    CheckOrganizationView,
]


class PolledEndpointThrottleTests(SimpleTestCase):
    def test_every_polled_view_declares_its_own_scope(self):
        for view in POLLED_VIEWS:
            with self.subTest(view=view.__name__):
                self.assertTrue(
                    view.throttle_classes,
                    f"{view.__name__} inherits the 60/hour anon default",
                )

    def test_no_polled_view_inherits_the_anon_hourly_cap(self):
        for view in POLLED_VIEWS:
            with self.subTest(view=view.__name__):
                self.assertNotIn(AnonRateThrottle, view.throttle_classes)

    def test_polled_views_use_the_polling_scope(self):
        for view in POLLED_VIEWS:
            with self.subTest(view=view.__name__):
                self.assertIn(PollingThrottle, view.throttle_classes)

    def test_the_polling_budget_outpaces_the_run_poller(self):
        """The analysing screen polls every 3.5s; the scope must allow that.

        Read from ``settings.base`` rather than the active settings: development
        and test null every rate out to disable throttling, so asserting against
        the live value here would pass on an empty string and prove nothing about
        production — which is the only place this ever broke.
        """
        from config.settings.base import REST_FRAMEWORK

        rate = REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["polling"]
        count, period = rate.split("/")
        self.assertEqual(period, "minute")
        # 3.5s between polls is ~18/minute for one open tab. Leave room for the
        # dashboard's own reads alongside it.
        self.assertGreaterEqual(int(count), 60)


class LatestRunProgressTests(TestCase):
    """The poll endpoint: one row, five columns, healed in place."""

    def setUp(self):
        from apps.analyzer.models import AnalysisRun

        self.email = "poller@example.com"
        AnalysisRun.objects.create(url="https://old.com", email=self.email, progress=100)
        self.latest = AnalysisRun.objects.create(
            url="https://new.com", email=self.email, progress=85, status="analyzing"
        )

    def _get(self, **params):
        from django.urls import reverse

        return self.client.get(reverse("analyzer:run-progress"), params)

    def test_returns_the_newest_run_only(self):
        body = self._get(email=self.email).json()
        self.assertTrue(body["found"])
        self.assertEqual(body["slug"], self.latest.slug)
        self.assertEqual(body["progress"], 85)
        self.assertEqual(body["status"], "analyzing")

    def test_payload_carries_nothing_the_bar_does_not_need(self):
        """The whole point: the list endpoint shipped 9 fields per row, times 20.

        ``phase`` earns its place — the two slowest stages hold one percentage
        for minutes, so the text is what tells a user the run is alive.
        """
        self.assertEqual(
            set(self._get(email=self.email).json()),
            {"found", "slug", "status", "progress", "phase"},
        )

    def test_an_account_with_no_runs_is_reported_not_404(self):
        body = self._get(email="nobody@example.com").json()
        self.assertFalse(body["found"])

    def test_a_missing_email_is_400(self):
        self.assertEqual(self._get().status_code, 400)

    def test_another_accounts_run_is_not_returned(self):
        from apps.analyzer.models import AnalysisRun

        AnalysisRun.objects.create(url="https://theirs.com", email="other@example.com")
        self.assertEqual(self._get(email=self.email).json()["slug"], self.latest.slug)

    def test_a_stale_run_is_healed_so_the_bar_recovers(self):
        from unittest.mock import patch

        with patch("apps.analyzer.run_guard.maybe_fail_stale") as heal:
            heal.side_effect = lambda run: run
            self._get(email=self.email)
        heal.assert_called_once()


class AnonCeilingTests(SimpleTestCase):
    """The global anon ceiling has to fit a dashboard, not a brochure site.

    Every signed-in customer currently keys as `anon` — the frontend sends
    cookies, not a Bearer token — and the bucket is per IP, so an office shares
    one. At 60/hour that was ~4 page loads before a hard lockout, and one
    analysing screen drained it in three minutes.
    """

    def _rate(self, scope: str) -> tuple[int, str]:
        from config.settings.base import REST_FRAMEWORK

        count, period = REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][scope].split("/")
        return int(count), period

    def test_the_anon_ceiling_fits_a_dashboard_session(self):
        count, period = self._rate("anon")
        self.assertEqual(period, "hour")
        # ~15 calls per page load; a real session is dozens of loads.
        self.assertGreaterEqual(count, 300)

    def test_an_unauthenticated_customer_is_not_worse_off_than_an_authed_one(self):
        """Until the frontend sends a token, anon *is* the customer path."""
        self.assertGreaterEqual(self._rate("anon")[0], self._rate("user")[0])

    def test_the_expensive_scopes_stay_tight(self):
        """Raising the cheap-read ceiling must not widen the billable surface."""
        for scope in ("expensive", "dataforseo", "ai_chat", "audit_start"):
            with self.subTest(scope=scope):
                count, period = self._rate(scope)
                self.assertEqual(period, "minute")
                self.assertLessEqual(count, 30)

    def test_auth_and_onboarding_sends_stay_tight(self):
        """These gate cost and abuse, not page rendering."""
        self.assertEqual(self._rate("auth_send"), (10, "minute"))
        self.assertEqual(self._rate("onboard_email"), (5, "hour"))
