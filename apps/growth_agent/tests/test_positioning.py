"""
Positioning report: pure metrics, fail-soft LLM context, report lifecycle and API scoping.

Run: python manage.py test apps.growth_agent.tests.test_positioning --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase

from apps.analyzer.models import AnalysisRun, Competitor, PromptCitation, PromptResult, PromptTrack

from ..models import GrowthProgram, PositioningReport
from ..services.positioning import context, metrics
from ..services.positioning.inputs import Citation, EngineAnswer, PositioningInputs, Rival
from ..services.positioning.report import build_report
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase

DISPATCH = "apps.growth_agent.views.dispatch_positioning_report"
ASK = "apps.growth_agent.services.positioning.context.ask_structured"


def _answer(prompt_id, engine, text, *, brand=False, cites=()):
    citations = tuple(Citation(domain=d, url=f"https://{d}/best-tools", title="Best tools") for d in cites)
    return EngineAnswer(prompt_id, f"prompt {prompt_id}", engine, text, brand, citations)


def _inputs(answers, rivals=(Rival("Acme", "acme.com"), Rival("Globex", "globex.com"))):
    checks = {
        "wikipedia_presence": False,
        "knowledge_panel": True,
        "community_links": {"reddit": False},
        "social_count": 4,
    }
    return PositioningInputs(("Signalor",), "signalor.ai", tuple(answers), tuple(rivals), checks, ())


class MetricsTests(SimpleTestCase):
    def setUp(self):
        self.inputs = _inputs(
            [
                _answer(1, "chatgpt", "Try Acme or Globex.", cites=("g2.com", "reddit.com", "acme.com")),
                _answer(1, "gemini", "Acme is the leader.", cites=("g2.com",)),
                _answer(2, "chatgpt", "Signalor is great.", brand=True, cites=("blog.example.com",)),
                _answer(2, "gemini", "Nothing relevant.", cites=()),
            ]
        )
        self.result = metrics.compute_metrics(self.inputs)

    def test_share_of_voice_counts_brand_and_rivals(self):
        overall = self.result["share_of_voice"]["overall"]
        self.assertEqual(overall["answers"], 4)
        self.assertEqual(overall["brand"], 25.0)
        self.assertEqual(overall["rivals"]["Acme"], 50.0)
        self.assertEqual(overall["rivals"]["Globex"], 25.0)

    def test_citation_gap_skips_owned_domains_and_answers_that_mention_brand(self):
        domains = [g["domain"] for g in self.result["citation_gaps"]]
        self.assertEqual(domains[0], "g2.com")  # 1 prompt, 2 engines: highest reach
        self.assertNotIn("acme.com", domains)  # competitor's own site
        self.assertNotIn("blog.example.com", domains)  # cited where the brand already appears
        g2 = self.result["citation_gaps"][0]
        self.assertEqual(g2["source_type"], "review_site")
        self.assertEqual(g2["rivals_present"], ["Acme", "Globex"])

    def test_lost_prompts_only_where_brand_absent_and_rival_present(self):
        lost = self.result["lost_prompts"]
        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0]["prompt"], "prompt 1")
        self.assertEqual(lost[0]["winning_rivals"][0], {"name": "Acme", "engines": ["chatgpt", "gemini"]})

    def test_entity_gaps_from_checks(self):
        signals = [g["signal"] for g in self.result["entity_gaps"]]
        self.assertEqual(signals, ["wikipedia", "reddit"])

    def test_headline_names_top_rival(self):
        headline = self.result["headline"]
        self.assertEqual(headline["top_rival"], "Acme")
        self.assertEqual(headline["prompts_analysed"], 2)

    def test_classify_source(self):
        self.assertEqual(metrics.classify_source("www.reddit.com"), "community")
        self.assertEqual(metrics.classify_source("site.com", "Top 10 tools", ""), "listicle")
        self.assertEqual(
            metrics.classify_source("news.com", "Funding round", "https://news.com/x"), "publisher"
        )

    def test_empty_inputs_do_not_crash(self):
        empty = metrics.compute_metrics(_inputs([], rivals=()))
        self.assertEqual(empty["headline"]["brand_share_of_voice"], 0.0)
        self.assertIsNone(empty["headline"]["top_rival"])


class ContextTests(SimpleTestCase):
    @mock.patch(ASK, return_value=None)
    def test_llm_failure_returns_empty_context(self, _ask):
        inputs = _inputs([_answer(1, "chatgpt", "Acme wins.")])
        self.assertEqual(context.analyse_context(inputs), {})

    @mock.patch(ASK)
    def test_no_excerpts_skips_llm(self, ask):
        inputs = _inputs([_answer(1, "chatgpt", "Signalor wins.", brand=True)])
        self.assertEqual(context.analyse_context(inputs), {})
        ask.assert_not_called()

    @mock.patch(ASK)
    def test_parses_structured_result(self, ask):
        ask.return_value = context.ContextGaps(summary="Rivals have reviews.", ai_recommended_brands=["Acme"])
        result = context.analyse_context(_inputs([_answer(1, "chatgpt", "Acme wins.")]))
        self.assertEqual(result["ai_recommended_brands"], ["Acme"])


class ContextTidyTests(SimpleTestCase):
    @mock.patch(ASK)
    def test_drops_filler_attributes_and_caps_lists(self, ask):
        ask.return_value = context.ContextGaps(
            summary="s",
            ai_recommended_brands=[f"Brand {n}" for n in range(20)],
            rival_attributes=[
                context.RivalAttribute(rival="Acme", attribute="Strong reviews"),
                context.RivalAttribute(rival="Globex", attribute="Not mentioned in answers"),
            ],
        )
        result = context.analyse_context(_inputs([_answer(1, "chatgpt", "Acme wins.")]))
        self.assertEqual(len(result["ai_recommended_brands"]), context.MAX_RECOMMENDED_BRANDS)
        self.assertEqual([a["rival"] for a in result["rival_attributes"]], ["Acme"])


class ReportLifecycleTests(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(organization=self.pro_org, created_by_email=PRO_EMAIL)
        self.run = AnalysisRun.objects.create(
            organization=self.pro_org,
            url=self.pro_org.url,
            email=PRO_EMAIL,
            status=AnalysisRun.Status.COMPLETE,
        )
        Competitor.objects.create(analysis_run=self.run, name="Acme", url="https://acme.com")
        track = PromptTrack.objects.create(analysis_run=self.run, prompt_text="best geo tools")
        result = PromptResult.objects.create(
            prompt_track=track, engine="chatgpt", response_text="Acme is best."
        )
        PromptCitation.objects.create(prompt_result=result, url="https://g2.com/x", domain="g2.com")
        self.path = f"/api/growth/programs/{self.pro_org.slug}/positioning/"

    @mock.patch(ASK, return_value=None)
    def test_build_report_stores_metrics(self, _ask):
        report = PositioningReport.objects.create(program=self.program, analysis_run=self.run)
        build_report(report.id)
        report.refresh_from_db()
        self.assertEqual(report.status, PositioningReport.Status.COMPLETE)
        self.assertEqual(report.citation_gaps[0]["domain"], "g2.com")
        self.assertEqual(report.lost_prompts[0]["prompt"], "best geo tools")

    @mock.patch(DISPATCH)
    def test_post_creates_once_then_reuses(self, dispatch):
        self._body = {}
        first = self.signed("POST", self.path, PRO_EMAIL)
        second = self.signed("POST", self.path, PRO_EMAIL)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(PositioningReport.objects.count(), 1)
        dispatch.assert_called_once()

    @mock.patch(DISPATCH)
    def test_post_without_complete_analysis_is_conflict(self, dispatch):
        AnalysisRun.objects.all().delete()
        self._body = {}
        response = self.signed("POST", self.path, PRO_EMAIL)
        self.assertEqual(response.status_code, 409)
        dispatch.assert_not_called()

    def test_cannot_read_someone_elses_report(self):
        other = GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        PositioningReport.objects.create(program=other)
        response = self.signed("GET", f"/api/growth/programs/{self.other_org.slug}/positioning/", PRO_EMAIL)
        self.assertEqual(response.status_code, 404)
