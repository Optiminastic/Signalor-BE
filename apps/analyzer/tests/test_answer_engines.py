"""Regression tests for answer-engine simulation.

Prompt tracking asks "would ChatGPT mention this brand?" The only way to answer
that is to ask the engine the way a real user does, which means with web search
on. Firing a base model instead answers a different question — "was this brand
famous before the model's training cutoff?" — and for any brand younger than the
cutoff that is always no. That is why every engine card read "not a widely
recognized term" regardless of the brand.

Two invariants are pinned here:
  1. Every answer engine sends a web-search plugin (or searches natively).
  2. A failed engine never has another vendor's answer substituted under its
     label. `x-ai/grok-3-mini` had been deprecated for long enough that the
     "Grok" column was silently serving OpenAI output.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer.pipeline import llm


class AnswerEngineConfigTests(SimpleTestCase):
    def test_every_engine_reaches_the_web(self):
        for name, spec in llm.ANSWER_ENGINES.items():
            with self.subTest(engine=name):
                if spec["search"] is None:
                    # Only a natively-searching model may opt out of the plugin.
                    self.assertIn("perplexity", spec["model"])
                else:
                    self.assertIn(spec["search"], {"native", "exa"})

    def test_no_answer_engine_uses_the_2023_cutoff_model(self):
        models = {spec["model"] for spec in llm.ANSWER_ENGINES.values()}
        self.assertNotIn("openai/gpt-4o-mini", models)

    def test_deprecated_grok_id_is_not_in_use(self):
        self.assertNotEqual(llm.GROK_MODEL, "x-ai/grok-3-mini")

    def test_engine_ids_are_env_overridable(self):
        with patch.dict("os.environ", {"ANSWER_ENGINE_GPT_MODEL": "openai/custom"}):
            self.assertEqual(llm._engine("gpt", "openai/gpt-5-mini", "native")["model"], "openai/custom")

    def test_search_can_be_disabled_per_engine_for_cost_control(self):
        with patch.dict("os.environ", {"ANSWER_ENGINE_LLAMA_SEARCH": "none"}):
            self.assertIsNone(llm._engine("llama", "meta-llama/x", "exa")["search"])


class WebSearchPayloadTests(SimpleTestCase):
    def _capture_payload(self, **kwargs):
        """Run one call and return the JSON body sent to OpenRouter."""
        captured = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "ok"}}]}

        def _fake_post(url, headers=None, json=None, timeout=None):
            captured["json"] = json
            captured["timeout"] = timeout
            return _Resp()

        with patch.object(llm.requests, "post", _fake_post):
            llm._call_openrouter("q", None, 100, 0.0, "key", "test", **kwargs)
        return captured

    def test_web_plugin_is_sent_when_search_is_requested(self):
        captured = self._capture_payload(model_override="anthropic/x", web_search="native")
        self.assertEqual(captured["json"]["plugins"], [{"id": "web", "engine": "native"}])

    def test_exa_engine_is_passed_through(self):
        captured = self._capture_payload(model_override="google/x", web_search="exa")
        self.assertEqual(captured["json"]["plugins"], [{"id": "web", "engine": "exa"}])

    def test_no_plugin_when_search_is_off(self):
        captured = self._capture_payload(model_override="openai/x")
        self.assertNotIn("plugins", captured["json"])

    def test_search_calls_get_a_longer_timeout(self):
        plain = self._capture_payload(model_override="openai/x")
        searched = self._capture_payload(model_override="openai/x", web_search="native")
        self.assertEqual(plain["timeout"], llm.DEFAULT_TIMEOUT_SEC)
        self.assertEqual(searched["timeout"], llm.WEB_SEARCH_TIMEOUT_SEC)

    def test_model_override_wins_over_the_internal_model_set(self):
        captured = self._capture_payload(model_override="x-ai/grok-4.5")
        self.assertEqual(captured["json"]["model"], "x-ai/grok-4.5")


class EngineAttributionTests(SimpleTestCase):
    """A named engine's answer must come from that engine, or not at all."""

    class _Fail:
        status_code = 404

        text = "deprecated"

        @staticmethod
        def json():
            return {}

    def test_failed_engine_is_not_backfilled_by_another_vendor(self):
        with patch.object(llm.requests, "post", lambda *a, **k: self._Fail()), patch.object(
            llm, "_retry_with_next"
        ) as mock_retry:
            text, citations = llm._call_openrouter(
                "q", None, 100, 0.0, "key", "test", model_override="x-ai/dead", allow_fallback=False
            )
        self.assertEqual(text, "")
        self.assertEqual(citations, [])
        mock_retry.assert_not_called()

    def test_internal_calls_still_fall_back(self):
        with patch.object(llm.requests, "post", lambda *a, **k: self._Fail()), patch.object(
            llm, "_retry_with_next", return_value=("recovered", [])
        ) as mock_retry:
            text, _ = llm._call_openrouter("q", "gemini", 100, 0.0, "key", "test")
        self.assertEqual(text, "recovered")
        mock_retry.assert_called_once()

    def test_answer_engines_never_fall_back_across_vendors(self):
        seen = {}

        def _fake(prompt, **kwargs):
            seen.update(kwargs)
            return ("text", [])

        with patch.object(llm, "is_available", return_value=True), patch.object(
            llm, "_get_openrouter_key", return_value="key"
        ), patch.object(llm, "ask_llm_with_citations", _fake):
            llm.ask_answer_engines("q", engines=["claude"])
        self.assertFalse(seen["allow_fallback"])
        self.assertEqual(seen["web_search"], llm.ANSWER_ENGINES["claude"]["search"])


class AskAnswerEnginesTests(SimpleTestCase):
    def test_returns_nothing_without_openrouter(self):
        """Web search needs OpenRouter; an ungrounded answer is worse than none."""
        with patch.object(llm, "is_available", return_value=True), patch.object(
            llm, "_get_openrouter_key", return_value=None
        ):
            self.assertEqual(llm.ask_answer_engines("q"), {})

    def test_unknown_engine_names_are_ignored(self):
        with patch.object(llm, "is_available", return_value=True), patch.object(
            llm, "_get_openrouter_key", return_value="key"
        ):
            self.assertEqual(llm.ask_answer_engines("q", engines=["not-an-engine"]), {})

    def test_each_engine_reports_the_model_that_answered(self):
        with patch.object(llm, "is_available", return_value=True), patch.object(
            llm, "_get_openrouter_key", return_value="key"
        ), patch.object(llm, "ask_llm_with_citations", return_value=("hi", [])):
            out = llm.ask_answer_engines("q", engines=["claude", "grok"])
        self.assertEqual(out["claude"]["model"], llm.ANSWER_ENGINES["claude"]["model"])
        self.assertEqual(out["grok"]["model"], llm.ANSWER_ENGINES["grok"]["model"])

    def test_one_engine_failing_does_not_lose_the_others(self):
        def _flaky(prompt, **kwargs):
            if kwargs.get("model_override") == llm.ANSWER_ENGINES["grok"]["model"]:
                raise RuntimeError("boom")
            return ("answer", [])

        with patch.object(llm, "is_available", return_value=True), patch.object(
            llm, "_get_openrouter_key", return_value="key"
        ), patch.object(llm, "ask_llm_with_citations", _flaky):
            out = llm.ask_answer_engines("q", engines=["claude", "grok"])
        self.assertEqual(out["claude"]["text"], "answer")
        self.assertEqual(out["grok"]["text"], "")


class CostObservabilityTests(SimpleTestCase):
    """OpenRouter returns the exact charge per call; we must not drop it.

    Before this, only three token counts were kept, so nothing in the system knew
    what a run cost and the only way to find out was the provider dashboard.
    """

    def test_usage_extraction_keeps_cost_and_cache_counts(self):
        payload = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cost": 0.0134,
                "prompt_tokens_details": {"cached_tokens": 80},
                "completion_tokens_details": {"reasoning_tokens": 5},
            }
        }
        u = llm._extract_usage(payload)
        self.assertEqual(u["cost"], 0.0134)
        self.assertEqual(u["cached_tokens"], 80)
        self.assertEqual(u["reasoning_tokens"], 5)

    def test_missing_usage_block_is_zeroed_not_crashing(self):
        u = llm._extract_usage({})
        self.assertEqual(u["cost"], 0.0)
        self.assertEqual(u["total_tokens"], 0)


class CostSummaryTests(SimpleTestCase):
    def _logs(self):
        return [
            {"purpose": "Prompt Track (run 1/1) [grok]", "model": "Grok", "duration_ms": 9000,
             "status": "success", "usage": {"prompt_tokens": 54424, "completion_tokens": 2609,
                                            "cost": 0.1343, "cached_tokens": 0}},
            {"purpose": "Prompt Track (run 1/1) [claude]", "model": "Claude Haiku 4.5",
             "duration_ms": 4000, "status": "success",
             "usage": {"prompt_tokens": 10272, "completion_tokens": 567, "cost": 0.0231,
                       "cached_tokens": 8000}},
            {"purpose": "Site Understanding", "model": "Gemini 2.5 Flash", "duration_ms": 800,
             "status": "error", "usage": {}},
        ]

    def test_totals_add_up(self):
        s = llm.summarize_llm_logs(self._logs())
        self.assertAlmostEqual(s["total_cost_usd"], 0.1574, places=4)
        self.assertEqual(s["total_calls"], 3)
        self.assertEqual(s["errors"], 1)
        self.assertEqual(s["cached_tokens"], 8000)

    def test_per_engine_calls_group_under_one_purpose(self):
        """[grok] and [claude] are the same job and must roll up together."""
        s = llm.summarize_llm_logs(self._logs())
        self.assertEqual(s["by_purpose"]["Prompt Track (run 1/1)"]["calls"], 2)

    def test_most_expensive_purpose_is_first(self):
        s = llm.summarize_llm_logs(self._logs())
        self.assertEqual(next(iter(s["by_purpose"])), "Prompt Track (run 1/1)")

    def test_slowest_call_is_identified(self):
        s = llm.summarize_llm_logs(self._logs())
        self.assertEqual(s["slowest_call_ms"], 9000)

    def test_empty_logs_are_safe(self):
        s = llm.summarize_llm_logs([])
        self.assertEqual(s["total_cost_usd"], 0.0)
        self.assertEqual(s["by_purpose"], {})


class SearchEngineCostTests(SimpleTestCase):
    def test_grok_uses_exa_not_native(self):
        """Measured: grok native = 54k tokens/$0.134 vs exa 2.3k/$0.015, fewer citations."""
        self.assertEqual(llm.ANSWER_ENGINES["grok"]["search"], "exa")


class CostPrecisionTests(SimpleTestCase):
    """A cheap call costs ~$0.00002; rounding must not report that as free."""

    def test_sub_cent_cost_is_not_rounded_away(self):
        s = llm.summarize_llm_logs(
            [{"purpose": "p", "model": "m", "duration_ms": 10, "status": "success",
              "usage": {"cost": 2.31e-05}}]
        )
        self.assertGreater(s["total_cost_usd"], 0.0)

    def test_many_small_calls_accumulate(self):
        logs = [{"purpose": "p", "model": "m", "duration_ms": 1, "status": "success",
                 "usage": {"cost": 2.31e-05}} for _ in range(100)]
        self.assertAlmostEqual(llm.summarize_llm_logs(logs)["total_cost_usd"], 0.00231, places=5)


class NullContentTests(SimpleTestCase):
    """`message.content` is genuinely null for reasoning models that run out of
    budget, and for refusals. The key exists, so a .get(..., "") default returns
    None and the next slice raises "'NoneType' object is not subscriptable",
    killing the call. Observed on every ChatGPT prompt-track call in one run.
    """

    def test_null_content_becomes_empty_string(self):
        data = {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}
        self.assertEqual(llm._content_of(data), "")

    def test_normal_content_passes_through(self):
        self.assertEqual(llm._content_of({"choices": [{"message": {"content": "hi"}}]}), "hi")

    def test_missing_choices_is_safe(self):
        self.assertEqual(llm._content_of({}), "")

    def test_null_choices_is_safe(self):
        self.assertEqual(llm._content_of({"choices": None}), "")

    def test_empty_choices_is_safe(self):
        self.assertEqual(llm._content_of({"choices": []}), "")

    def test_a_null_content_response_does_not_crash_the_call(self):
        """End-to-end: the whole reason this bug mattered."""
        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": None}}], "usage": {"cost": 0.07}}

        with patch.object(llm.requests, "post", lambda *a, **k: _Resp()):
            text, citations = llm._call_openrouter(
                "q", None, 100, 0.0, "key", "test", model_override="openai/x", allow_fallback=False
            )
        self.assertEqual(text, "")
        self.assertEqual(citations, [])


class ChatGptEngineTests(SimpleTestCase):
    def test_chatgpt_engine_is_not_a_reasoning_model(self):
        """gpt-5-mini returned content=None and 0 citations at 2x the price."""
        self.assertNotIn("gpt-5", llm.ANSWER_ENGINES["gpt"]["model"])
