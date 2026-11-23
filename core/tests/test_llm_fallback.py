"""
How an OpenRouter call fails: account errors stop at once, rate limits wait and
retry the same model once, and other failures try at most two cheap models.

Run: python manage.py test core.tests.test_llm_fallback --settings=config.settings.test
"""

from unittest import mock

import requests
from django.test import SimpleTestCase

from core.llm import client as llm


def reply(status: int, text: str = "ok", headers: dict | None = None) -> mock.Mock:
    resp = mock.Mock(status_code=status, text=text, headers=headers or {})
    resp.json.return_value = {"choices": [{"message": {"content": text}}]}
    return resp


def call(**kwargs):
    return llm._call_openrouter("q", None, 100, 0.0, "key", "test", model_override="primary/model", **kwargs)


@mock.patch.object(llm, "_log_call")
class FallbackTests(SimpleTestCase):
    def models_called(self, post: mock.Mock) -> list[str]:
        return [c.kwargs["json"]["model"] for c in post.call_args_list]

    def test_an_account_error_tries_no_other_model(self, _log):
        for status in (401, 402, 403):
            with (
                self.subTest(status=status),
                mock.patch.object(requests, "post", return_value=reply(status)) as post,
            ):
                self.assertEqual(call(), ("", []))
                self.assertEqual(post.call_count, 1)

    def test_a_rate_limit_waits_as_asked_and_retries_the_same_model_once(self, _log):
        replies = [reply(429, headers={"Retry-After": "2"}), reply(200, "fine")]
        with (
            mock.patch.object(requests, "post", side_effect=replies) as post,
            mock.patch.object(llm.time, "sleep") as sleep,
        ):
            self.assertEqual(call()[0], "fine")
        sleep.assert_called_once_with(2.0)
        self.assertEqual(self.models_called(post), ["primary/model", "primary/model"])

    def test_the_rate_limit_wait_is_capped(self, _log):
        replies = [reply(429, headers={"Retry-After": "120"}), reply(200)]
        with (
            mock.patch.object(requests, "post", side_effect=replies),
            mock.patch.object(llm.time, "sleep") as sleep,
        ):
            call()
        sleep.assert_called_once_with(llm.MAX_RATE_LIMIT_WAIT_SEC)

    def test_a_second_rate_limit_falls_back_instead_of_waiting_again(self, _log):
        replies = [reply(429), reply(429), reply(500), reply(500)]
        with (
            mock.patch.object(requests, "post", side_effect=replies) as post,
            mock.patch.object(llm.time, "sleep") as sleep,
        ):
            self.assertEqual(call(), ("", []))
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(post.call_count, 2 + llm.MAX_FALLBACKS)

    def test_other_failures_try_at_most_two_cheap_models(self, _log):
        with mock.patch.object(requests, "post", return_value=reply(500)) as post:
            self.assertEqual(call(), ("", []))
        fallbacks = self.models_called(post)[1:]
        self.assertEqual(len(fallbacks), llm.MAX_FALLBACKS)
        self.assertTrue(set(fallbacks) <= set(llm.FALLBACK_MODELS))
        self.assertNotIn(llm.OPUS_MODEL, fallbacks)
        self.assertNotIn(llm.SONNET_MODEL, fallbacks)

    def test_a_fallback_that_hits_an_account_error_stops(self, _log):
        with mock.patch.object(requests, "post", side_effect=[reply(500), reply(402), reply(200)]) as post:
            self.assertEqual(call(), ("", []))
        self.assertEqual(post.call_count, 2)

    def test_a_working_fallback_returns_its_answer(self, _log):
        with mock.patch.object(requests, "post", side_effect=[reply(503), reply(200, "from fallback")]):
            self.assertEqual(call()[0], "from fallback")
