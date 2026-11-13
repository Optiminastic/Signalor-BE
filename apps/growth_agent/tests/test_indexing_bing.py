"""
Indexing slice 2: send new and changed pages to Bing (IndexNow) every week.

No network: the sitemap fetch and the IndexNow call are faked.

Run: python manage.py test apps.growth_agent.tests.test_indexing_bing --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from apps.analyzer.services.indexnow import SubmissionResult

from ..models import AgentActionLog, GrowthProgram
from ..services.indexing import bing, report, sitemap, weekly
from ..services.indexing.sitemap import SitemapEntry
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase

DISPATCH = "apps.growth_agent.analysis_tasks.dispatch_bing_send"
URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://brand-pro.com/a</loc><lastmod>2026-11-01</lastmod></url>
  <url><loc>https://brand-pro.com/b</loc><lastmod>2026-11-10T08:00:00+00:00</lastmod></url>
  <url><loc>https://brand-pro.com/c</loc></url>
</urlset>"""
INDEX = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://brand-pro.com/pages.xml</loc></sitemap>
</sitemapindex>"""
XXE = b"""<?xml version="1.0"?>
<!DOCTYPE urlset [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>&secret;</loc></url></urlset>"""


def accepted(count):
    return SubmissionResult(submitted=count, status_code=200, ok=True, message="Accepted.")


class SitemapParseTests(SimpleTestCase):
    def test_reads_pages_with_their_last_change_dates(self):
        entries, children = sitemap.parse(URLSET)
        self.assertEqual([e.url for e in entries], [f"https://brand-pro.com/{p}" for p in "abc"])
        self.assertEqual(entries[1].lastmod.day, 10)
        self.assertIsNone(entries[2].lastmod)
        self.assertEqual(children, [])

    def test_follows_a_sitemap_index(self):
        self.assertEqual(sitemap.parse(INDEX), ([], ["https://brand-pro.com/pages.xml"]))

    def test_external_entities_are_never_resolved_and_garbage_is_ignored(self):
        entries, _ = sitemap.parse(XXE)
        self.assertFalse(any("root:" in e.url for e in entries))
        self.assertEqual(sitemap.parse(b"not xml at all"), ([], []))


class BingTestCase(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.pro_org.url = "https://brand-pro.com"
        self.pro_org.save()
        self.program = GrowthProgram.objects.create(
            organization=self.pro_org, created_by_email=PRO_EMAIL, status=GrowthProgram.Status.ACTIVE
        )

    def entries(self, *urls, lastmod=None):
        return [SitemapEntry(f"https://brand-pro.com/{u}", lastmod) for u in urls]

    def send_with(self, entries, result=None):
        with (
            mock.patch.object(sitemap, "fetch_entries", return_value=entries),
            mock.patch.object(
                bing.indexnow, "submit", return_value=result or accepted(len(entries))
            ) as submit,
        ):
            outcome = bing.send(self.program)
        return outcome, submit


class SendTests(BingTestCase):
    def test_first_run_sends_every_sitemap_page_and_remembers_them(self):
        outcome, submit = self.send_with(self.entries("a", "b"))
        self.assertEqual(outcome, bing.SUBMITTED)
        site, pages = submit.call_args.args
        self.assertEqual(
            (site, pages), ("https://brand-pro.com", ["https://brand-pro.com/a", "https://brand-pro.com/b"])
        )
        entry = AgentActionLog.objects.get(action=bing.SUBMITTED)
        self.assertEqual(len(entry.detail["fingerprints"]), 2)
        self.assertNotIn("https://brand-pro.com/a", entry.detail["fingerprints"])  # hashed, not raw

    def test_next_week_only_new_or_changed_pages_go_out(self):
        self.send_with(self.entries("a", "b"))
        outcome, submit = self.send_with(self.entries("a", "b"))
        self.assertEqual(outcome, bing.NO_CHANGES)
        submit.assert_not_called()
        later = timezone.now() + timedelta(days=1)
        changed = self.entries("a") + [SitemapEntry("https://brand-pro.com/b", later)] + self.entries("new")
        _, submit = self.send_with(changed)
        self.assertEqual(submit.call_args.args[1], ["https://brand-pro.com/b", "https://brand-pro.com/new"])

    def test_a_refused_submission_is_logged_as_blocked_and_not_remembered(self):
        refused = SubmissionResult(0, 403, False, "Key not valid. The key file is missing or does not match.")
        outcome, _ = self.send_with(self.entries("a"), result=refused)
        self.assertEqual(outcome, bing.BLOCKED)
        self.assertIn("Key not valid", AgentActionLog.objects.get(action=bing.BLOCKED).detail["message"])
        _, submit = self.send_with(self.entries("a"))
        submit.assert_called_once()  # still counts as new next time

    def test_no_sitemap_is_reported(self):
        outcome, submit = self.send_with([])
        self.assertEqual(outcome, bing.BLOCKED)
        submit.assert_not_called()


class WeeklyBingTests(BingTestCase):
    def test_nothing_is_sent_unless_the_owner_turned_it_on(self):
        with mock.patch(DISPATCH) as dispatch, self.captureOnCommitCallbacks(execute=True):
            weekly.weekly_round(self.program)
        dispatch.assert_not_called()

    def test_once_on_it_runs_weekly_and_only_once_a_week(self):
        bing.set_enabled(self.program, True, PRO_EMAIL)
        with mock.patch(DISPATCH) as dispatch, self.captureOnCommitCallbacks(execute=True):
            weekly.weekly_round(self.program)
            weekly.weekly_round(self.program)
        dispatch.assert_called_once_with(self.program.id)
        AgentActionLog.objects.filter(action=bing.STARTED).update(
            created_at=timezone.now() - timedelta(days=8)
        )
        with mock.patch(DISPATCH) as dispatch, self.captureOnCommitCallbacks(execute=True):
            weekly.weekly_round(self.program)
        dispatch.assert_called_once()

    def test_turning_it_off_stops_it(self):
        bing.set_enabled(self.program, True, PRO_EMAIL)
        bing.set_enabled(self.program, False, PRO_EMAIL)
        self.assertFalse(bing.enabled(self.program))
        with mock.patch(DISPATCH) as dispatch:
            weekly.weekly_round(self.program)
        dispatch.assert_not_called()


class BingApiTests(BingTestCase):
    def url(self, suffix=""):
        return f"/api/growth/programs/{self.pro_org.slug}/indexing/{suffix}"

    def test_turning_it_on_is_recorded_with_who_and_shows_the_key_file(self):
        self._body = {"enabled": True}
        body = self.signed("POST", self.url("bing/"), PRO_EMAIL).json()
        self.assertTrue(body["bing"]["enabled"])
        self.assertTrue(body["bing"]["key_file_url"].startswith("https://brand-pro.com/"))
        self.assertTrue(body["bing"]["key_file_url"].endswith(f"{body['bing']['key']}.txt"))
        self.assertEqual(AgentActionLog.objects.get(action=bing.ON).actor_email, PRO_EMAIL)

    def test_verify_checks_the_key_file(self):
        self._body = {}
        with mock.patch.object(
            bing.indexnow, "verify_key_file", return_value=(False, "returned HTTP 404")
        ) as check:
            body = self.signed("POST", self.url("bing/verify/"), PRO_EMAIL).json()
        self.assertEqual(body, {"verified": False, "message": "returned HTTP 404"})
        self.assertEqual(check.call_args.args[0], "https://brand-pro.com")

    def test_send_now_queues_once_then_waits(self):
        self._body = {}
        with mock.patch(DISPATCH) as dispatch, self.captureOnCommitCallbacks(execute=True):
            first = self.signed("POST", self.url("bing/send/"), PRO_EMAIL)
            second = self.signed("POST", self.url("bing/send/"), PRO_EMAIL)
        self.assertEqual((first.status_code, second.status_code), (202, 409))
        dispatch.assert_called_once_with(self.program.id)

    def test_the_report_carries_the_last_bing_result(self):
        self.send_with(self.entries("a", "b"))
        state = report.build(self.pro_org)["bing"]
        self.assertEqual((state["last"]["kind"], state["last"]["submitted"]), ("submitted", 2))

    def test_another_brands_owner_cannot_switch_verify_or_send(self):
        self._body = {"enabled": True}
        for suffix in ("bing/", "bing/verify/", "bing/send/"):
            self.assertIn(self.signed("POST", self.url(suffix), OTHER_EMAIL).status_code, (403, 404))
        self.assertFalse(bing.enabled(self.program))
