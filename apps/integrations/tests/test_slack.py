"""Slack integration — the pure pieces, tested without a workspace.

`blocks` and `verify_signature` carry the logic worth pinning: one decides what
a user reads, the other is the security boundary for inbound requests.
"""

import hashlib
import hmac
import time
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.integrations.services.slack import blocks, client


class BlockFormattingTests(SimpleTestCase):
    def _blocks(self, **over):
        kwargs = {
            "brand": "Signalor",
            "url": "https://signalor.ai",
            "score": 62.0,
            "delta": None,
            "tasks": [],
            "dashboard_url": "https://signalor.ai/dashboard/abc",
        }
        kwargs.update(over)
        return blocks.analysis_complete_blocks(**kwargs)

    def test_a_run_with_no_tasks_still_renders(self):
        """A clean site has no recommendations; the message must not break."""
        out = self._blocks()
        self.assertEqual(out[0]["type"], "header")
        self.assertEqual(out[-1]["type"], "actions")

    def test_the_task_list_is_capped_and_says_how_many_are_hidden(self):
        tasks = [{"title": f"Task {i}", "priority": "high", "signal": "Schema"} for i in range(9)]
        text = str(self._blocks(tasks=tasks))
        self.assertIn("Task 0", text)
        self.assertNotIn("Task 8", text)
        self.assertIn(f"and {9 - blocks.MAX_TASKS_SHOWN} more", text)

    def test_no_trend_line_when_there_is_no_previous_run(self):
        """First run has nothing to compare against; inventing 0% would lie."""
        self.assertNotIn("since last run", str(self._blocks(delta=None)))

    def test_trend_direction_reads_correctly(self):
        self.assertIn("+8 since last run", str(self._blocks(delta=8.0)))
        self.assertIn("-5 since last run", str(self._blocks(delta=-5.0)))

    def test_score_band_colours_the_headline(self):
        self.assertIn("large_green_circle", str(self._blocks(score=80)))
        self.assertIn("large_yellow_circle", str(self._blocks(score=50)))
        self.assertIn("red_circle", str(self._blocks(score=10)))


class SignatureVerificationTests(SimpleTestCase):
    SECRET = "shhh"

    def _sign(self, body: bytes, ts: str) -> str:
        base = b"v0:" + ts.encode() + b":" + body
        return "v0=" + hmac.new(self.SECRET.encode(), base, hashlib.sha256).hexdigest()

    def test_a_genuine_request_is_accepted(self):
        body, ts = b"payload=1", str(int(time.time()))
        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": self.SECRET}):
            self.assertTrue(
                client.verify_signature(timestamp=ts, signature=self._sign(body, ts), body=body)
            )

    def test_a_tampered_body_is_rejected(self):
        ts = str(int(time.time()))
        sig = self._sign(b"payload=1", ts)
        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": self.SECRET}):
            self.assertFalse(
                client.verify_signature(timestamp=ts, signature=sig, body=b"payload=2")
            )

    def test_an_old_request_is_rejected_even_when_correctly_signed(self):
        """Replay protection: a captured request must not work forever."""
        body = b"payload=1"
        ts = str(int(time.time()) - client.SIGNATURE_MAX_AGE_SEC - 60)
        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": self.SECRET}):
            self.assertFalse(
                client.verify_signature(timestamp=ts, signature=self._sign(body, ts), body=body)
            )

    def test_missing_secret_refuses_rather_than_accepts(self):
        """Fail closed: no secret configured must never mean "allow"."""
        body, ts = b"payload=1", str(int(time.time()))
        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": ""}):
            self.assertFalse(
                client.verify_signature(timestamp=ts, signature=self._sign(body, ts), body=body)
            )

    def test_a_garbage_timestamp_is_rejected_not_crashed(self):
        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": self.SECRET}):
            self.assertFalse(client.verify_signature(timestamp="nope", signature="v0=x", body=b""))
