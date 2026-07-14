"""
Unit tests for the LLM-as-judge (``pipeline.judge``).

Pure-logic: the single LLM boundary (``ask_structured``) is patched, so no network.
"""

import os
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer.pipeline import judge
from apps.analyzer.pipeline.judge import JudgeVerdict, judge_output, should_judge


def _verdict(**kw) -> JudgeVerdict:
    base = dict(relevance=5, faithfulness=4, format_quality=5, passed=True, notes="ok")
    base.update(kw)
    return JudgeVerdict(**base)


class JudgeVerdictTests(SimpleTestCase):
    def test_average(self):
        self.assertEqual(_verdict(relevance=3, faithfulness=3, format_quality=3).average, 3.0)
        self.assertEqual(_verdict(relevance=5, faithfulness=4, format_quality=5).average, 4.67)

    def test_score_bounds_enforced(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            _verdict(relevance=6)
        with self.assertRaises(ValidationError):
            _verdict(faithfulness=0)


class JudgeOutputTests(SimpleTestCase):
    def test_empty_output_skips_llm(self):
        with patch.object(judge, "ask_structured") as mock:
            self.assertIsNone(judge_output("task", ""))
            self.assertIsNone(judge_output("task", "   "))
            mock.assert_not_called()

    def test_returns_validated_verdict(self):
        with patch.object(judge, "ask_structured", return_value=_verdict()) as mock:
            v = judge_output("assess eeat", "grounded answer", context="about page")
            self.assertIsInstance(v, JudgeVerdict)
            self.assertTrue(v.passed)
            mock.assert_called_once()

    def test_context_and_reference_reach_prompt(self):
        captured = {}

        def _fake(prompt, schema, **kw):
            captured["prompt"] = prompt
            return _verdict()

        with patch.object(judge, "ask_structured", side_effect=_fake):
            judge_output("the task", "the output", context="SECRET_CONTEXT", reference="GOLDEN")
        self.assertIn("SECRET_CONTEXT", captured["prompt"])
        self.assertIn("GOLDEN", captured["prompt"])
        self.assertIn("the task", captured["prompt"])

    def test_none_verdict_is_failsoft(self):
        with patch.object(judge, "ask_structured", return_value=None):
            self.assertIsNone(judge_output("task", "some output"))


class ShouldJudgeTests(SimpleTestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_JUDGE_ENABLED", None)
            self.assertFalse(should_judge())

    def test_enabled_full_sample_always_true(self):
        with patch.dict(os.environ, {"LLM_JUDGE_ENABLED": "true", "LLM_JUDGE_SAMPLE_RATE": "1.0"}):
            self.assertTrue(should_judge())

    def test_enabled_zero_sample_always_false(self):
        with patch.dict(os.environ, {"LLM_JUDGE_ENABLED": "true", "LLM_JUDGE_SAMPLE_RATE": "0"}):
            self.assertFalse(should_judge())

    def test_bad_sample_rate_falls_back(self):
        with patch.dict(os.environ, {"LLM_JUDGE_ENABLED": "true", "LLM_JUDGE_SAMPLE_RATE": "oops"}):
            # falls back to 0.05 default; just assert it does not raise and returns a bool
            self.assertIn(should_judge(), (True, False))
