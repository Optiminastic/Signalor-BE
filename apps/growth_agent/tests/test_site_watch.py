"""
Site watch: daily checks of the brand's homepage, robots.txt, sitemap and Google
clicks, logging only changes. Fetches are faked: nothing goes out.

Run: python manage.py test apps.growth_agent.tests.test_site_watch --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase

from apps.integrations.models import GSCDataSnapshot, Integration

from ..models import AgentActionLog
from ..services.site_watch import checks, watch
from .test_execution_flow import ExecutionTestCase

PAGE = '<html><head><title>Pro Brand - GEO</title><link rel="canonical" href="https://pro.example/"></head></html>'
NOINDEX = '<html><head><title>Pro Brand</title><meta name="robots" content="noindex, nofollow"></head></html>'
OK_STATE = {"url": "https://pro.example", "down": False, "title": "Pro Brand - GEO", "noindex": False}


class CheckRuleTests(SimpleTestCase):
    def test_reads_title_noindex_and_canonical(self):
        self.assertEqual(checks.read_homepage(PAGE)["title"], "Pro Brand - GEO")
        self.assertTrue(checks.read_homepage(NOINDEX)["noindex"])
        self.assertTrue(checks.read_homepage(PAGE, "noindex")["noindex"])

    def test_robots_blocks_by_most_specific_group(self):
        robots = (
            "User-agent: *\nDisallow:\n\nUser-agent: GPTBot\nDisallow: /\n\nUser-agent: Googlebot\nAllow: /\n"
        )
        self.assertEqual(checks.blocked_bots(robots), ["GPTBot"])
        self.assertEqual(
            set(checks.blocked_bots("User-agent: *\nDisallow: /\n")), {*checks.SEARCH_BOTS, *checks.AI_BOTS}
        )

    def test_problems_are_reported_when_they_appear_and_when_fixed(self):
        broken = {**OK_STATE, "noindex": True, "blocked": ["Googlebot"]}
        found = {a.kind: a.severity for a in checks.compare(OK_STATE, broken)}
        self.assertEqual(found, {"noindex_added": "danger", "robots_blocked": "danger"})
        self.assertEqual(checks.compare(broken, broken), [])  # still broken: not repeated
        fixed = {a.kind for a in checks.compare(broken, OK_STATE)}
        self.assertEqual(fixed, {"noindex_removed", "robots_unblocked"})

    def test_down_and_back_up(self):
        down = {"url": "https://pro.example", "down": True, "status": 503}
        self.assertEqual([a.kind for a in checks.compare(OK_STATE, down)], ["site_down"])
        self.assertEqual([a.kind for a in checks.compare(down, OK_STATE)], ["site_back_up"])

    def test_title_change_and_ai_bot_block_are_softer(self):
        changed = {**OK_STATE, "title": "New title", "blocked": ["GPTBot"]}
        found = {a.kind: a.severity for a in checks.compare(OK_STATE, changed)}
        self.assertEqual(found, {"title_changed": "info", "robots_blocked": "attention"})

    def test_clicks_drop(self):
        days = [{"date": f"2026-11-{d:02d}", "clicks": 10} for d in range(1, 8)]
        days += [{"date": f"2026-11-{d:02d}", "clicks": 4} for d in range(8, 15)]
        self.assertEqual(checks.clicks_drop(days)["before"], 70)
        self.assertIsNone(checks.clicks_drop(days[:10]))
        steady = [{"date": f"2026-11-{d:02d}", "clicks": 10} for d in range(1, 15)]
        self.assertIsNone(checks.clicks_drop(steady))


class SiteWatchRunTests(ExecutionTestCase):
    def run_with(self, state):
        with mock.patch.object(watch, "read_state", return_value=state):
            return watch.run_check(self.program)

    def anomalies(self):
        return list(
            AgentActionLog.objects.filter(action=watch.ANOMALY).values_list("detail__kind", flat=True)
        )

    def test_each_change_is_logged_once_with_a_daily_check_line(self):
        self.run_with({**OK_STATE, "blocked": [], "sitemap_missing": False})
        self.assertEqual(self.anomalies(), [])
        self.run_with({"url": "https://pro.example", "down": True, "status": 500})
        self.run_with({"url": "https://pro.example", "down": True, "status": 500})
        self.assertEqual(self.anomalies(), ["site_down"])
        self.assertEqual(AgentActionLog.objects.filter(action=watch.CHECKED).count(), 3)

    def test_a_clicks_drop_alerts_at_most_once_a_week(self):
        gsc = Integration.objects.create(
            organization=self.pro_org, provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE
        )
        days = [{"date": f"2026-11-{d:02d}", "clicks": 10 if d < 8 else 2} for d in range(1, 15)]
        GSCDataSnapshot.objects.create(
            integration=gsc,
            date_start="2026-11-01",
            date_end="2026-11-14",
            sync_status="complete",
            daily_trend=days,
        )
        self.run_with({**OK_STATE, "blocked": [], "sitemap_missing": False})
        self.run_with({**OK_STATE, "blocked": [], "sitemap_missing": False})
        self.assertEqual(self.anomalies(), ["clicks_drop"])

    def test_daily_check_is_queued_at_most_once_a_day(self):
        with mock.patch("apps.growth_agent.analysis_tasks.dispatch_site_check"):
            self.assertTrue(watch.daily(self.program))
            self.assertFalse(watch.daily(self.program))
