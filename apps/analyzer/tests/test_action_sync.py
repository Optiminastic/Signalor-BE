"""Tests for materializing Recommendations into UserAction tasks.

Regression context: every analysis writes a fresh set of Recommendation rows,
and the Tasks list is organization-scoped across *all* runs. Materializing
per-run with no cross-run check therefore showed one copy of each recurring
finding per analysis - three runs of the same site produced three "Add Publish
Date" tasks, which is what users saw as task duplication.
"""

from django.test import TestCase

from apps.analyzer.action_sync import materialize_run_actions
from apps.analyzer.models import AnalysisRun, Recommendation, UserAction
from apps.organizations.models import Organization

OWNER = "owner@example.com"


class ActionMaterializationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", owner_email=OWNER)

    def _run(self):
        return AnalysisRun.objects.create(organization=self.org, url="https://acme.com")

    def _rec(self, run, code="no_publish_date", title="Add Publish Date"):
        return Recommendation.objects.create(
            analysis_run=run,
            pillar="eeat",
            priority="high",
            title=title,
            description="d",
            action="a",
            category="eeat",
            finding_code=code,
        )

    def test_creates_one_task_per_recommendation(self):
        run = self._run()
        self._rec(run)
        created, total = materialize_run_actions(run, OWNER)
        self.assertEqual((created, total), (1, 1))

    def test_resyncing_the_same_run_creates_nothing_new(self):
        run = self._run()
        self._rec(run)
        materialize_run_actions(run, OWNER)
        created, total = materialize_run_actions(run, OWNER)
        self.assertEqual((created, total), (0, 1))

    def test_second_run_does_not_duplicate_an_open_task(self):
        """The actual reported bug."""
        first = self._run()
        self._rec(first)
        materialize_run_actions(first, OWNER)

        second = self._run()
        self._rec(second)
        created, _ = materialize_run_actions(second, OWNER)

        self.assertEqual(created, 0)
        self.assertEqual(UserAction.objects.filter(title="Add Publish Date").count(), 1)

    def test_three_runs_still_yield_one_task(self):
        for _ in range(3):
            run = self._run()
            self._rec(run)
            materialize_run_actions(run, OWNER)
        self.assertEqual(UserAction.objects.count(), 1)

    def test_a_different_finding_is_still_raised(self):
        first = self._run()
        self._rec(first)
        materialize_run_actions(first, OWNER)

        second = self._run()
        self._rec(second, code="no_h1", title="Add an H1 Tag")
        created, _ = materialize_run_actions(second, OWNER)
        self.assertEqual(created, 1)

    def test_completed_finding_reappearing_is_raised_again(self):
        """A fixed issue that comes back is a regression, not a duplicate."""
        first = self._run()
        self._rec(first)
        materialize_run_actions(first, OWNER)
        UserAction.objects.update(status=UserAction.ActionStatus.COMPLETED)

        second = self._run()
        self._rec(second)
        created, _ = materialize_run_actions(second, OWNER)
        self.assertEqual(created, 1)

    def test_in_progress_task_is_not_duplicated(self):
        first = self._run()
        self._rec(first)
        materialize_run_actions(first, OWNER)
        UserAction.objects.update(status=UserAction.ActionStatus.IN_PROGRESS)

        second = self._run()
        self._rec(second)
        created, _ = materialize_run_actions(second, OWNER)
        self.assertEqual(created, 0)

    def test_duplicate_findings_inside_one_run_collapse(self):
        run = self._run()
        self._rec(run)
        self._rec(run)  # same finding_code twice
        created, _ = materialize_run_actions(run, OWNER)
        self.assertEqual(created, 1)

    def test_another_orgs_open_task_does_not_suppress_ours(self):
        other_org = Organization.objects.create(name="Other", owner_email="x@example.com")
        other_run = AnalysisRun.objects.create(organization=other_org, url="https://other.com")
        self._rec(other_run)
        materialize_run_actions(other_run, "x@example.com")

        run = self._run()
        self._rec(run)
        created, _ = materialize_run_actions(run, OWNER)
        self.assertEqual(created, 1)

    def test_distinct_tasks_sharing_a_finding_code_are_all_kept(self):
        """GEO signals emit many real tasks under one code.

        ``geo_prompt_lost`` produces a "Win the AI query: <query>" task per
        losing prompt, and ``geo_citation_gap`` one per domain. Keying dedupe on
        the code alone would collapse them into a single task and lose work.
        """
        run = self._run()
        for query in ("best AI visibility tools", "how to track ChatGPT mentions", "GEO platforms"):
            self._rec(run, code="geo_prompt_lost", title=f'Win the AI query: "{query}"')
        created, _ = materialize_run_actions(run, OWNER)
        self.assertEqual(created, 3)

    def test_distinct_shared_code_tasks_survive_a_second_run(self):
        run_one = self._run()
        self._rec(run_one, code="geo_citation_gap", title="Get mentioned on semrush.com")
        materialize_run_actions(run_one, OWNER)

        run_two = self._run()
        self._rec(run_two, code="geo_citation_gap", title="Get mentioned on semrush.com")
        self._rec(run_two, code="geo_citation_gap", title="Get mentioned on blog.hubspot.com")
        created, _ = materialize_run_actions(run_two, OWNER)

        # The repeat is suppressed; the new domain is raised.
        self.assertEqual(created, 1)
        self.assertEqual(UserAction.objects.filter(title="Get mentioned on semrush.com").count(), 1)
        self.assertEqual(UserAction.objects.filter(title="Get mentioned on blog.hubspot.com").count(), 1)

    def test_findings_without_a_code_dedupe_on_title(self):
        """Older rows and SiteOne findings can carry an empty finding_code."""
        first = self._run()
        self._rec(first, code="", title="Insecure TLS protocol versions supported")
        materialize_run_actions(first, OWNER)

        second = self._run()
        self._rec(second, code="", title="Insecure TLS protocol versions supported")
        created, _ = materialize_run_actions(second, OWNER)
        self.assertEqual(created, 0)
