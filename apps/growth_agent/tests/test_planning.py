"""
Month plan: candidate scoring, scheduling rules, plan lifecycle and API scoping.

Run: python manage.py test apps.growth_agent.tests.test_planning --settings=config.settings.test
"""

from datetime import date

from django.test import SimpleTestCase

from ..models import CHANNELS, GrowthCycle, GrowthProgram, GrowthTask, PositioningReport
from ..services.planning import candidates, schedule
from ..services.planning.tactics import TACTICS
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase

MONDAY = date(2026, 11, 9)


def _report(**overrides):
    report = {
        "headline": {"brand_share_of_voice": 4.0, "prompts_analysed": 10},
        "share_of_voice": {"engines": {"chatgpt": {}, "gemini": {}, "perplexity": {}, "claude": {}}},
        "citation_gaps": [
            {
                "domain": "best-tools.com",
                "source_type": "listicle",
                "prompts": 6,
                "engines": ["chatgpt", "gemini"],
                "example_urls": ["https://best-tools.com/top-10"],
            },
            {
                "domain": "g2.com",
                "source_type": "review_site",
                "prompts": 3,
                "engines": ["perplexity"],
                "example_urls": [],
            },
            {
                "domain": "news.com",
                "source_type": "publisher",
                "prompts": 1,
                "engines": ["claude"],
                "example_urls": [],
            },
        ],
        "lost_prompts": [
            {"prompt": "best geo tool", "winning_rivals": [{"name": "Acme", "engines": ["chatgpt"]}]}
        ],
        "entity_gaps": [{"signal": "knowledge_panel", "label": "x"}, {"signal": "wikipedia", "label": "y"}],
        "context_gaps": {"missing_topics": ["AI citations", "GEO audits"]},
    }
    report.update(overrides)
    return report


class CandidateTests(SimpleTestCase):
    def setUp(self):
        self.specs = candidates.build_candidates(_report())
        self.by_target = {(s.tactic, s.target): s for s in self.specs}

    def test_every_tactic_maps_to_an_allowed_channel(self):
        for tactic in TACTICS.values():
            self.assertIn(tactic.channel, CHANNELS, tactic.key)

    def test_each_gap_becomes_its_tactic(self):
        self.assertIn(("listicle_inclusion", "best-tools.com"), self.by_target)
        # Review sites need human vendor verification, so they never become tasks.
        self.assertFalse(any(target == "g2.com" for _, target in self.by_target))
        self.assertIn(("publisher_pitch", "news.com"), self.by_target)

    def test_strongest_gap_outranks_weaker_gap_of_same_tactic_family(self):
        listicle = self.by_target[("listicle_inclusion", "best-tools.com")]
        publisher = self.by_target[("publisher_pitch", "news.com")]
        self.assertGreater(listicle.score, publisher.score)

    def test_topics_lost_prompts_and_entity_gaps_become_tasks(self):
        tactics = {s.tactic for s in self.specs}
        self.assertTrue({"answer_content", "comparison_page", "entity_schema"} <= tactics)
        # knowledge_panel + wikipedia gaps collapse into a single schema task
        self.assertEqual(sum(1 for s in self.specs if s.tactic == "entity_schema"), 1)

    def test_candidates_are_sorted_and_deduplicated(self):
        scores = [s.score for s in self.specs]
        self.assertEqual(scores, sorted(scores, reverse=True))
        keys = [(s.tactic, s.target.lower()) for s in self.specs]
        self.assertEqual(len(keys), len(set(keys)))

    def test_empty_report_yields_no_tasks(self):
        self.assertEqual(candidates.build_candidates({}), [])

    def test_only_autonomous_tactics_exist(self):
        human_only = {
            "review_profile",
            "wikipedia_draft",
            "wikidata_entry",
            "community_answer",
            "journalist_requests",
        }
        self.assertFalse(human_only & set(TACTICS))


class ScheduleTests(SimpleTestCase):
    def setUp(self):
        self.placed = schedule.schedule(candidates.build_candidates(_report()), MONDAY)

    def test_outreach_never_lands_before_its_earliest_week(self):
        for item in self.placed:
            self.assertGreaterEqual(item.week, item.spec.earliest_week, item.spec.tactic)

    def test_tasks_fall_on_workdays_inside_their_week(self):
        for item in self.placed:
            self.assertLess(item.scheduled_for.weekday(), 5)
            offset = (item.scheduled_for - MONDAY).days
            self.assertEqual(offset // 7 + 1, item.week)

    def test_weekly_capacity_and_cycle_cap(self):
        many = [
            candidates.TaskSpec(
                "answer_content", "owned_content", f"t{i}", "", f"topic {i}", "", 50 - i, 1, 1
            )
            for i in range(40)
        ]
        placed = schedule.schedule(many, MONDAY)
        self.assertEqual(len(placed), schedule.MAX_TASKS_PER_CYCLE)
        for week in range(1, schedule.WEEKS_PER_CYCLE + 1):
            self.assertLessEqual(sum(1 for p in placed if p.week == week), schedule.TASKS_PER_WEEK)


class PlanApiTests(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(organization=self.pro_org, created_by_email=PRO_EMAIL)
        PositioningReport.objects.create(
            program=self.program, status=PositioningReport.Status.COMPLETE, **_report()
        )
        self.plan_path = f"/api/growth/programs/{self.pro_org.slug}/plan/"
        self._body = {}

    def test_create_plan_once_then_reuse_and_activate_program(self):
        first = self.signed("POST", self.plan_path, PRO_EMAIL)
        second = self.signed("POST", self.plan_path, PRO_EMAIL)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(GrowthCycle.objects.count(), 1)
        plan = first.data["plan"]
        self.assertEqual(plan["baseline_share_of_voice"], 4.0)
        self.assertEqual(plan["target_share_of_voice"], 14.0)  # +10 point default goal
        self.assertTrue(plan["tasks"])
        self.program.refresh_from_db()
        self.assertEqual(self.program.status, GrowthProgram.Status.ACTIVE)

    def test_no_positioning_report_is_conflict(self):
        PositioningReport.objects.all().delete()
        response = self.signed("POST", self.plan_path, PRO_EMAIL)
        self.assertEqual(response.status_code, 409)

    def test_skip_and_restore_a_task(self):
        self.signed("POST", self.plan_path, PRO_EMAIL)
        task = GrowthTask.objects.first()
        path = f"/api/growth/programs/{self.pro_org.slug}/tasks/{task.id}/"
        self._body = {"status": "skipped"}
        self.assertEqual(self.signed("PATCH", path, PRO_EMAIL).status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, GrowthTask.Status.SKIPPED)
        self._body = {"status": "planned"}
        self.signed("PATCH", path, PRO_EMAIL)
        task.refresh_from_db()
        self.assertEqual(task.status, GrowthTask.Status.PLANNED)

    def test_started_task_cannot_be_changed(self):
        self.signed("POST", self.plan_path, PRO_EMAIL)
        task = GrowthTask.objects.first()
        GrowthTask.objects.filter(pk=task.pk).update(status=GrowthTask.Status.DONE)
        self._body = {"status": "skipped"}
        response = self.signed(
            "PATCH", f"/api/growth/programs/{self.pro_org.slug}/tasks/{task.id}/", PRO_EMAIL
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_touch_someone_elses_plan(self):
        other = GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        PositioningReport.objects.create(program=other, status=PositioningReport.Status.COMPLETE, **_report())
        response = self.signed("POST", f"/api/growth/programs/{self.other_org.slug}/plan/", PRO_EMAIL)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(GrowthCycle.objects.exists())
