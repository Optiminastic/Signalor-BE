"""
Prompt presence: for each tracked prompt, Google links, Reddit threads (with
subreddits), LinkedIn posts and X posts, and where the brand already appears.

Run: python manage.py test apps.growth_agent.tests.test_prompt_presence --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase

from apps.analyzer.models import AnalysisRun, PromptTrack

from ..models import PromptPresence
from ..services.prompt_presence import check, search
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase


def organic(*links):
    return [
        {"link": link, "title": f"Title {i}", "snippet": "", "position": i} for i, link in enumerate(links, 1)
    ]


class ReadTests(SimpleTestCase):
    def test_reddit_keeps_threads_with_their_subreddit(self):
        rows = search.read(
            "reddit",
            organic(
                "https://www.reddit.com/r/3Dprinting/comments/abc/best_printer/",
                "https://www.reddit.com/r/3Dprinting/",  # a subreddit page, not a thread
                "https://www.reddit.com/user/someone/",
            ),
        )
        self.assertEqual([(r.url[-13:], r.community) for r in rows], [("best_printer/", "r/3Dprinting")])

    def test_linkedin_keeps_posts_and_articles_only(self):
        rows = search.read(
            "linkedin",
            organic(
                "https://www.linkedin.com/posts/jane_3d-printing-activity-123",
                "https://www.linkedin.com/pulse/why-3d-printing-john",
                "https://www.linkedin.com/company/acme",
            ),
        )
        self.assertEqual(len(rows), 2)

    def test_x_keeps_status_posts_on_x_or_twitter(self):
        rows = search.read(
            "x",
            organic(
                "https://x.com/maker/status/1789",
                "https://twitter.com/maker/status/42",
                "https://x.com/maker",
            ),
        )
        self.assertEqual(len(rows), 2)

    def test_at_most_five_per_platform(self):
        self.assertEqual(
            len(search.read("google", organic(*[f"https://s{i}.example" for i in range(10)]))), 5
        )

    def test_brand_match_by_site_or_name(self):
        by_site = search.Result("google", "https://blog.shop.example/post", "x", "", "", 1)
        by_name = search.Result(
            "reddit", "https://reddit.com/r/a/comments/1/", "Is Shop Store any good?", "", "", 1
        )
        other = search.Result("google", "https://other.example", "Shopping tips", "", "", 1)
        self.assertTrue(search.mentions_brand(by_site, name="Shop Store", domain="shop.example"))
        self.assertTrue(search.mentions_brand(by_name, name="Shop Store", domain="shop.example"))
        self.assertFalse(search.mentions_brand(other, name="Shop Store", domain="shop.example"))


class PromptPresenceTestCase(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        run = AnalysisRun.objects.create(
            organization=self.pro_org,
            url=self.pro_org.url,
            email=PRO_EMAIL,
            status=AnalysisRun.Status.COMPLETE,
        )
        PromptTrack.objects.create(analysis_run=run, prompt_text="best 3d printer for beginners")
        PromptTrack.objects.create(analysis_run=run, prompt_text="resin vs filament printing")
        self.base = f"/api/growth/programs/{self.pro_org.slug}/prompt-presence/"
        self._body = {}

    def fake_search(self, prompt):
        return {
            "google": search.read("google", organic(f"{self.pro_org.url}/guide", "https://other.example/x")),
            "reddit": search.read("reddit", organic("https://www.reddit.com/r/3Dprinting/comments/1/q/")),
            "linkedin": [],
            "x": search.read("x", organic("https://x.com/a/status/1")),
        }


class CheckTests(PromptPresenceTestCase):
    def test_every_tracked_prompt_is_searched_and_stored(self):
        with mock.patch.object(search, "search_prompt", side_effect=self.fake_search):
            detail = check.run_check(self.program)
        self.assertEqual((detail["prompts"], detail["results"]), (2, 8))
        self.assertEqual(
            PromptPresence.objects.filter(mentions_brand=True).count(), 2
        )  # own site, per prompt

    def test_rerunning_the_same_week_replaces_rows(self):
        with mock.patch.object(search, "search_prompt", side_effect=self.fake_search):
            check.run_check(self.program)
            check.run_check(self.program)
        self.assertEqual(PromptPresence.objects.count(), 8)

    def test_no_search_key_stores_nothing(self):
        with mock.patch.object(search, "search_prompt", return_value=None):
            self.assertEqual(check.run_check(self.program)["results"], 0)

    def test_deleted_prompts_are_skipped_and_the_cap_holds(self):
        PromptTrack.objects.filter(prompt_text="resin vs filament printing").update(
            deleted_at="2026-11-01T00:00:00Z"
        )
        self.assertEqual(check.tracked_prompts(self.program), ["best 3d printer for beginners"])

    def test_report_groups_by_prompt_with_subreddits_and_presence(self):
        with mock.patch.object(search, "search_prompt", side_effect=self.fake_search):
            check.run_check(self.program)
        prompt = next(p for p in check.report(self.program)["prompts"] if p["prompt"].startswith("best"))
        self.assertEqual(prompt["subreddits"], [{"name": "r/3Dprinting", "threads": 1}])
        self.assertEqual(prompt["brand_present_on"], ["google"])
        self.assertEqual((len(prompt["google"]), len(prompt["x"]), prompt["linkedin"]), (2, 1, []))


class ApiTests(PromptPresenceTestCase):
    def test_owner_reads_the_report(self):
        response = self.signed("GET", self.base, PRO_EMAIL)
        self.assertEqual((response.status_code, response.json()["tracked_prompts"]), (200, 2))

    def test_check_now_runs_at_most_once_an_hour(self):
        with mock.patch("apps.growth_agent.analysis_tasks.dispatch_prompt_presence"):
            with self.captureOnCommitCallbacks(execute=True):
                first = self.signed("POST", f"{self.base}check/", PRO_EMAIL)
                second = self.signed("POST", f"{self.base}check/", PRO_EMAIL)
        self.assertEqual((first.status_code, second.status_code), (202, 409))

    def test_another_brands_owner_gets_nothing(self):
        for method, path in (("GET", ""), ("POST", "check/")):
            with self.subTest(path=path):
                self.assertIn(self.signed(method, f"{self.base}{path}", OTHER_EMAIL).status_code, (403, 404))
