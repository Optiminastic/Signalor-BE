"""Linking a prompt task back to the prompt it targets.

A UserAction has no foreign key to a PromptTrack — the only connection is the
prompt *text* stored on the recommendation's evidence. Resolving that safely is
the whole job here, and the contract that matters is the scoping one: the same
prompt string legitimately exists on many runs and many brands, so a lookup
keyed on text alone would hand one tenant a link into another's data.
"""

from django.test import TestCase
from django.utils import timezone

from apps.analyzer.models import (
    AnalysisRun,
    PromptTrack,
    Recommendation,
    UserAction,
)
from apps.analyzer.serializers import UserActionSerializer, prompt_track_index

PROMPT = "best policy administration platforms for UK MGAs"


class PromptTrackIndexTests(TestCase):
    def setUp(self):
        self.run = AnalysisRun.objects.create(url="https://acme.com", email="a@acme.com")

    def _task(self, run, prompt, code="geo_prompt_lost"):
        rec = Recommendation.objects.create(
            analysis_run=run,
            finding_code=code,
            title=f'Win the AI query: "{prompt}"',
            evidence={"prompt": prompt} if prompt else {},
        )
        return UserAction.objects.create(
            user_email="a@acme.com",
            analysis_run=run,
            recommendation=rec,
            action_type=UserAction.ActionType.OTHER
            if hasattr(UserAction.ActionType, "OTHER")
            else UserAction.ActionType.values[0],
            title=rec.title,
        )

    def test_index_maps_a_task_to_its_prompt(self):
        track = PromptTrack.objects.create(analysis_run=self.run, prompt_text=PROMPT)
        task = self._task(self.run, PROMPT)

        index = prompt_track_index([task])

        self.assertEqual(index[(self.run.id, PROMPT)], track.id)

    def test_prompt_from_another_run_is_never_linked(self):
        """The scoping contract: text alone must not cross a run boundary."""
        other_run = AnalysisRun.objects.create(url="https://rival.com", email="b@rival.com")
        PromptTrack.objects.create(analysis_run=other_run, prompt_text=PROMPT)
        task = self._task(self.run, PROMPT)

        index = prompt_track_index([task])
        serialized = UserActionSerializer(task, context={"prompt_track_index": index}).data

        self.assertIsNone(serialized["prompt_track_id"])

    def test_newest_track_wins_when_a_rerun_duplicates_the_prompt(self):
        PromptTrack.objects.create(analysis_run=self.run, prompt_text=PROMPT)
        newer = PromptTrack.objects.create(analysis_run=self.run, prompt_text=PROMPT)
        task = self._task(self.run, PROMPT)

        self.assertEqual(prompt_track_index([task])[(self.run.id, PROMPT)], newer.id)

    def test_soft_deleted_prompts_are_not_linked(self):
        PromptTrack.objects.create(
            analysis_run=self.run, prompt_text=PROMPT, deleted_at=timezone.now()
        )
        task = self._task(self.run, PROMPT)

        self.assertEqual(prompt_track_index([task]), {})

    def test_task_without_a_prompt_costs_no_query(self):
        task = self._task(self.run, "")

        with self.assertNumQueries(0):
            self.assertEqual(prompt_track_index([task]), {})

    def test_whole_page_resolves_in_one_query(self):
        """The reason the index exists at all — no N+1 as the list grows."""
        tasks = []
        for i in range(5):
            text = f"{PROMPT} {i}"
            PromptTrack.objects.create(analysis_run=self.run, prompt_text=text)
            tasks.append(self._task(self.run, text))

        with self.assertNumQueries(1):
            index = prompt_track_index(tasks)

        self.assertEqual(len(index), 5)


class SerializerFallbackTests(TestCase):
    def test_no_context_yields_none_rather_than_a_surprise_query(self):
        run = AnalysisRun.objects.create(url="https://acme.com", email="a@acme.com")
        rec = Recommendation.objects.create(
            analysis_run=run, finding_code="geo_prompt_lost", title="t", evidence={"prompt": PROMPT}
        )
        PromptTrack.objects.create(analysis_run=run, prompt_text=PROMPT)
        task = UserAction.objects.create(
            user_email="a@acme.com",
            analysis_run=run,
            recommendation=rec,
            action_type=UserAction.ActionType.values[0],
            title="t",
        )

        with self.assertNumQueries(0):
            data = UserActionSerializer(task).data

        self.assertIsNone(data["prompt_track_id"])
