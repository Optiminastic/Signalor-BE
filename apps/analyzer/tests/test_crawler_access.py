"""Tests for the AI crawler access monitor.

This joins two signals that were already collected but never compared:
robots.txt policy and real ``CrawlerHit`` telemetry. Each alone is misleading.
"Allowed" says nothing about whether the bot ever came; zero hits looks the same
whether the bot was blocked, never found the site, or the telemetry integration
was never installed.

The contract that matters most is the last one: **no telemetry must report
`unknown`, never "never crawled"**. Telling a customer their site is uncrawled
when we simply are not watching would send them chasing a problem that may not
exist.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.analyzer.models import CrawlerHit
from apps.analyzer.services import crawler_access as ca
from apps.organizations.models import Organization

BLOCK_GPT = """
User-agent: GPTBot
Disallow: /
"""

BLOCK_ALL = """
User-agent: *
Disallow: /
"""

ALLOW_ALL = """
User-agent: *
Disallow: /admin/
"""


class _Base(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", owner_email="o@acme.com")

    def _hit(self, bot, path="/", days_ago=0):
        CrawlerHit.objects.create(
            organization=self.org,
            bot=bot,
            path=path,
            hit_at=timezone.now() - timedelta(days=days_ago),
        )

    def _by_bot(self, report):
        return {e.bot: e for e in report.engines}


class TelemetryPresenceTests(_Base):
    """unknown vs never-crawled - the distinction the report hinges on."""

    def test_no_telemetry_reports_unknown_not_never_crawled(self):
        report = ca.build_report(self.org, robots_txt=ALLOW_ALL)
        self.assertFalse(report.has_telemetry)
        for engine in report.engines:
            self.assertEqual(engine.status, ca.UNKNOWN)

    def test_unknown_diagnosis_tells_them_how_to_measure(self):
        report = ca.build_report(self.org, robots_txt=ALLOW_ALL)
        self.assertIn("telemetry", report.engines[0].diagnosis.lower())

    def test_any_telemetry_enables_real_per_bot_verdicts(self):
        self._hit("GPTBot")
        report = ca.build_report(self.org, robots_txt=ALLOW_ALL)
        self.assertTrue(report.has_telemetry)
        # A bot with rows is active; one without is now genuinely never-crawled.
        self.assertEqual(self._by_bot(report)["GPTBot"].status, ca.ACTIVE)
        self.assertEqual(self._by_bot(report)["PerplexityBot"].status, ca.NEVER_SEEN)


class PolicyTests(_Base):
    def test_disallowed_bot_is_blocked(self):
        self._hit("PerplexityBot")
        report = ca.build_report(self.org, robots_txt=BLOCK_GPT)
        self.assertEqual(self._by_bot(report)["GPTBot"].status, ca.BLOCKED)
        self.assertIs(self._by_bot(report)["GPTBot"].allowed, False)

    def test_wildcard_disallow_blocks_every_engine(self):
        report = ca.build_report(self.org, robots_txt=BLOCK_ALL)
        self.assertTrue(all(e.status == ca.BLOCKED for e in report.engines))

    def test_blocked_beats_telemetry(self):
        """A bot that visited before being blocked is still blocked now."""
        self._hit("GPTBot")
        report = ca.build_report(self.org, robots_txt=BLOCK_GPT)
        self.assertEqual(self._by_bot(report)["GPTBot"].status, ca.BLOCKED)

    def test_missing_robots_is_not_treated_as_blocking(self):
        report = ca.build_report(self.org, robots_txt="")
        self.assertFalse(report.robots_found)
        self.assertFalse(any(e.status == ca.BLOCKED for e in report.engines))


class ActivityTests(_Base):
    def test_recent_visit_is_active(self):
        self._hit("ClaudeBot", days_ago=1)
        self.assertEqual(self._by_bot(ca.build_report(self.org, ALLOW_ALL))["ClaudeBot"].status, ca.ACTIVE)

    def test_old_visit_is_stale(self):
        self._hit("ClaudeBot", days_ago=ca.STALE_AFTER_DAYS + 5)
        self.assertEqual(self._by_bot(ca.build_report(self.org, ALLOW_ALL))["ClaudeBot"].status, ca.STALE)

    def test_visits_outside_the_window_do_not_count(self):
        self._hit("ClaudeBot", days_ago=ca.WINDOW_DAYS + 10)
        self._hit("GPTBot", days_ago=1)  # so telemetry exists
        self.assertEqual(self._by_bot(ca.build_report(self.org, ALLOW_ALL))["ClaudeBot"].status, ca.NEVER_SEEN)

    def test_hit_and_path_counts_are_reported(self):
        self._hit("GPTBot", "/")
        self._hit("GPTBot", "/pricing")
        self._hit("GPTBot", "/pricing")
        row = self._by_bot(ca.build_report(self.org, ALLOW_ALL))["GPTBot"]
        self.assertEqual(row.hits, 3)
        self.assertEqual(row.distinct_paths, 2)

    def test_another_orgs_hits_are_not_counted(self):
        other = Organization.objects.create(name="Other", owner_email="x@x.com")
        CrawlerHit.objects.create(organization=other, bot="GPTBot", path="/", hit_at=timezone.now())
        self._hit("ClaudeBot")
        self.assertEqual(self._by_bot(ca.build_report(self.org, ALLOW_ALL))["GPTBot"].status, ca.NEVER_SEEN)


class EngineFramingTests(_Base):
    def test_every_bot_maps_to_a_named_engine(self):
        for engine in ca.build_report(self.org, ALLOW_ALL).engines:
            self.assertTrue(engine.engine)
            self.assertTrue(engine.why)

    def test_chatgpt_search_and_training_agents_are_distinguished(self):
        """Being open to training but closed to search is a costly misconfiguration."""
        by_bot = self._by_bot(ca.build_report(self.org, ALLOW_ALL))
        self.assertEqual(by_bot["OAI-SearchBot"].role, "search")
        self.assertEqual(by_bot["GPTBot"].role, "training")

    def test_worst_status_sorts_first(self):
        self._hit("ClaudeBot")
        report = ca.build_report(self.org, robots_txt=BLOCK_GPT)
        self.assertEqual(report.engines[0].status, ca.BLOCKED)

    def test_summary_names_the_problem_engines(self):
        self._hit("ClaudeBot")
        summary = ca.build_report(self.org, robots_txt=BLOCK_GPT).summary()
        self.assertIn("ChatGPT", summary["blocked_engines"])


class UncrawledPageTests(_Base):
    def test_pages_no_bot_fetched_are_listed(self):
        self._hit("GPTBot", "/")
        report = ca.build_report(
            self.org, ALLOW_ALL, known_urls=["https://acme.com/", "https://acme.com/pricing"]
        )
        self.assertEqual(report.uncrawled_pages, ["https://acme.com/pricing"])

    def test_trailing_slash_does_not_cause_a_false_gap(self):
        self._hit("GPTBot", "/pricing/")
        report = ca.build_report(self.org, ALLOW_ALL, known_urls=["https://acme.com/pricing"])
        self.assertEqual(report.uncrawled_pages, [])

    def test_no_telemetry_reports_no_page_gaps(self):
        """Without telemetry every page would look uncrawled, which is meaningless."""
        report = ca.build_report(self.org, ALLOW_ALL, known_urls=["https://acme.com/x"])
        self.assertEqual(report.uncrawled_pages, [])


class SerializationTests(_Base):
    def test_report_is_json_safe(self):
        import json

        self._hit("GPTBot", "/")
        json.dumps(ca.build_report(self.org, ALLOW_ALL, known_urls=["https://acme.com/x"]).as_dict())

    def test_report_for_run_without_org_is_safe(self):
        from types import SimpleNamespace

        out = ca.report_for_run(SimpleNamespace(organization=None, url="https://acme.com"))
        self.assertFalse(out["has_telemetry"])


class StaleAgentListTests(_Base):
    """The analyzer's own robots parser matches a hardcoded agent list that does
    not include newer agents, most importantly OAI-SearchBot, which is what
    ChatGPT search actually uses. A blanket block must still cover them."""

    def test_wildcard_block_covers_agents_the_legacy_list_omits(self):
        by_bot = self._by_bot(ca.build_report(self.org, robots_txt=BLOCK_ALL))
        for bot in ("OAI-SearchBot", "Bingbot", "Claude-SearchBot", "Perplexity-User"):
            self.assertEqual(by_bot[bot].status, ca.BLOCKED, bot)

    def test_partial_wildcard_disallow_is_not_a_site_wide_block(self):
        report = ca.build_report(self.org, robots_txt="User-agent: *\nDisallow: /admin/\n")
        self.assertFalse(any(e.status == ca.BLOCKED for e in report.engines))


class RecommendationTests(_Base):
    """Crawler access becomes a task because it gates every other task."""

    def test_blocked_engines_raise_a_critical_task(self):
        recs = ca.to_recommendations(ca.build_report(self.org, robots_txt=BLOCK_GPT))
        blocked = [r for r in recs if r["finding_code"] == "ai_crawler_blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["priority"], "critical")
        self.assertIn("ChatGPT", blocked[0]["description"])

    def test_blocked_task_names_cloudflare_as_the_likely_cause(self):
        """The rules are usually injected, not authored - say so or they hunt in the wrong file."""
        recs = ca.to_recommendations(ca.build_report(self.org, robots_txt=BLOCK_ALL))
        self.assertIn("Cloudflare", recs[0]["action"])

    def test_never_crawled_engines_raise_a_task(self):
        self._hit("GPTBot")
        recs = ca.to_recommendations(ca.build_report(self.org, robots_txt=ALLOW_ALL))
        codes = {r["finding_code"] for r in recs}
        self.assertIn("ai_crawler_never_visited", codes)

    def test_unknown_never_produces_a_task(self):
        """No telemetry means no evidence; advice built on missing data is worse than none."""
        self.assertEqual(ca.to_recommendations(ca.build_report(self.org, robots_txt=ALLOW_ALL)), [])

    def test_healthy_site_produces_no_tasks(self):
        for bot in ca.BOT_ENGINES:
            self._hit(bot)
        self.assertEqual(ca.to_recommendations(ca.build_report(self.org, robots_txt=ALLOW_ALL)), [])

    def test_tasks_only_use_real_model_fields(self):
        from apps.analyzer.models import Recommendation

        allowed = {f.name for f in Recommendation._meta.get_fields()}
        for rec in ca.to_recommendations(ca.build_report(self.org, robots_txt=BLOCK_ALL)):
            self.assertTrue(set(rec).issubset(allowed), set(rec) - allowed)


class EndpointTests(TestCase):
    """GET /runs/s/<slug>/crawler-access/"""

    def setUp(self):
        from apps.analyzer.models import AnalysisRun

        self.org = Organization.objects.create(name="Acme", owner_email="o@acme.com")
        self.run = AnalysisRun.objects.create(url="https://acme.com", organization=self.org)

    def _url(self, slug=None):
        from django.urls import reverse

        return reverse("analyzer:crawler-access", args=[slug or self.run.slug])

    def test_returns_a_report(self):
        from unittest.mock import patch

        with patch("apps.analyzer.pipeline.crawler.fetch_file_content", return_value=BLOCK_ALL):
            resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertIn("engines", resp.json())
        self.assertTrue(resp.json()["summary"]["blocked_engines"])

    def test_unknown_slug_is_404_not_a_500(self):
        self.assertEqual(self.client.get(self._url("does-not-exist")).status_code, 404)

    def test_run_without_an_organization_is_rejected(self):
        from apps.analyzer.models import AnalysisRun

        orphan = AnalysisRun.objects.create(url="https://x.com")
        self.assertEqual(self.client.get(self._url(orphan.slug)).status_code, 400)

    def test_a_failing_robots_fetch_still_returns_a_report(self):
        """Network trouble must degrade the verdict, not the endpoint."""
        from unittest.mock import patch

        with patch(
            "apps.analyzer.pipeline.crawler.fetch_file_content", side_effect=RuntimeError("timeout")
        ):
            resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
