"""recheck_brand_mentions: re-read stored answers with the fixed matcher.

Run: python manage.py test apps.analyzer.tests.test_recheck_brand_mentions --settings=config.settings.test
"""

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.analyzer.models import AnalysisRun, PromptResult, PromptTrack
from apps.organizations.models import Organization


class RecheckBrandMentionsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Scrn Studio", url="https://scrnstudio.in/", owner_email="o@example.test"
        )
        run = AnalysisRun.objects.create(
            organization=self.org,
            url="https://scrnstudio.in/",
            email="o@example.test",
            brand_name="Scrnstudio",
        )
        track = PromptTrack.objects.create(
            analysis_run=run, prompt_text="hand screen printed t-shirts in Mumbai"
        )
        self.missed = PromptResult.objects.create(
            prompt_track=track,
            engine="perplexity",
            response_text="A strong match is **SCRN STUDIO** in Mumbai.",
        )
        self.absent = PromptResult.objects.create(
            prompt_track=track, engine="chatgpt", response_text="Try Bewakoof or Bluorng."
        )
        self.marked = PromptResult.objects.create(
            prompt_track=track, engine="claude", response_text="Bluorng is popular.", brand_mentioned=True
        )

    def run_command(self, *args):
        out = StringIO()
        call_command("recheck_brand_mentions", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_and_changes_nothing(self):
        out = self.run_command("--org", self.org.slug)
        self.assertIn("Would mark 1 answers", out)
        self.missed.refresh_from_db()
        self.assertFalse(self.missed.brand_mentioned)

    def test_apply_marks_only_answers_that_name_the_brand(self):
        self.run_command("--org", self.org.slug, "--apply")
        for result, expected in ((self.missed, True), (self.absent, False), (self.marked, True)):
            result.refresh_from_db()
            self.assertEqual(result.brand_mentioned, expected, result.engine)

    def test_never_unmarks_and_is_safe_to_rerun(self):
        self.run_command("--org", self.org.slug, "--apply")
        out = self.run_command("--org", self.org.slug, "--apply")
        self.assertIn("Marked 0 answers", out)
        self.marked.refresh_from_db()
        self.assertTrue(self.marked.brand_mentioned)

    def test_other_brands_are_untouched(self):
        self.run_command("--org", "some-other-brand", "--apply")
        self.missed.refresh_from_db()
        self.assertFalse(self.missed.brand_mentioned)

    def test_needs_a_scope(self):
        with self.assertRaises(CommandError):
            self.run_command("--apply")
