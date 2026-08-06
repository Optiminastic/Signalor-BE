"""The top-bar live-visitors poll.

Two properties matter more than the numbers it returns:

1. It is brand-scoped. It is polled with an `org_id` from the URL, so a stale or
   guessed id must not read another account's traffic.
2. It never 500s. It sits in the top bar of every dashboard page, so a GA outage
   has to degrade to a reason code — and the upstream error text must not leak
   into the response body.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.analyzer.models import CrawlerHit
from apps.integrations.models import Integration
from apps.integrations.views.live import LiveVisitorsView
from apps.organizations.models import Organization
from core.permissions.throttling import PollingThrottle

_OWNER = "owner@example.com"
_STRANGER = "stranger@example.com"

_SNAPSHOT = {"active_users": 12, "countries": [{"code": "IN", "name": "India", "users": 7}]}

# The test settings use DummyCache, which silently drops every write — any
# caching assertion would pass vacuously without this.
_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "live-visitors-tests",
    }
}


@override_settings(CACHES=_LOCMEM)
class LiveVisitorsTestCase(TestCase):
    """Overridden at class level, not per method, so `setUp` clears the *real*
    cache. With the override on the method, setUp cleared DummyCache while the
    test ran against locmem — and since org ids repeat under transaction
    rollback, one class's cached snapshot silently satisfied the next one."""

    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name="Brand", url="https://brand.example", owner_email=_OWNER)
        self.url = reverse("integrations:live-visitors")

    def _get(self, **params):
        return self.client.get(self.url, {"email": _OWNER, "org_id": self.org.id, **params})

    def _connect_ga(self):
        return Integration.objects.create(
            organization=self.org,
            provider=Integration.Provider.GOOGLE_ANALYTICS,
            is_active=True,
            metadata={"property_id": "123456"},
        )


class AuthorizationTests(LiveVisitorsTestCase):
    def test_requires_email(self):
        self.assertEqual(self.client.get(self.url).status_code, 400)

    def test_other_owners_org_is_forbidden(self):
        resp = self.client.get(self.url, {"email": _STRANGER, "org_id": self.org.id})
        self.assertEqual(resp.status_code, 403)

    def test_unknown_org_is_not_found(self):
        resp = self.client.get(self.url, {"email": _OWNER, "org_id": self.org.id + 9999})
        self.assertEqual(resp.status_code, 404)

    def test_poll_is_throttled(self):
        # A 30s poll from every open tab: an unthrottled route here is a
        # self-inflicted load generator.
        self.assertIn(PollingThrottle, LiveVisitorsView.throttle_classes)

    def test_never_returns_the_crawler_ingest_token(self):
        # That token is a write capability for the org's crawler feed. It has no
        # business on a poll that every dashboard page makes.
        self.assertNotIn("ingest_token", self._get().content.decode())


class DegradationTests(LiveVisitorsTestCase):
    def test_ga_not_connected_still_returns_200_with_bots(self):
        CrawlerHit.objects.create(organization=self.org, bot="gptbot", path="/", hit_at=timezone.now())
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["humans"]["available"])
        self.assertEqual(body["humans"]["reason"], "not_connected")
        self.assertTrue(body["bots"]["available"])
        self.assertEqual(body["bots"]["total_hits"], 1)

    def test_no_property_selected(self):
        Integration.objects.create(
            organization=self.org,
            provider=Integration.Provider.GOOGLE_ANALYTICS,
            is_active=True,
            metadata={},
        )
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["humans"]["reason"], "no_property")

    def test_ga_failure_degrades_without_leaking_the_error(self):
        self._connect_ga()
        secret = "quota exceeded for project 8817261 token abc123"
        with patch("apps.integrations.views.live.fetch_realtime_snapshot", side_effect=RuntimeError(secret)):
            resp = self._get()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["humans"]["available"])
        self.assertEqual(body["humans"]["reason"], "api_error")
        self.assertNotIn(secret, resp.content.decode())
        self.assertNotIn("8817261", resp.content.decode())

    def test_revoked_access_reports_auth_expired(self):
        self._connect_ga()
        with patch(
            "apps.integrations.views.live.fetch_realtime_snapshot",
            side_effect=RuntimeError("invalid_grant: Token has been expired or revoked."),
        ):
            resp = self._get()
        self.assertEqual(resp.json()["humans"]["reason"], "auth_expired")


class BotWindowTests(LiveVisitorsTestCase):
    def test_only_hits_inside_the_window_count(self):
        now = timezone.now()
        CrawlerHit.objects.create(
            organization=self.org, bot="gptbot", path="/pricing", hit_at=now - timedelta(minutes=5)
        )
        CrawlerHit.objects.create(
            organization=self.org, bot="claudebot", path="/old", hit_at=now - timedelta(minutes=45)
        )
        bots = self._get().json()["bots"]
        self.assertEqual(bots["total_hits"], 1)
        self.assertEqual([r["bot"] for r in bots["rows"]], ["gptbot"])
        self.assertEqual(bots["rows"][0]["path"], "/pricing")
        self.assertEqual(bots["rows"][0]["label"], "GPT Bot (OpenAI)")

    def test_ever_seen_distinguishes_quiet_from_never_installed(self):
        self.assertFalse(self._get().json()["bots"]["ever_seen"])
        CrawlerHit.objects.create(
            organization=self.org,
            bot="gptbot",
            path="/",
            hit_at=timezone.now() - timedelta(hours=6),
        )
        bots = self._get().json()["bots"]
        self.assertTrue(bots["ever_seen"])
        self.assertEqual(bots["total_hits"], 0)

    def test_other_orgs_hits_are_not_counted(self):
        other = Organization.objects.create(name="Other", url="https://other.example", owner_email=_STRANGER)
        CrawlerHit.objects.create(organization=other, bot="gptbot", path="/", hit_at=timezone.now())
        self.assertEqual(self._get().json()["bots"]["total_hits"], 0)


class CachingTests(LiveVisitorsTestCase):
    def test_repeat_polls_collapse_to_one_upstream_call(self):
        # The whole point of the 20s TTL: N open dashboard tabs must not become
        # N calls against GA's realtime quota.
        self._connect_ga()
        with (
            patch("apps.integrations.views.live.fetch_realtime_snapshot", return_value=_SNAPSHOT) as realtime,
            patch("apps.integrations.views.live.fetch_today_sources", return_value=[]),
        ):
            self._get()
            self._get()
            self._get()
        self.assertEqual(realtime.call_count, 1)

    def test_failure_is_negatively_cached(self):
        # GA allows ~10 realtime errors per property per hour. Retrying on every
        # poll would burn that in minutes and lock the property out entirely.
        self._connect_ga()
        with patch(
            "apps.integrations.views.live.fetch_realtime_snapshot",
            side_effect=RuntimeError("boom"),
        ) as realtime:
            self._get()
            self._get()
        self.assertEqual(realtime.call_count, 1)


class RealtimeParsingTests(TestCase):
    """The aggregate row is the only correct headline — see the service module."""

    def _response(self, rows):
        class _V:
            def __init__(self, value):
                self.value = value

        class _Row:
            def __init__(self, dims, metric):
                self.dimension_values = [_V(d) for d in dims]
                self.metric_values = [_V(metric)]

        class _Resp:
            def __init__(self):
                self.rows = [_Row(d, m) for d, m in rows]
                self.property_quota = None

        return _Resp()

    def test_total_row_is_extracted_and_excluded_from_countries(self):
        from apps.integrations.services.ga4_realtime import _parse_realtime

        parsed = _parse_realtime(
            self._response(
                [
                    (["India", "IN"], "7"),
                    (["United States", "US"], "5"),
                    (["RESERVED_TOTAL", "RESERVED_TOTAL"], "9"),
                ]
            )
        )
        # 9, not 12: activeUsers is de-duplicated, so summing countries is wrong.
        self.assertEqual(parsed["active_users"], 9)
        self.assertEqual([c["code"] for c in parsed["countries"]], ["IN", "US"])

    def test_missing_total_row_falls_back_to_the_sum(self):
        from apps.integrations.services.ga4_realtime import _parse_realtime

        parsed = _parse_realtime(self._response([(["India", "IN"], "7")]))
        self.assertEqual(parsed["active_users"], 7)
