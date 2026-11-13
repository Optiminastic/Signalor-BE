"""
Weekly indexing (slice 1): the report from Search Console checks, the weekly
check, and the Agent log entries. No network: snapshots are created directly
and the check itself is never run (its dispatch is patched).

Run: python manage.py test apps.growth_agent.tests.test_indexing --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from apps.integrations.models import GSCIndexStatusSnapshot, Integration

from ..models import AgentActionLog, GrowthProgram
from ..services.indexing import reasons, report, weekly
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase

DISPATCH = "apps.growth_agent.analysis_tasks.dispatch_indexing_check"
SITE = "sc-domain:brand-pro.com"


def page(url, on_google, state):
    return {"url": url, "on_google": on_google, "coverage_state": state}


class ReasonTests(SimpleTestCase):
    def test_google_states_get_plain_words_and_who_can_fix_them(self):
        self.assertEqual(reasons.classify("Excluded by 'noindex' tag").key, "noindex")
        self.assertTrue(reasons.classify("Excluded by 'noindex' tag").fixable)
        self.assertEqual(reasons.classify("Crawled - currently not indexed").key, "crawled_not_indexed")
        self.assertEqual(reasons.classify("Crawled – currently not indexed").key, "crawled_not_indexed")
        self.assertFalse(reasons.classify("Discovered - currently not indexed").fixable)
        self.assertEqual(
            reasons.classify("Duplicate, Google chose different canonical than user").key,
            "canonical_mismatch",
        )
        self.assertEqual(reasons.classify("Something new from Google").key, "other")


class IndexingTestCase(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(
            organization=self.pro_org, created_by_email=PRO_EMAIL, status=GrowthProgram.Status.ACTIVE
        )

    def connect_gsc(self, site=SITE):
        return Integration.objects.create(
            organization=self.pro_org,
            provider="google_search_console",
            metadata={"site_url": site} if site else {},
        )

    def snapshot(self, integration, pages, *, days_ago=0, status="complete"):
        indexed = sum(1 for p in pages if p["on_google"])
        snap = GSCIndexStatusSnapshot.objects.create(
            integration=integration,
            pages=pages,
            checked_count=len(pages),
            indexed_count=indexed,
            not_indexed_count=len(pages) - indexed,
            sync_status=status,
        )
        GSCIndexStatusSnapshot.objects.filter(pk=snap.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
        return snap


class ReportTests(IndexingTestCase):
    def test_without_search_console_the_report_says_so(self):
        self.assertEqual(report.build(self.pro_org)["status"], "not_connected")
        self.connect_gsc(site=None)
        self.assertEqual(report.build(self.pro_org)["status"], "no_property")

    def test_report_groups_unindexed_pages_by_reason_and_compares_with_last_week(self):
        gsc = self.connect_gsc()
        self.snapshot(
            gsc,
            [
                page("/a", True, "Submitted and indexed"),
                page("/b", False, "Discovered - currently not indexed"),
            ],
            days_ago=7,
        )
        self.snapshot(
            gsc,
            [
                page("/a", False, "Excluded by 'noindex' tag"),
                page("/b", True, "Submitted and indexed"),
                page("/c", False, "Excluded by 'noindex' tag"),
            ],
        )
        built = report.build(self.pro_org)
        self.assertEqual((built["status"], built["indexed"], built["not_indexed"]), ("ready", 1, 2))
        self.assertEqual(built["reasons"][0]["key"], "noindex")
        self.assertEqual(built["reasons"][0]["count"], 2)
        self.assertEqual(built["indexed_change"], 0)
        self.assertEqual(built["newly_indexed"], ["/b"])
        self.assertEqual(built["dropped"], ["/a"])

    def test_a_running_check_shows_as_checking_with_last_results_kept(self):
        gsc = self.connect_gsc()
        self.snapshot(gsc, [page("/a", True, "Submitted and indexed")], days_ago=7)
        self.snapshot(gsc, [], status="syncing")
        built = report.build(self.pro_org)
        self.assertEqual((built["status"], built["indexed"]), ("checking", 1))


class WeeklyTests(IndexingTestCase):
    def test_a_check_starts_when_the_last_one_is_a_week_old_and_only_once(self):
        gsc = self.connect_gsc()
        self.snapshot(gsc, [page("/a", True, "Submitted and indexed")], days_ago=8)
        with mock.patch(DISPATCH) as dispatch, self.captureOnCommitCallbacks(execute=True):
            weekly.weekly_round(self.program)
            weekly.weekly_round(self.program)  # a second cron run soon after
        dispatch.assert_called_once_with(gsc.id)
        self.assertEqual(AgentActionLog.objects.filter(action=weekly.STARTED).count(), 1)

    def test_no_check_when_the_last_one_is_recent_or_search_console_is_missing(self):
        with mock.patch(DISPATCH) as dispatch:
            weekly.weekly_round(self.program)  # not connected
            gsc = self.connect_gsc()
            self.snapshot(gsc, [page("/a", True, "Submitted and indexed")], days_ago=2)
            weekly.weekly_round(self.program)
        dispatch.assert_not_called()

    def test_a_finished_check_is_logged_once_with_its_numbers(self):
        gsc = self.connect_gsc()
        self.snapshot(
            gsc, [page("/a", True, "Submitted and indexed"), page("/b", False, "Blocked by robots.txt")]
        )
        self.assertTrue(weekly.log_finished(self.program))
        self.assertFalse(weekly.log_finished(self.program))
        entry = AgentActionLog.objects.get(action=weekly.CHECKED)
        self.assertEqual((entry.detail["indexed"], entry.detail["checked"]), (1, 2))
        self.assertEqual(entry.detail["top_reason"], "Blocked by robots.txt")


class IndexingApiTests(IndexingTestCase):
    def url(self, suffix=""):
        return f"/api/growth/programs/{self.pro_org.slug}/indexing/{suffix}"

    def test_owner_reads_the_report(self):
        gsc = self.connect_gsc()
        self.snapshot(gsc, [page("/a", True, "Submitted and indexed")])
        body = self.signed("GET", self.url(), PRO_EMAIL).json()
        self.assertEqual((body["status"], body["indexed"], body["property"]), ("ready", 1, SITE))

    def test_check_now_queues_a_check_then_refuses_a_second_one(self):
        gsc = self.connect_gsc()
        self._body = {}
        with mock.patch(DISPATCH) as dispatch, self.captureOnCommitCallbacks(execute=True):
            first = self.signed("POST", self.url("check/"), PRO_EMAIL)
            second = self.signed("POST", self.url("check/"), PRO_EMAIL)
        self.assertEqual((first.status_code, second.status_code), (202, 409))
        dispatch.assert_called_once_with(gsc.id)
        self.assertEqual(AgentActionLog.objects.get(action=weekly.STARTED).actor_email, PRO_EMAIL)

    def test_check_now_without_search_console_explains_what_to_do(self):
        self._body = {}
        response = self.signed("POST", self.url("check/"), PRO_EMAIL)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Connect Search Console", response.json()["detail"])

    def test_another_brands_owner_cannot_read_or_check(self):
        self._body = {}
        self.assertIn(self.signed("GET", self.url(), OTHER_EMAIL).status_code, (403, 404))
        self.assertIn(self.signed("POST", self.url("check/"), OTHER_EMAIL).status_code, (403, 404))
