"""
Satellite step 4: weekly Google check of each published satellite article
through signalor.ai's own Search Console. Google is faked.

Run: python manage.py test apps.growth_agent.tests.test_satellite_rank --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.integrations.models import Integration
from apps.organizations.models import Organization

from ..models import AgentActionLog, GrowthTask
from ..services.execution import audit
from ..services.indexing import satellite_rank, weekly
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase

GSC = "apps.integrations.services.gsc"
DISPATCH = "apps.growth_agent.analysis_tasks.dispatch_satellite_rank"
URL = "https://betterversus.com/ahrefs-vs-semrush"
PROPERTIES = ["sc-domain:betterversus.com", "https://thepickpost.com/"]


class PropertyAndFlagTests(SimpleTestCase):
    def test_the_property_covering_an_article_is_found(self):
        self.assertEqual(satellite_rank.property_for(PROPERTIES, URL), "sc-domain:betterversus.com")
        self.assertEqual(
            satellite_rank.property_for(PROPERTIES, "https://thepickpost.com/x"), "https://thepickpost.com/"
        )
        self.assertEqual(satellite_rank.property_for(PROPERTIES, "https://guidefactories.com/x"), "")

    def test_lagging_articles_are_flagged(self):
        week, fortnight = timedelta(days=8), timedelta(days=15)
        self.assertEqual(satellite_rank.flag({"indexed": False}, timedelta(days=3)), "")
        self.assertEqual(satellite_rank.flag({"indexed": False}, week), "needs_links")
        self.assertEqual(satellite_rank.flag({"indexed": True, "position": 24.0}, fortnight), "needs_refresh")
        self.assertEqual(satellite_rank.flag({"indexed": True, "position": 4.2}, fortnight), "")
        self.assertEqual(satellite_rank.flag({"property_found": False}, fortnight), "")


@override_settings(SATELLITE_GSC_ORG_SLUG="signalor-ops")
class SatelliteRankTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        ops = Organization.objects.create(
            name="signalor.ai", owner_email="ops@signalor.ai", url="https://signalor.ai"
        )
        Organization.objects.filter(pk=ops.pk).update(slug="signalor-ops")
        Integration.objects.create(
            organization=ops, provider="google_search_console", metadata={"site_url": "sc-domain:signalor.ai"}
        )
        self.article = self.make_task(
            channel="satellite_content",
            tactic="satellite_article",
            target="ahrefs vs semrush",
            target_url="https://betterversus.com",
            status=GrowthTask.Status.DONE,
            evidence_url=URL,
        )
        entry = audit.record(
            self.program, self.article, "publish", AgentActionLog.Outcome.SUCCEEDED, {"url": URL}
        )
        AgentActionLog.objects.filter(pk=entry.pk).update(created_at=timezone.now() - timedelta(days=9))

    def run_checks(self, on_google=False):
        inspection = {"on_google": on_google, "coverage_state": "Discovered - currently not indexed"}
        metrics = {"position": 0.0, "clicks": 0, "impressions": 3}
        with (
            mock.patch(f"{GSC}.list_gsc_sites", return_value=[{"site_url": p} for p in PROPERTIES]),
            mock.patch(f"{GSC}.inspect_gsc_url", return_value=inspection) as inspect,
            mock.patch(f"{GSC}.fetch_gsc_page_metrics", return_value=metrics),
        ):
            done = satellite_rank.run_checks(self.program)
        return done, inspect

    def test_each_published_article_is_checked_on_its_sites_property(self):
        done, inspect = self.run_checks()
        self.assertEqual(done, 1)
        self.assertEqual(inspect.call_args.kwargs["site_url"], "sc-domain:betterversus.com")
        detail = AgentActionLog.objects.get(action=satellite_rank.CHECKED).detail
        self.assertEqual((detail["indexed"], detail["reason"]), (False, "Found, not crawled yet"))

    def test_an_article_is_checked_once_a_week(self):
        self.run_checks()
        done, inspect = self.run_checks()
        self.assertEqual(done, 0)
        inspect.assert_not_called()

    def test_the_report_flags_an_article_still_not_on_google_after_a_week(self):
        self.run_checks()
        row = satellite_rank.articles(self.program)["articles"][0]
        self.assertEqual((row["url"], row["flag"]), (URL, "needs_links"))

    def test_weekly_queues_checks_only_with_search_console_and_only_once(self):
        with mock.patch(DISPATCH) as dispatch, self.captureOnCommitCallbacks(execute=True):
            weekly.weekly_round(self.program)
            weekly.weekly_round(self.program)
        dispatch.assert_called_once_with(self.program.id)
        with override_settings(SATELLITE_GSC_ORG_SLUG=""), mock.patch(DISPATCH) as dispatch:
            self.assertFalse(satellite_rank.weekly(self.program))
        dispatch.assert_not_called()

    def test_the_endpoint_is_owner_only(self):
        path = f"/api/growth/programs/{self.pro_org.slug}/indexing/satellite/"
        body = self.signed("GET", path, PRO_EMAIL).json()
        self.assertEqual((body["search_console"], len(body["articles"])), (True, 1))
        self.assertIn(self.signed("GET", path, OTHER_EMAIL).status_code, (403, 404))


class RefreshQueueTests(SatelliteRankTests):
    def flag_below_page_one(self):
        detail = {"url": URL, "property_found": True, "indexed": True, "position": 23.0, "clicks": 1}
        audit.record(
            self.program, self.article, satellite_rank.CHECKED, AgentActionLog.Outcome.SUCCEEDED, detail
        )
        AgentActionLog.objects.filter(action="publish").update(created_at=timezone.now() - timedelta(days=15))

    def test_an_article_below_page_one_after_two_weeks_gets_one_refresh_task(self):
        self.flag_below_page_one()
        self.assertEqual(satellite_rank.queue_refreshes(self.program), 1)
        self.assertEqual(satellite_rank.queue_refreshes(self.program), 0)  # not twice within 30 days
        refresh = GrowthTask.objects.get(tactic="satellite_refresh")
        self.assertEqual((refresh.target_url, refresh.channel), (URL, "satellite_content"))
        row = satellite_rank.articles(self.program)["articles"][0]
        self.assertEqual((row["flag"], row["refresh_queued"]), ("needs_refresh", True))

    def test_articles_doing_fine_get_no_refresh(self):
        detail = {"url": URL, "property_found": True, "indexed": True, "position": 3.0}
        audit.record(
            self.program, self.article, satellite_rank.CHECKED, AgentActionLog.Outcome.SUCCEEDED, detail
        )
        AgentActionLog.objects.filter(action="publish").update(created_at=timezone.now() - timedelta(days=15))
        self.assertEqual(satellite_rank.queue_refreshes(self.program), 0)
