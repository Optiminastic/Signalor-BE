"""
Satellite step 1: the month's satellite topic plan (one article every 3 days on
signalor.ai's own sites), shown in the plan and never executed yet.

Run: python manage.py test apps.growth_agent.tests.test_planning_satellite --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from ..models import GrowthCycle, GrowthProgram, GrowthTask, PositioningReport
from ..services.execution.executor import due_tasks
from ..services.execution.satellite_channel import SatelliteContentChannel
from ..services.planning import candidates, plan, satellite, schedule
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase
from .test_planning import MONDAY, _report

SITES = {
    "research": "https://brightsfindings.com",
    "listicals": "https://thepickpost.com",
    "market_trends": "https://trendledgers.com",
    "comparison": "https://betterversus.com",
    "step_guide": "https://guidefactories.com",
}
CONFIGURED = "apps.analyzer.blog_store.is_configured"


def lost(*prompts):
    return [{"prompt": p, "winning_rivals": [{"name": "Acme", "engines": ["chatgpt"]}]} for p in prompts]


class SiteChoiceTests(SimpleTestCase):
    def test_each_topic_goes_to_the_site_whose_format_fits(self):
        self.assertEqual(satellite.best_site("Ahrefs vs Semrush for agencies"), "comparison")
        self.assertEqual(satellite.best_site("best GEO tools for startups"), "listicals")
        self.assertEqual(satellite.best_site("How to get cited by ChatGPT"), "step_guide")
        self.assertEqual(satellite.best_site("AI search trends 2027"), "market_trends")
        self.assertEqual(satellite.best_site("what is generative engine optimization"), "research")


class SatelliteCandidateTests(SimpleTestCase):
    def test_lost_prompts_come_first_then_missing_topics_up_to_ten_unique(self):
        report = _report(
            lost_prompts=lost("best geo tool", "Best GEO tool", "ahrefs vs signalor"),
            context_gaps={"missing_topics": [f"topic {i}" for i in range(20)]},
        )
        specs = satellite.satellite_candidates(report, SITES)
        self.assertEqual(len(specs), satellite.MAX_TOPICS)
        self.assertEqual([s.target for s in specs[:2]], ["best geo tool", "ahrefs vs signalor"])
        self.assertTrue(
            all(s.tactic == "satellite_article" and s.channel == "satellite_content" for s in specs)
        )

    def test_the_site_is_the_target_url_and_named_in_the_title(self):
        spec = satellite.satellite_candidates(_report(lost_prompts=lost("ahrefs vs semrush")), SITES)[0]
        self.assertEqual(spec.target_url, "https://betterversus.com")
        self.assertEqual(spec.title, "Article on betterversus.com: ahrefs vs semrush")

    def test_no_site_gets_more_than_three_a_month(self):
        report = _report(lost_prompts=lost(*[f"best tool {i}" for i in range(10)]), context_gaps={})
        per_site: dict[str, int] = {}
        for spec in satellite.satellite_candidates(report, SITES):
            per_site[spec.target_url] = per_site.get(spec.target_url, 0) + 1
        self.assertLessEqual(max(per_site.values()), satellite.MAX_PER_SITE)

    def test_without_sites_there_are_no_satellite_topics(self):
        self.assertEqual(satellite.satellite_candidates(_report(), {}), [])


class SatelliteScheduleTests(SimpleTestCase):
    def specs(self, count=10):
        report = _report(
            lost_prompts=[], context_gaps={"missing_topics": [f"topic {i}" for i in range(count)]}
        )
        return satellite.satellite_candidates(report, SITES)

    def test_one_article_every_three_days_from_the_cycle_start(self):
        placed = schedule.schedule(self.specs(), MONDAY)
        dates = [t.scheduled_for for t in placed if t.spec.tactic == "satellite_article"]
        self.assertEqual(dates, [MONDAY + timedelta(days=3 * i) for i in range(10)])
        self.assertEqual(placed[-1].week, 4)

    def test_satellites_come_on_top_of_the_monthly_cap(self):
        many = [
            candidates._spec(candidates.TACTICS["answer_content"], target=f"topic {i}", reach=0.5)
            for i in range(40)
        ]
        placed = schedule.schedule(self.specs() + many, MONDAY)
        others = [t for t in placed if t.spec.tactic != "satellite_article"]
        self.assertEqual(len(others), schedule.MAX_TASKS_PER_CYCLE)  # other work keeps its full month
        self.assertEqual(sum(1 for t in placed if t.spec.tactic == "satellite_article"), 10)


@override_settings(SATELLITE_SITES=SITES)
class SatellitePlanTests(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(organization=self.pro_org, created_by_email=PRO_EMAIL)
        PositioningReport.objects.create(
            program=self.program, status=PositioningReport.Status.COMPLETE, **_report()
        )
        self.plan_path = f"/api/growth/programs/{self.pro_org.slug}/plan/"
        self._body = {}

    def make_plan(self, configured=True):
        with mock.patch(CONFIGURED, return_value=configured):
            return self.signed("POST", self.plan_path, PRO_EMAIL)

    def test_a_new_plan_includes_the_satellite_topics_when_the_sites_are_set_up(self):
        self.assertEqual(self.make_plan().status_code, 201)
        topics = GrowthTask.objects.filter(tactic="satellite_article")
        self.assertEqual(topics.count(), 3)  # 1 lost prompt + 2 missing topics in the sample report
        self.assertEqual(topics.first().target_url in SITES.values(), True)

    def test_plans_include_satellite_topics_even_before_publishing_is_set_up(self):
        self.make_plan(configured=False)
        self.assertEqual(GrowthTask.objects.filter(tactic="satellite_article").count(), 3)
        # Running them waits until the sites can be published to.
        with mock.patch(CONFIGURED, return_value=False):
            self.assertTrue(SatelliteContentChannel().readiness(self.program))

    def test_a_plan_made_without_satellite_topics_gets_them_added_once(self):
        self.make_plan()
        GrowthTask.objects.filter(tactic="satellite_article").delete()
        self.assertEqual(plan.add_satellite_topics(self.program), 3)
        dates = sorted(
            GrowthTask.objects.filter(tactic="satellite_article").values_list("scheduled_for", flat=True)
        )
        cycle = GrowthCycle.objects.get()
        start = max(timezone.now().date(), cycle.starts_on)
        self.assertEqual(dates, [start + timedelta(days=3 * i) for i in range(3)])
        self.assertEqual(plan.add_satellite_topics(self.program), 0)

    def test_satellite_topics_become_due_on_their_dates(self):
        self.make_plan()
        cycle = GrowthCycle.objects.get()
        self.assertFalse(
            due_tasks(cycle.starts_on - timedelta(days=1)).filter(tactic="satellite_article").exists()
        )
        self.assertTrue(due_tasks(cycle.starts_on).filter(tactic="satellite_article").exists())

    def test_the_owner_can_skip_and_restore_a_topic_and_others_cannot(self):
        self.make_plan()
        task = GrowthTask.objects.filter(tactic="satellite_article").first()
        path = f"/api/growth/programs/{self.pro_org.slug}/tasks/{task.id}/"
        self._body = {"status": "skipped"}
        self.assertEqual(self.signed("PATCH", path, PRO_EMAIL).status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, GrowthTask.Status.SKIPPED)
        self.assertIn(self.signed("PATCH", path, OTHER_EMAIL).status_code, (403, 404))
        self._body = {"status": "planned"}
        self.signed("PATCH", path, PRO_EMAIL)
        task.refresh_from_db()
        self.assertEqual(task.status, GrowthTask.Status.PLANNED)
