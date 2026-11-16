"""
Social step 3: weekly post drafts per platform, owner approval, and posting by
hand until Buffer is connected. The writer model is faked.

Run: python manage.py test apps.growth_agent.tests.test_social_posts --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase

from apps.analyzer.models import AnalysisRun, BrandVisibility

from ..models import AgentActionLog, GrowthAction, GrowthTask
from ..services.control import actions, dispatch, manual_post
from ..services.control.approvals import DecisionRefused
from ..services.execution import executor, social_channel
from ..services.social import check, posts
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase
from .test_social import STORED


class ReviewNoteTests(SimpleTestCase):
    def test_platforms_are_read_from_profile_urls(self):
        self.assertEqual(posts.platform_of("https://www.linkedin.com/company/x/"), "linkedin")
        self.assertEqual(posts.platform_of("https://twitter.com/x"), "x")
        self.assertEqual(posts.platform_of("https://youtube.com/@x"), "")

    def test_drafts_are_checked_for_length_foreign_links_and_instagram_images(self):
        notes = social_channel.review_notes("x" * 300 + " https://evil.example", "x", "https://brand.com")
        self.assertEqual(len(notes), 2)
        self.assertIn("image", social_channel.review_notes("Hello", "instagram", "https://brand.com")[0])
        self.assertEqual(
            social_channel.review_notes("See https://brand.com/p", "linkedin", "https://brand.com"), []
        )


class SocialPostTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        self.pro_org.url = "https://brand.com"
        self.pro_org.save()
        run = AnalysisRun.objects.create(
            url="https://brand.com", organization=self.pro_org, email=PRO_EMAIL, status="complete"
        )
        BrandVisibility.objects.create(analysis_run=run, social_presence_details=STORED)
        self.make_task(
            channel="satellite_content", tactic="satellite_article", target="tracking AI citations"
        )
        check.run_check(self.program)

    def test_next_weeks_posts_are_planned_once_per_platform_the_brand_is_on(self):
        self.assertEqual(posts.queue_weekly(self.program), 3)  # LinkedIn, X, Instagram; not YouTube or Reddit
        self.assertEqual(posts.queue_weekly(self.program), 0)
        planned = GrowthTask.objects.filter(tactic="social_post")
        self.assertEqual({posts.platform_of(t.target_url) for t in planned}, {"linkedin", "x", "instagram"})
        self.assertTrue(all(t.target == "tracking AI citations" or t.target for t in planned))

    def draft(self, text="AI answers cite clear pages. Here is how we think about it."):
        posts.queue_weekly(self.program)
        task = GrowthTask.objects.filter(tactic="social_post", target_url__contains="linkedin").first()
        GrowthTask.objects.filter(pk=task.pk).update(scheduled_for=self.cycle.starts_on)
        post = social_channel.SocialPost(text=text)
        with (
            mock.patch.object(social_channel, "ask_structured", return_value=post),
            mock.patch.object(social_channel, "gather_knowledge", return_value="facts"),
        ):
            executor.run_task(task.id)
        return GrowthAction.objects.get(task=task)

    def test_a_due_post_becomes_a_draft_waiting_for_approval(self):
        action = self.draft()
        self.assertEqual((action.kind, action.state), ("publish_social_post", "awaiting_approval"))
        shown = actions.present(action, action.current_version)
        self.assertEqual(shown["platform"], "linkedin")
        self.assertIn("AI answers cite", shown["text"])

    def approve(self, action):
        version = action.current_version
        self._body = {"version": version.number, "payload_hash": version.payload_hash, "reason": ""}
        path = f"/api/growth/programs/{self.pro_org.slug}/actions/{action.public_id}/approve/"
        return self.signed("POST", path, PRO_EMAIL)

    def test_an_approved_post_waits_for_buffer_and_nothing_is_sent(self):
        action = self.draft()
        self.assertEqual(self.approve(action).status_code, 200)
        with mock.patch.object(social_channel.OwnedSocialChannel, "execute") as execute:
            dispatch.dispatch(action.id)
        execute.assert_not_called()
        action.refresh_from_db()
        self.assertEqual(action.state, "approved")

    def test_the_owner_can_edit_the_post_text(self):
        action = self.draft()
        version = action.current_version
        self._body = {
            "version": version.number,
            "payload_hash": version.payload_hash,
            "fields": {"text": "Shorter."},
        }
        path = f"/api/growth/programs/{self.pro_org.slug}/actions/{action.public_id}/edit/"
        self.assertEqual(self.signed("POST", path, PRO_EMAIL).status_code, 200)
        action.refresh_from_db()
        self.assertEqual(action.current_version.payload["text"], "Shorter.")

    def test_marking_an_approved_post_posted_closes_it_for_good(self):
        action = self.draft()
        self.approve(action)
        self._body = {}
        path = f"/api/growth/programs/{self.pro_org.slug}/actions/{action.public_id}/posted/"
        self.assertIn(self.signed("POST", path, OTHER_EMAIL).status_code, (403, 404))
        self.assertEqual(self.signed("POST", path, PRO_EMAIL).status_code, 200)
        action.refresh_from_db()
        self.assertEqual((action.state, action.task.status), ("succeeded", "done"))
        entry = AgentActionLog.objects.get(action=manual_post.POSTED_BY_OWNER)
        self.assertEqual(entry.actor_email, PRO_EMAIL)
        self.assertEqual(self.signed("POST", path, PRO_EMAIL).status_code, 409)

    def test_a_draft_not_yet_approved_cannot_be_marked_posted(self):
        action = self.draft()
        with self.assertRaises(DecisionRefused):
            manual_post.mark_posted(self.program, action.public_id, PRO_EMAIL)
