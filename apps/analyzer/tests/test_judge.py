"""Tests for the LLM-as-judge (Epic 6). The judge's own LLM call is mocked."""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer.pipeline import judge as judge_mod
from apps.analyzer.pipeline.judge import JudgeVerdict, judge

_ASK = "apps.analyzer.pipeline.structured.ask_structured"


class JudgeTests(SimpleTestCase):
    def test_scores_pass_through_and_pass_is_derived(self):
        with patch(_ASK, return_value=JudgeVerdict(faithfulness=0.9, relevance=0.8, format_score=0.95)):
            v = judge(task="Generate prompts", output="[...]")
        self.assertTrue(v.passed)
        self.assertAlmostEqual(v.relevance, 0.8)

    def test_pass_requires_all_axes_above_threshold(self):
        with patch(_ASK, return_value=JudgeVerdict(faithfulness=0.9, relevance=0.5, format_score=0.9)):
            v = judge(task="t", output="o")
        self.assertFalse(v.passed)  # relevance 0.5 < 0.7

    def test_scores_are_clamped(self):
        with patch(_ASK, return_value=JudgeVerdict(faithfulness=1.7, relevance=-0.3, format_score=0.8)):
            v = judge(task="t", output="o")
        self.assertEqual(v.faithfulness, 1.0)
        self.assertEqual(v.relevance, 0.0)

    def test_none_verdict_is_fail_soft(self):
        with patch(_ASK, return_value=None):
            self.assertIsNone(judge(task="t", output="o"))

    def test_judge_uses_strong_tier_by_default(self):
        with patch(_ASK, return_value=JudgeVerdict()) as mock_ask:
            judge(task="t", output="o")
        self.assertEqual(mock_ask.call_args.kwargs.get("tier"), "strong")

    def test_threshold_constant(self):
        self.assertEqual(judge_mod.PASS_THRESHOLD, 0.7)
