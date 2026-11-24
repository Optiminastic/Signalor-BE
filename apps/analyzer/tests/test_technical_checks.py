"""
Technical checks: robots.txt lets AI crawlers in; the sitemap exists, has no broken
or redirecting URLs and is kept up to date.

Run: python manage.py test apps.analyzer.tests.test_technical_checks --settings=config.settings.test
"""

from datetime import UTC, datetime, timedelta
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.analyzer.action_sync import PILLAR_GROUP, materialize_run_actions
from apps.analyzer.models import AnalysisRun, Recommendation, TechnicalCheck, UserAction
from apps.analyzer.pipeline import robots_rules
from apps.analyzer.pipeline import technical_checks as tc
from apps.analyzer.pipeline.technical import _check_robots_allows_ai
from apps.analyzer.services import technical_checks as service
from apps.analyzer.tests.auth_helpers import signed_in
from apps.organizations.models import Organization

NOW = datetime(2026, 11, 24, tzinfo=UTC)


def sitemap(urls=("https://s.example/a",), days_old=(5,)):
    return tc.Sitemap(
        True, "https://s.example/sitemap.xml", list(urls), [NOW - timedelta(days=d) for d in days_old]
    )


class RobotsRulesTests(SimpleTestCase):
    def test_a_bot_follows_its_own_group_over_the_wildcard(self):
        robots = "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /\n"
        self.assertEqual(robots_rules.blocked(robots, ["GPTBot", "ClaudeBot"]), ["ClaudeBot"])

    def test_the_old_score_no_longer_reports_an_allowed_bot_as_blocked(self):
        robots = "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /\n"
        allowed, blocked = _check_robots_allows_ai(robots)
        self.assertNotIn("GPTBot", blocked)
        self.assertFalse(allowed)  # ClaudeBot and others still fall under "*"

    def test_disallowing_a_folder_is_not_blocking_the_site(self):
        self.assertEqual(robots_rules.blocked("User-agent: GPTBot\nDisallow: /private/\n", ["GPTBot"]), [])

    def test_comments_and_case_are_ignored(self):
        robots = "user-agent: gptbot  # OpenAI\nDISALLOW: /\n"
        self.assertEqual(robots_rules.blocked(robots, ["GPTBot"]), ["GPTBot"])

    def test_sitemap_lines_are_read(self):
        self.assertEqual(
            robots_rules.sitemaps("Sitemap: https://s.example/sm.xml\n"), ["https://s.example/sm.xml"]
        )


class RobotsCheckTests(SimpleTestCase):
    def test_a_blocked_required_bot_fails_and_names_it(self):
        check = tc.robots_check("User-agent: PerplexityBot\nDisallow: /\n", 200)
        self.assertEqual((check.status, check.detail), ("failed", "robots.txt blocks PerplexityBot."))
        self.assertIn("PerplexityBot", check.evidence["blocked"])

    def test_all_required_bots_allowed_passes(self):
        self.assertEqual(tc.robots_check("User-agent: *\nAllow: /\n", 200).status, "passed")

    def test_no_robots_file_means_everything_is_allowed(self):
        self.assertEqual(tc.robots_check("", 404).status, "passed")

    def test_an_unreachable_robots_file_is_unknown_not_passed(self):
        self.assertEqual(tc.robots_check(None, None).status, "unknown")


class SitemapChecksTests(SimpleTestCase):
    def test_missing_sitemap_fails_and_url_checks_are_skipped(self):
        missing = tc.Sitemap(False)
        checks = {c.code: c.status for c in tc.evaluate(("", 404), missing, {}, NOW)}
        self.assertEqual(checks["sitemap_found"], "failed")
        self.assertEqual(
            {checks["sitemap_broken_urls"], checks["sitemap_redirects"], checks["sitemap_fresh"]}, {"unknown"}
        )

    def test_unreachable_sitemap_is_unknown_not_missing(self):
        self.assertEqual(tc.sitemap_found_check(tc.Sitemap(None)).status, "unknown")

    def test_404_urls_fail_with_examples(self):
        statuses = {"https://s.example/a": 200, "https://s.example/gone": 404, "https://s.example/old": 410}
        check = tc.broken_urls_check(sitemap(), statuses)
        self.assertEqual((check.status, check.evidence["count"], check.evidence["checked"]), ("failed", 2, 3))
        self.assertIn("https://s.example/gone", check.evidence["examples"])

    def test_redirecting_urls_fail(self):
        check = tc.redirects_check(sitemap(), {"https://s.example/a": 301, "https://s.example/b": 200})
        self.assertEqual((check.status, check.evidence["count"]), ("failed", 1))

    def test_clean_urls_pass_both_checks(self):
        statuses = {"https://s.example/a": 200, "https://s.example/b": 200}
        self.assertEqual(tc.broken_urls_check(sitemap(), statuses).status, "passed")
        self.assertEqual(tc.redirects_check(sitemap(), statuses).status, "passed")

    def test_freshness_uses_the_newest_lastmod(self):
        self.assertEqual(tc.freshness_check(sitemap(days_old=(400, 10)), NOW).status, "passed")
        stale = tc.freshness_check(sitemap(days_old=(400, 200)), NOW)
        self.assertEqual((stale.status, stale.evidence["age_days"]), ("failed", 200))

    def test_no_lastmod_is_unknown(self):
        self.assertEqual(tc.freshness_check(sitemap(days_old=()), NOW).status, "unknown")


class ParseSitemapTests(SimpleTestCase):
    def test_urls_and_lastmods_are_read(self):
        xml = (
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://s.example/a</loc><lastmod>2026-11-01</lastmod></url>"
            "<url><loc>https://s.example/b</loc></url></urlset>"
        )
        pages, children, dates = tc.parse_sitemap(xml)
        self.assertEqual(
            (pages, children, len(dates)), (["https://s.example/a", "https://s.example/b"], [], 1)
        )

    def test_a_sitemap_index_lists_children(self):
        xml = (
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://s.example/posts.xml</loc></sitemap></sitemapindex>"
        )
        self.assertEqual(tc.parse_sitemap(xml)[1], ["https://s.example/posts.xml"])

    def test_entity_expansion_is_refused(self):
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>'
        )
        self.assertEqual(tc.parse_sitemap(bomb), ([], [], []))

    def test_garbage_is_empty_not_an_error(self):
        self.assertEqual(tc.parse_sitemap("<html>not xml"), ([], [], []))


class RunChecksTests(SimpleTestCase):
    def test_fetching_feeds_the_pure_checks(self):
        responses = {
            "https://s.example/robots.txt": (
                200,
                "User-agent: *\nAllow: /\nSitemap: https://s.example/sm.xml\n",
            ),
            "https://s.example/sm.xml": (
                200,
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                "<url><loc>https://s.example/a</loc><lastmod>2026-11-20</lastmod></url>"
                "<url><loc>https://s.example/gone</loc></url></urlset>",
            ),
        }

        def get(url, **_):
            status, text = responses.get(url, (404, ""))
            return mock.Mock(status_code=status, ok=status == 200, text=text)

        def head(url, **_):
            return mock.Mock(status_code=404 if url.endswith("gone") else 200)

        session = mock.Mock(get=get, head=head)
        with mock.patch.object(tc, "guarded_session", return_value=session):
            checks = {c.code: c for c in tc.run_checks("https://s.example", now=NOW)}
        self.assertEqual(checks["robots_ai_bots"].status, "passed")
        self.assertEqual(checks["sitemap_found"].status, "passed")
        self.assertEqual(checks["sitemap_broken_urls"].evidence["examples"], ["https://s.example/gone"])
        self.assertEqual(checks["sitemap_fresh"].status, "passed")


class StoreAndTicketTests(TestCase):
    """Every result is kept; only failures become On-site tickets (and then actions)."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="Shop", url="https://s.example", owner_email="o@s.example"
        )
        self.run = AnalysisRun.objects.create(
            organization=self.org,
            url=self.org.url,
            email=self.org.owner_email,
            status=AnalysisRun.Status.COMPLETE,
        )

    def checks(self, robots="passed", broken="failed", fresh="passed"):
        return [
            tc.Check(
                "robots_ai_bots",
                "AI crawlers allowed",
                robots,
                "robots.txt blocks GPTBot.",
                {"blocked": ["GPTBot"]},
            ),
            tc.Check("sitemap_found", "Sitemap found", "passed", "12 URLs listed."),
            tc.Check(
                "sitemap_broken_urls",
                "No broken URLs",
                broken,
                "2 of 12 return 404.",
                {"count": 2, "examples": ["https://s.example/gone"]},
            ),
            tc.Check("sitemap_redirects", "No redirects", "unknown", "Skipped."),
            tc.Check("sitemap_fresh", "Up to date", fresh, "Updated 5 days ago."),
        ]

    def test_every_result_is_stored_and_only_failures_become_tickets(self):
        service.store(self.run, self.checks())
        self.assertEqual(TechnicalCheck.objects.filter(analysis_run=self.run).count(), 5)
        recs = list(self.run.recommendations.values_list("finding_code", flat=True))
        self.assertEqual(recs, ["sitemap_broken_urls"])
        rec = self.run.recommendations.get()
        self.assertEqual((rec.pillar, rec.affected_pages), ("technical", ["https://s.example/gone"]))

    def test_a_failure_is_ticketed_even_when_the_generator_would_suppress_it(self):
        # generate_recommendations drops findings for high-scoring pillars; a failed check never is.
        service.store(self.run, self.checks(robots="failed"))
        self.assertIn("ai_bots_blocked", set(self.run.recommendations.values_list("finding_code", flat=True)))

    def test_an_existing_ticket_for_the_same_problem_is_not_duplicated(self):
        Recommendation.objects.create(
            analysis_run=self.run,
            pillar="technical",
            priority="critical",
            title="Unblock AI Crawlers",
            description="x",
            action="y",
            category="technical",
            finding_code="ai_bots_blocked",
        )
        service.store(self.run, self.checks(robots="failed"))
        self.assertEqual(self.run.recommendations.filter(finding_code="ai_bots_blocked").count(), 1)

    def test_storing_twice_is_idempotent(self):
        service.store(self.run, self.checks())
        service.store(self.run, self.checks())
        self.assertEqual((TechnicalCheck.objects.count(), self.run.recommendations.count()), (5, 1))

    def test_passed_checks_never_reach_today_or_done(self):
        service.store(self.run, self.checks(broken="passed"))
        materialize_run_actions(self.run, self.org.owner_email)
        self.assertFalse(UserAction.objects.exists())

    def test_a_failed_check_becomes_an_on_site_action(self):
        service.store(self.run, self.checks())
        materialize_run_actions(self.run, self.org.owner_email)
        action = UserAction.objects.get()
        self.assertEqual((action.status, PILLAR_GROUP[action.recommendation.pillar]), ("pending", "On-site"))

    def test_a_check_error_never_fails_the_analysis(self):
        with mock.patch.object(service.technical_checks, "run_checks", side_effect=RuntimeError("boom")):
            service.run_for(self.run)  # logs, does not raise
        self.assertFalse(TechnicalCheck.objects.exists())


class TechnicalChecksApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Shop", url="https://s.example", owner_email="o@s.example"
        )
        self.run = AnalysisRun.objects.create(
            organization=self.org,
            url=self.org.url,
            email=self.org.owner_email,
            status=AnalysisRun.Status.COMPLETE,
        )
        service.store(self.run, StoreAndTicketTests.checks(self))
        self.url = reverse("analyzer:technical-checks", args=[self.run.slug])

    def test_the_owner_sees_every_check_with_counts(self):
        with signed_in(self.org.owner_email):
            body = self.client.get(self.url).json()
        self.assertEqual((body["passed"], body["failed"], body["unknown"]), (3, 1, 1))
        self.assertEqual(len(body["checks"]), 5)

    def test_another_signed_in_user_gets_404(self):
        with signed_in("stranger@x.example"):
            self.assertEqual(self.client.get(self.url).status_code, 404)
