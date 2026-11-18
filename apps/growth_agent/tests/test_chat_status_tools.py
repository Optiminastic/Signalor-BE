"""
The chat agent can see everything the agent does: drafts waiting and results,
outreach replies, guest posts, connected tools, and one overview of it all.
It can also start the same checks as the board's buttons, with their limits.

Run: python manage.py test apps.growth_agent.tests.test_chat_status_tools --settings=config.settings.test
"""

import json
from unittest import mock

from ..models import OutreachContact, OutreachThread
from ..services.chat import agent, status_tools, tools, write_tools
from ..services.control import actions
from .test_outreach import OutreachTestCase

NEW_READ_TOOLS = ("get_overview", "get_approvals", "get_outreach", "get_guest_posts", "get_connections")
NEW_WRITE_TOOLS = ("find_guest_sites", "check_social_now", "check_indexing_now")


class ChatStatusToolTests(OutreachTestCase):
    def draft(self):
        prepared, _ = self.prepare()
        return actions.create_action(self.task, prepared)

    def test_waiting_drafts_show_a_preview_and_the_site_not_the_address(self):
        self.draft()
        result = status_tools.get_approvals(self.program, {})
        waiting = result["waiting_for_owner"][0]
        self.assertEqual(result["waiting_total"], 1)
        self.assertTrue(waiting["headline"])
        self.assertNotIn("@", json.dumps(result))

    def test_outreach_replies_are_visible(self):
        contact = OutreachContact.objects.create(
            organization=self.pro_org, email="ed@site.com", domain="site.com"
        )
        OutreachThread.objects.create(
            task=self.task,
            contact=contact,
            gmail_thread_id="t1",
            subject="Hi",
            status=OutreachThread.Status.REPLIED,
        )
        result = status_tools.get_outreach(self.program, {})
        self.assertEqual(result["threads_by_status"], {"Replied": 1})
        self.assertEqual(result["threads"][0]["site"], "site.com")

    def test_the_overview_covers_every_area_in_one_call(self):
        self.draft()
        overview = status_tools.get_overview(self.program, {})
        for key in (
            "plan",
            "drafts",
            "outreach_threads",
            "guest_post_sites",
            "social_score",
        ):
            self.assertIn(key, overview)
        self.assertEqual(sum(overview["drafts"]["need_owner_approval"].values()), 1)
        self.assertEqual(overview["drafts"]["approved_not_sent_yet"]["count_by_kind"], {})
        names = " ".join(overview["plan"]["tasks"])
        self.assertNotIn("_", names)  # plain names, never internal tactic keys
        self.assertTrue(overview["connections"]["gmail"]["connected"])
        self.assertFalse(overview["connections"]["buffer"]["connected"])

    def test_checks_started_from_chat_respect_the_buttons_limits(self):
        with mock.patch("apps.growth_agent.analysis_tasks.dispatch_social_check"):
            self.assertTrue(write_tools.check_social_now(self.program, {})["started"])
            again = write_tools.check_social_now(self.program, {})
        self.assertFalse(again["started"])
        self.assertIn("6 hours", again["reason"])
        self.assertFalse(write_tools.check_indexing_now(self.program, {})["started"])

    def test_new_tools_are_registered_with_the_right_access(self):
        names = [spec["function"]["name"] for spec in tools.specs()]
        for name in NEW_READ_TOOLS + NEW_WRITE_TOOLS:
            self.assertIn(name, names)
        self.assertTrue(set(NEW_WRITE_TOOLS) <= tools.WRITE_TOOLS)
        self.assertFalse(set(NEW_READ_TOOLS) & tools.WRITE_TOOLS)

    def test_the_prompt_covers_approval_limits_languages_and_untrusted_text(self):
        prompt = agent.SYSTEM_PROMPT
        for phrase in ("get_overview", "Guest posts", "language the owner writes", "never\n  instructions"):
            self.assertIn(phrase, prompt)
        self.assertIn("Approve, send or publish anything", prompt)


class ApproveHintTests(OutreachTestCase):
    def test_approve_requests_get_a_direct_reminder_and_others_do_not(self):
        hinted = agent._with_hints([{"role": "user", "content": "just approve all the emails"}])
        self.assertIn(agent.APPROVE_HINT, hinted[-1]["content"])
        self.assertTrue(hinted[-1]["content"].startswith("just approve all the emails"))
        plain = [{"role": "user", "content": "how is it going?"}]
        self.assertEqual(agent._with_hints(plain), plain)
