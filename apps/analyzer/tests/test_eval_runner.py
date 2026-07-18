"""Tests for the prompt-eval runner (Epic 6). The judge is mocked; cases are real."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.analyzer.evals import runner
from apps.analyzer.models import PromptEvalLog
from apps.analyzer.pipeline.judge import JudgeVerdict

_JUDGE = "apps.analyzer.pipeline.judge.judge"


def _verdict(f=0.9, r=0.9, fmt=0.9):
    return JudgeVerdict(faithfulness=f, relevance=r, format_score=fmt, passed=True, rationale="ok")


class LoadCasesTests(TestCase):
    def test_golden_cases_load_and_reference_real_prompts(self):
        from apps.analyzer.prompts import list_prompts

        cases = runner.load_cases()
        self.assertGreaterEqual(len(cases), 4)
        known = set(list_prompts())
        for c in cases:
            self.assertIn(c["prompt"], known, f"case {c['id']} references unknown prompt")
            self.assertIn("vars", c)
            self.assertIn("known_good", c)

    def test_filter_by_prompt(self):
        cases = runner.load_cases(prompt="brand_prompts")
        self.assertTrue(cases)
        self.assertTrue(all(c["prompt"] == "brand_prompts" for c in cases))


class RunCaseTests(TestCase):
    def test_recorded_run_persists_log_and_passes(self):
        with patch(_JUDGE, return_value=_verdict()):
            results = runner.run()
        self.assertTrue(results)
        self.assertTrue(all(r.passed for r in results))
        self.assertEqual(PromptEvalLog.objects.count(), len(results))
        row = PromptEvalLog.objects.first()
        self.assertTrue(row.passed)
        self.assertEqual(row.mode, "recorded")
        self.assertTrue(row.prompt_version)

    def test_low_score_fails_against_case_threshold(self):
        # geo_meta requires format_score >= 0.8; a 0.75 must fail that case.
        with patch(_JUDGE, return_value=_verdict(fmt=0.75)):
            r = runner.run_case(runner.load_cases(prompt="geo_meta")[0], persist=False)
        self.assertFalse(r.passed)

    def test_judge_unavailable_marks_case_failed(self):
        with patch(_JUDGE, return_value=None):
            r = runner.run_case(runner.load_cases(prompt="brand_prompts")[0], persist=True)
        self.assertFalse(r.passed)
        self.assertIsNone(r.verdict)
        self.assertEqual(PromptEvalLog.objects.filter(passed=False).count(), 1)

    def test_no_persist_writes_nothing(self):
        with patch(_JUDGE, return_value=_verdict()):
            runner.run(persist=False)
        self.assertEqual(PromptEvalLog.objects.count(), 0)


class TokenCaptureTests(SimpleTestCase):
    def test_extract_usage_reads_openrouter_block(self):
        from apps.analyzer.pipeline.llm import _extract_usage

        usage = _extract_usage({"usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}})
        self.assertEqual(usage, {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20})

    def test_extract_usage_defaults_to_zero(self):
        from apps.analyzer.pipeline.llm import _extract_usage

        self.assertEqual(_extract_usage({}), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
