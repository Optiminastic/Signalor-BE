"""Tests for entity disambiguation.

Before an engine can decide whether to cite a brand it has to resolve the name.
When it cannot, the prompt reads as "not mentioned" — indistinguishable from a
brand the engine knows and chose to omit. Those are different problems: one
needs better content, the other needs the entity to exist at all, and no amount
of on-page work fixes the second.

Detection is deterministic phrase matching, so it is testable without a model.
The live behaviour was validated separately against a real unresolvable name
(33% confusion, task raised) and against a resolvable one (0 of 7 engines
confused) — both correct.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.analyzer.services import entity_disambiguation as ed

NOT_RECOGNISED = 'There is no widely recognized entity named "Zilqorath" in popular culture.'
DID_YOU_MEAN = 'I am not familiar with "Signalor". Did you mean Signal, the messaging app?'
GOOD = "Signalor is an AI search visibility platform that tracks brand citations in AI answers."


def _run(brand="Signalor"):
    return SimpleNamespace(id=1, brand_name=brand, url="https://signalor.ai", organization=None)


class DetectionTests(SimpleTestCase):
    def test_unrecognised_phrase_is_caught(self):
        kind, suggested = ed.detect(NOT_RECOGNISED, "Zilqorath")
        self.assertEqual(kind, "unrecognised")
        self.assertEqual(suggested, "")

    def test_correction_captures_the_competing_entity(self):
        """The alternative is the most useful part — it names what you lose to."""
        kind, suggested = ed.detect(DID_YOU_MEAN, "Signalor")
        self.assertEqual(kind, "correction")
        self.assertEqual(suggested, "Signal")

    def test_a_correct_answer_is_not_flagged(self):
        self.assertEqual(ed.detect(GOOD, "Signalor"), ("", ""))

    def test_empty_text_is_not_confusion(self):
        self.assertEqual(ed.detect("", "Signalor"), ("", ""))

    def test_correction_outranks_unrecognised(self):
        """Both present: report the one that carries the alternative name."""
        text = 'Not a widely recognized term. Did you mean Semrush?'
        kind, suggested = ed.detect(text, "Signalor")
        self.assertEqual(kind, "correction")
        self.assertEqual(suggested, "Semrush")

    def test_a_spelling_check_of_the_brand_itself_is_not_confusion(self):
        """"Did you mean Signalor?" means the engine resolved the name."""
        self.assertEqual(ed.detect("Did you mean Signalor?", "Signalor"), ("", ""))

    def test_trailing_clause_is_trimmed_from_the_suggestion(self):
        _kind, suggested = ed.detect("Did you mean Signal or something similar?", "Signalor")
        self.assertEqual(suggested, "Signal")

    def test_variant_phrasings_are_covered(self):
        for text in (
            "I'm not familiar with that product.",
            "That isn't a widely recognized brand.",
            "Could not find any information about it.",
        ):
            self.assertEqual(ed.detect(text, "X")[0], "unrecognised", text)


class ProbeTests(SimpleTestCase):
    def _probe(self, answers):
        payload = {e: {"text": t} for e, t in answers.items()}
        with patch("apps.analyzer.pipeline.llm.ask_answer_engines", return_value=payload):
            return ed.probe_identity(_run())

    def test_confusion_rate_is_per_response(self):
        """One bad answer in four is 25% confused, not 'confused'."""
        report = self._probe({"gpt": GOOD, "claude": GOOD, "gemini": GOOD, "grok": DID_YOU_MEAN})
        self.assertEqual(report.responses, 4)
        self.assertEqual(report.confused, 1)
        self.assertEqual(report.confusion_rate, 0.25)

    def test_below_threshold_is_not_blocking(self):
        self.assertFalse(
            self._probe({"a": GOOD, "b": GOOD, "c": GOOD, "d": DID_YOU_MEAN}).is_blocking
        )

    def test_at_or_above_threshold_is_blocking(self):
        self.assertTrue(self._probe({"a": DID_YOU_MEAN, "b": GOOD}).is_blocking)

    def test_a_failed_engine_call_is_not_counted_as_confusion(self):
        report = self._probe({"gpt": "", "claude": GOOD})
        self.assertEqual(report.responses, 1)
        self.assertEqual(report.confused, 0)

    def test_competing_entities_are_aggregated(self):
        report = self._probe({"a": DID_YOU_MEAN, "b": DID_YOU_MEAN, "c": GOOD})
        self.assertEqual(report.top_alternatives[0], {"name": "Signal", "count": 2})

    def test_per_engine_breakdown_is_reported(self):
        report = self._probe({"gpt": DID_YOU_MEAN, "claude": GOOD})
        self.assertEqual(report.by_engine["gpt"]["confused"], 1)
        self.assertEqual(report.by_engine["claude"]["confused"], 0)

    def test_no_brand_name_yields_an_empty_report(self):
        with patch(
            "apps.analyzer.pipeline.llm.ask_answer_engines",
            side_effect=AssertionError("should not probe"),
        ):
            self.assertEqual(ed.probe_identity(_run(brand="  ")).responses, 0)

    def test_report_is_json_safe(self):
        import json

        json.dumps(self._probe({"gpt": DID_YOU_MEAN}).as_dict())


class TaskTests(SimpleTestCase):
    def _report(self, rate, alternatives=None):
        report = ed.DisambiguationReport(brand="Signalor", responses=10)
        report.confused = int(rate * 10)
        report.confusion_rate = rate
        report.is_blocking = rate >= ed.CONFUSION_THRESHOLD
        report.by_engine = {"ChatGPT": {"responses": 10, "confused": report.confused, "rate": rate}}
        report.top_alternatives = [{"name": n, "count": 1} for n in (alternatives or [])]
        return report

    def test_blocking_confusion_raises_a_critical_task(self):
        recs = ed.to_recommendations(self._report(0.6, ["Signal"]))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["priority"], "critical")
        self.assertEqual(recs[0]["finding_code"], "entity_unresolved")

    def test_the_task_names_what_the_brand_is_mistaken_for(self):
        recs = ed.to_recommendations(self._report(0.6, ["Signal"]))
        self.assertIn("Signal", recs[0]["description"])

    def test_occasional_confusion_raises_nothing(self):
        """One odd answer per brand is normal; a critical task for it burns trust."""
        self.assertEqual(ed.to_recommendations(self._report(0.1)), [])

    def test_no_responses_raises_nothing(self):
        self.assertEqual(ed.to_recommendations(ed.DisambiguationReport(brand="X")), [])

    def test_task_only_uses_real_model_fields(self):
        from apps.analyzer.models import Recommendation

        allowed = {f.name for f in Recommendation._meta.get_fields()}
        for rec in ed.to_recommendations(self._report(0.6, ["Signal"])):
            self.assertTrue(set(rec).issubset(allowed), set(rec) - allowed)

    def test_action_prescribes_entity_work_not_content_work(self):
        action = ed.to_recommendations(self._report(0.6))[0]["action"]
        self.assertIn("Wikidata", action)
        self.assertIn("sameAs", action)


class EndpointValidationTests(TestCase):
    """POST runs/s/<slug>/entity-resolution/ — malformed bodies are 400, not 500.

    ``engines`` reached a ``for`` loop downstream, so a scalar raised TypeError
    and surfaced as a server error for what is plainly a bad request. Each name
    also costs one billable call, so the list is bounded to the known set.
    """

    def setUp(self):
        from apps.analyzer.models import AnalysisRun
        from apps.organizations.models import Organization

        self.org = Organization.objects.create(name="Acme", owner_email="o@acme.com")
        self.run = AnalysisRun.objects.create(
            url="https://acme.com", organization=self.org, brand_name="Acme"
        )

    def _post(self, body):
        from django.urls import reverse

        from apps.analyzer.tests.auth_helpers import signed_in

        with signed_in(self.org.owner_email):
            return self.client.post(
                reverse("analyzer:entity-resolution", args=[self.run.slug]),
                data=body,
                content_type="application/json",
            )

    def test_a_scalar_engines_value_is_400(self):
        self.assertEqual(self._post({"engines": 5}).status_code, 400)

    def test_a_string_engines_value_is_400(self):
        """Previously iterated per character and silently probed nothing."""
        self.assertEqual(self._post({"engines": "gpt"}).status_code, 400)

    def test_an_unknown_engine_name_is_400(self):
        self.assertEqual(self._post({"engines": ["not-an-engine"]}).status_code, 400)

    def test_an_empty_engines_list_is_400_rather_than_meaning_all(self):
        self.assertEqual(self._post({"engines": []}).status_code, 400)

    def test_a_known_engine_is_accepted(self):
        from unittest.mock import patch

        with patch(
            "apps.analyzer.services.entity_disambiguation.probe_identity"
        ) as probe:
            probe.return_value.as_dict.return_value = {"ok": True}
            resp = self._post({"engines": ["gpt"]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(probe.call_args.kwargs["engines"], ["gpt"])

    def test_an_omitted_engines_key_means_every_engine(self):
        from unittest.mock import patch

        with patch(
            "apps.analyzer.services.entity_disambiguation.probe_identity"
        ) as probe:
            probe.return_value.as_dict.return_value = {"ok": True}
            resp = self._post({})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(probe.call_args.kwargs["engines"])
