"""
Chat agent: tool scoping and validation, the bounded loop (LLM mocked), turns, API.

Run: python manage.py test apps.growth_agent.tests.test_chat --settings=config.settings.test
"""

import json
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from ..models import AgentActionLog, AgentMessage, GrowthCycle, GrowthProgram, GrowthTask
from ..services.chat import agent, conversation, tools
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase

LLM = "apps.growth_agent.services.chat.agent.ask_llm_with_tools"
DISPATCH = "apps.growth_agent.views.dispatch_chat_turn"


def tool_call(name, args, call_id="c1"):
    message = {"role": "assistant", "content": "", "tool_calls": [{"id": call_id}]}
    return {
        "message": message,
        "text": "",
        "tool_calls": [{"id": call_id, "name": name, "arguments": args}],
        "finish_reason": "tool_calls",
    }


def final(text):
    return {
        "message": {"role": "assistant", "content": text},
        "text": text,
        "tool_calls": [],
        "finish_reason": "stop",
    }


class ChatTestCase(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(
            organization=self.pro_org, created_by_email=PRO_EMAIL, status=GrowthProgram.Status.ACTIVE
        )
        today = timezone.now().date()
        self.cycle = GrowthCycle.objects.create(
            program=self.program,
            starts_on=today,
            ends_on=today + timedelta(days=27),
            baseline_share_of_voice=1.0,
            target_share_of_voice=11.0,
        )
        self.content = self.task("owned_content", "answer_content")
        self.outreach = self.task("outreach_email", "listicle_inclusion")

    def task(self, channel, tactic, cycle=None):
        return GrowthTask.objects.create(
            cycle=cycle or self.cycle,
            week=1,
            scheduled_for=timezone.now().date(),
            channel=channel,
            tactic=tactic,
            title=f"{tactic} task",
        )

    def call(self, name, args=None):
        return json.loads(tools.dispatch(self.program, name, args or {}))


class WriteToolTests(ChatTestCase):
    def test_skip_and_restore_channel(self):
        self.assertEqual(self.call("skip_channel", {"channel": "outreach_email"})["updated"], 1)
        self.outreach.refresh_from_db()
        self.assertEqual(self.outreach.status, GrowthTask.Status.SKIPPED)
        self.call("restore_channel", {"channel": "outreach_email"})
        self.outreach.refresh_from_db()
        self.assertEqual(self.outreach.status, GrowthTask.Status.PLANNED)
        self.assertTrue(AgentActionLog.objects.filter(action="chat_skip_channel").exists())

    def test_cannot_touch_another_brands_task(self):
        other = GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        other_cycle = GrowthCycle.objects.create(
            program=other, starts_on=timezone.now().date(), ends_on=timezone.now().date()
        )
        foreign = self.task("owned_content", "answer_content", cycle=other_cycle)
        self.assertEqual(self.call("skip_tasks", {"task_ids": [foreign.id]})["updated"], 0)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, GrowthTask.Status.PLANNED)

    def test_started_tasks_cannot_change(self):
        GrowthTask.objects.filter(pk=self.content.pk).update(status=GrowthTask.Status.DONE)
        self.assertEqual(self.call("skip_tasks", {"task_ids": [self.content.id]})["updated"], 0)

    def test_move_task_lands_on_a_workday_in_that_week(self):
        result = self.call("move_task", {"task_id": self.content.id, "week": 3})
        self.content.refresh_from_db()
        self.assertEqual(self.content.week, 3)
        offset = (self.content.scheduled_for - self.cycle.starts_on).days
        self.assertTrue(14 <= offset < 21)
        self.assertLess(self.content.scheduled_for.weekday(), 5)
        self.assertEqual(result["week"], 3)

    def test_invalid_arguments_are_rejected(self):
        self.assertIn("error", self.call("move_task", {"task_id": self.content.id, "week": 9}))
        self.assertIn("error", self.call("skip_channel", {"channel": "reddit_bots"}))
        self.assertIn("error", self.call("set_uplift_target", {"points": 500}))

    def test_pause_resume_and_target(self):
        self.assertEqual(self.call("pause_program")["program_status"], "paused")
        self.assertIn("error", self.call("pause_program"))
        self.assertEqual(self.call("resume_program")["program_status"], "active")
        self.assertEqual(self.call("set_uplift_target", {"points": 20})["target_visibility_pct"], 21.0)

    def test_unknown_tool_and_crash_are_contained(self):
        self.assertIn("Unknown tool", self.call("delete_everything")["error"])
        with mock.patch.dict(
            tools._TOOLS, {"get_plan": ("x", {}, mock.Mock(side_effect=RuntimeError("db")))}
        ):
            self.assertIn("failed", self.call("get_plan")["error"])


class ReadToolTests(ChatTestCase):
    def test_get_plan_lists_tasks(self):
        plan = self.call("get_plan")
        self.assertEqual(plan["target_visibility_pct"], 11.0)
        self.assertEqual({t["id"] for t in plan["tasks"]}, {self.content.id, self.outreach.id})

    def test_positioning_without_report(self):
        self.assertFalse(self.call("get_positioning")["available"])


class AgentLoopTests(ChatTestCase):
    def test_tool_then_answer(self):
        with mock.patch(
            LLM, side_effect=[tool_call("skip_channel", {"channel": "outreach_email"}), final("Done.")]
        ):
            reply, trace = agent.run_loop(self.program, [{"role": "system", "content": "s"}])
        self.assertEqual(reply, "Done.")
        self.assertEqual(trace, [{"tool": "skip_channel", "write": True}])
        self.outreach.refresh_from_db()
        self.assertEqual(self.outreach.status, GrowthTask.Status.SKIPPED)

    def test_llm_unavailable_gives_fallback(self):
        with mock.patch(
            LLM, return_value={"message": {}, "text": "", "tool_calls": [], "finish_reason": "no_key"}
        ):
            reply, _ = agent.run_loop(self.program, [])
        self.assertEqual(reply, agent.FALLBACK_REPLY)

    def test_loop_is_bounded(self):
        with mock.patch(LLM, return_value=tool_call("get_plan", {})) as llm:
            reply, trace = agent.run_loop(self.program, [])
        self.assertEqual(llm.call_count, agent.MAX_STEPS)
        self.assertEqual(reply, agent.FALLBACK_REPLY)
        self.assertEqual(len(trace), agent.MAX_STEPS)

    def test_run_turn_fills_pending_reply_with_history(self):
        reply = conversation.post_message(self.program, "Skip outreach please")
        with mock.patch(LLM, return_value=final("Outreach skipped.")) as llm:
            agent.run_turn(reply.id)
        reply.refresh_from_db()
        self.assertEqual((reply.status, reply.content), (AgentMessage.Status.COMPLETE, "Outreach skipped."))
        sent = llm.call_args.args[0]
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(sent[-1], {"role": "user", "content": "Skip outreach please"})


class ConversationTests(ChatTestCase):
    def test_one_reply_in_flight(self):
        conversation.post_message(self.program, "hi")
        with self.assertRaises(conversation.ChatError):
            conversation.post_message(self.program, "again")

    def test_empty_and_too_long_messages(self):
        with self.assertRaises(conversation.ChatError):
            conversation.post_message(self.program, "   ")
        with self.assertRaises(conversation.ChatError):
            conversation.post_message(self.program, "x" * (conversation.MAX_MESSAGE_CHARS + 1))


class ChatApiTests(ChatTestCase):
    @mock.patch(DISPATCH)
    def test_post_and_read_thread(self, dispatch):
        path = f"/api/growth/programs/{self.pro_org.slug}/chat/"
        self._body = {"message": "Why is Perplexity ignoring us?"}
        response = self.signed("POST", path, PRO_EMAIL)
        self.assertEqual(response.status_code, 202)
        roles = [(m["role"], m["status"]) for m in response.data["messages"]]
        self.assertEqual(roles, [("user", "complete"), ("assistant", "pending")])
        dispatch.assert_called_once()
        self.assertEqual(self.signed("POST", path, PRO_EMAIL).status_code, 409)  # reply still pending

    def test_cannot_read_someone_elses_chat(self):
        GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        response = self.signed("GET", f"/api/growth/programs/{self.other_org.slug}/chat/", PRO_EMAIL)
        self.assertEqual(response.status_code, 404)
