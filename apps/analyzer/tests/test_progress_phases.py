"""The analysing screen must describe real work, not a number that stalls.

Two checkpoints used to cover most of the wall-clock: firing every tracked
prompt across seven answer engines (~66% of the run), and discovering and
scoring competitor sites. Both sat on a single value for their whole duration,
so the bar froze at 75 and again at 85 with no explanation.

Three contracts fix that: checkpoints are spaced by *time* rather than pipeline
position, every checkpoint names what it is doing, and the two long loops report
sub-progress as items complete.
"""

import inspect
import re

from django.test import TestCase

from apps.analyzer import tasks
from apps.analyzer.models import AnalysisRun

MAIN = inspect.getsource(tasks.run_single_page_analysis)


def _checkpoints(source: str) -> list[int]:
    return [int(m) for m in re.findall(r"_update_status\(run, AnalysisRun\.Status\.\w+, (\d+)", source)]


class CheckpointSpacingTests(TestCase):
    def test_checkpoints_only_move_forward(self):
        marks = _checkpoints(MAIN)
        self.assertEqual(marks, sorted(marks), f"progress goes backwards: {marks}")

    def test_prompt_firing_owns_the_largest_span(self):
        """It is ~66% of the run; it used to own 5 points out of 100."""
        marks = _checkpoints(MAIN)
        spans = [(b - a) for a, b in zip(marks, marks[1:], strict=False)]
        self.assertEqual(max(spans), 55, f"expected 25->80 to be the widest span, got {spans}")

    def test_every_checkpoint_carries_a_human_phase(self):
        calls = re.findall(r"_update_status\(run, AnalysisRun\.Status\.\w+, \d+([^)]*)\)", MAIN)
        for call in calls:
            with self.subTest(call=call.strip()):
                self.assertIn('"', call, "checkpoint has no phase text")

    def test_no_checkpoint_claims_completion_early(self):
        self.assertTrue(all(m < 100 for m in _checkpoints(MAIN)))


class SubProgressTests(TestCase):
    def setUp(self):
        self.run = AnalysisRun.objects.create(url="https://a.com", email="o@a.com")

    def test_it_interpolates_between_the_two_bounds(self):
        tasks._update_sub_progress(self.run, 25, 78, 5, 10, "Asking AI engines")
        self.run.refresh_from_db()
        self.assertEqual(self.run.progress, 51)  # 25 + 53*0.5
        self.assertIn("5/10", self.run.phase)

    def test_the_first_item_already_moves_the_bar(self):
        """The point is that something changes, not that it finishes."""
        tasks._update_sub_progress(self.run, 25, 78, 1, 10, "Asking AI engines")
        self.run.refresh_from_db()
        self.assertGreater(self.run.progress, 25)

    def test_it_never_overshoots_its_ceiling(self):
        tasks._update_sub_progress(self.run, 25, 78, 99, 10, "Asking AI engines")
        self.run.refresh_from_db()
        self.assertEqual(self.run.progress, 78)

    def test_zero_items_is_a_no_op(self):
        tasks._update_sub_progress(self.run, 25, 78, 0, 0, "Nothing to do")
        self.run.refresh_from_db()
        self.assertEqual(self.run.progress, 0)

    def test_a_failed_write_never_breaks_the_run(self):
        """Progress reporting must not be able to kill an analysis."""
        from unittest.mock import patch

        with patch.object(AnalysisRun.objects, "filter", side_effect=RuntimeError("db down")):
            tasks._update_sub_progress(self.run, 25, 78, 1, 10, "x")  # must not raise

    def test_the_phase_is_capped_to_the_column(self):
        tasks._update_sub_progress(self.run, 0, 100, 1, 2, "x" * 300)
        self.run.refresh_from_db()
        self.assertLessEqual(len(self.run.phase), 140)


class LongLoopsReportProgressTests(TestCase):
    def test_the_prompt_loop_reports_as_prompts_complete(self):
        source = inspect.getsource(tasks._save_probes_and_tracks)
        self.assertIn("_update_sub_progress", source)
        self.assertIn("as_completed", source)

    def test_the_competitor_loop_reports_as_sites_are_scored(self):
        self.assertIn("Scoring competitor sites", MAIN)


class ProgressEndpointTests(TestCase):
    def test_the_poll_endpoint_returns_the_phase(self):
        from django.urls import reverse

        run = AnalysisRun.objects.create(
            url="https://a.com", email="o@a.com", progress=40, status="analyzing"
        )
        AnalysisRun.objects.filter(pk=run.pk).update(phase="Asking AI engines (3/10)")
        body = self.client.get(reverse("analyzer:run-progress"), {"email": "o@a.com"}).json()
        self.assertEqual(body["phase"], "Asking AI engines (3/10)")
        self.assertEqual(body["progress"], 40)
