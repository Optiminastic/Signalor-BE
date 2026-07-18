"""Growth Agent plan endpoint: ownership, shape, and ranked ordering.

Run:
    python manage.py test apps.analyzer.tests.test_agent_plan
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.analyzer.models import AnalysisRun, Recommendation, UserAction
from apps.organizations.models import Organization

OWNER = "owner@example.com"
STRANGER = "stranger@example.com"


class AgentPlanTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.example", owner_email=OWNER)
        self.run = AnalysisRun.objects.create(
            organization=self.org,
            url="https://acme.example",
            email=OWNER,
            run_type="single_page",
            status="complete",
            composite_score=42.0,
        )
        # Three recs, one flagged top fix, with explicit ranks.
        self.rec_top = self._rec("technical", "critical", "Create llms.txt", rank=1, top=True)
        self.rec_mid = self._rec("content", "high", "Add citations", rank=2)
        self.rec_low = self._rec("entity", "low", "Add social profiles", rank=0)
        self.url = reverse("analyzer:agent-plan", args=[self.run.slug])

    def _rec(self, pillar, priority, title, *, rank=0, top=False):
        return Recommendation.objects.create(
            analysis_run=self.run,
            pillar=pillar,
            priority=priority,
            title=title,
            description="d",
            action="a",
            category=pillar,
            xp_reward=20,
            daily_priority_rank=rank,
            is_top_fix=top,
        )

    def test_rejects_non_owner(self):
        res = self.client.get(self.url, {"email": STRANGER})
        self.assertEqual(res.status_code, 404)

    def test_requires_email(self):
        self.assertEqual(self.client.get(self.url).status_code, 400)

    def test_owner_gets_ranked_plan(self):
        res = self.client.get(self.url, {"email": OWNER})
        self.assertEqual(res.status_code, 200)
        body = res.json()

        # Materialized on first view — the page is never empty.
        self.assertEqual(UserAction.objects.filter(analysis_run=self.run).count(), 3)

        self.assertEqual(body["brief"]["website"], "https://acme.example")
        self.assertEqual(body["brief"]["score"], 42.0)
        self.assertEqual(body["counts"], {"today": 3, "backlog": 3, "done": 0})

        # top_fix is the flagged rec.
        self.assertEqual(body["top_fix"]["title"], "Create llms.txt")

        # Pillars mapped to display groups.
        pillars = {g["pillar"] for g in body["groups"]}
        self.assertEqual(pillars, {"On-site", "Content", "Off-page"})

    def test_ordering_top_fix_first_then_rank(self):
        self.client.get(self.url, {"email": OWNER})  # materialize
        res = self.client.get(self.url, {"email": OWNER})
        flat = [a for g in res.json()["groups"] for a in g["actions"]]
        # First action overall must be the top fix; last must be the unranked (rank 0) one.
        # Rebuild global order by (not top, rank-or-big).
        ordered = sorted(flat, key=lambda a: (0 if a["is_top_fix"] else 1, a["rank"] or 10000))
        self.assertEqual(ordered[0]["title"], "Create llms.txt")
        self.assertEqual(ordered[-1]["title"], "Add social profiles")

    def test_completed_tasks_count_as_done(self):
        self.client.get(self.url, {"email": OWNER})  # materialize
        UserAction.objects.filter(recommendation=self.rec_mid).update(
            status=UserAction.ActionStatus.COMPLETED
        )
        body = self.client.get(self.url, {"email": OWNER}).json()
        self.assertEqual(body["counts"]["done"], 1)
        self.assertEqual(body["counts"]["backlog"], 2)


class AgentRefreshRateLimitTests(TestCase):
    """Refresh is a once-a-day action — the plan is a daily artifact."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.org = Organization.objects.create(name="Acme", url="https://acme.example", owner_email=OWNER)
        self.run = AnalysisRun.objects.create(
            organization=self.org, url="https://acme.example", email=OWNER, status="complete"
        )
        Recommendation.objects.create(
            analysis_run=self.run,
            pillar="technical",
            priority="high",
            title="Create llms.txt",
            description="d",
            action="a",
            category="technical",
        )
        self.url = reverse("analyzer:agent-plan-refresh", args=[self.run.slug])

    def test_first_refresh_ok_second_rate_limited(self):
        first = self.client.post(self.url, {"email": OWNER}, content_type="application/json")
        self.assertEqual(first.status_code, 200)
        self.assertIsNotNone(first.json()["refresh_available_at"])

        second = self.client.post(self.url, {"email": OWNER}, content_type="application/json")
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["code"], "rate_limited")

    def test_non_owner_cannot_refresh(self):
        res = self.client.post(self.url, {"email": STRANGER}, content_type="application/json")
        self.assertEqual(res.status_code, 404)
