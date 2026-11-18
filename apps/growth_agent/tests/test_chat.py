"""
Chat agent: tool scoping and validation, the bounded loop (LLM mocked), turns, API.

Run: python manage.py test apps.growth_agent.tests.test_chat --settings=config.settings.test
"""

import json
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from ..models import (
    CONVERSATION_TITLE_CHARS,
    AgentActionLog,
    AgentMessage,
    GrowthCycle,
    GrowthProgram,
    GrowthTask,
)
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
        _, reply = conversation.start_conversation(self.program, "Skip outreach please")
        with mock.patch(LLM, return_value=final("Outreach skipped.")) as llm:
            agent.run_turn(reply.id)
        reply.refresh_from_db()
        self.assertEqual((reply.status, reply.content), (AgentMessage.Status.COMPLETE, "Outreach skipped."))
        sent = llm.call_args.args[0]
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(sent[-1], {"role": "user", "content": "Skip outreach please"})


class ConversationTests(ChatTestCase):
    def test_one_reply_in_flight_per_brand(self):
        conv, _ = conversation.start_conversation(self.program, "hi")
        with self.assertRaises(conversation.ChatError):
            conversation.post_message(conv, "again")
        with self.assertRaises(conversation.ChatError):
            conversation.start_conversation(self.program, "new chat while busy")

    def test_empty_and_too_long_messages(self):
        with self.assertRaises(conversation.ChatError):
            conversation.start_conversation(self.program, "   ")
        with self.assertRaises(conversation.ChatError):
            conversation.start_conversation(self.program, "x" * (conversation.MAX_MESSAGE_CHARS + 1))
        self.assertEqual(conversation.list_conversations(self.program), [])

    def test_title_is_first_question_and_id_is_url_safe(self):
        conv, _ = conversation.start_conversation(self.program, "Why is Perplexity ignoring us? " * 10)
        self.assertEqual(len(conv.title), CONVERSATION_TITLE_CHARS)
        self.assertRegex(conv.public_id, r"^[a-z0-9]{10}$")

    def test_history_stays_inside_one_conversation(self):
        first, reply = conversation.start_conversation(self.program, "Talk about outreach")
        AgentMessage.objects.filter(pk=reply.pk).update(status=AgentMessage.Status.COMPLETE, content="Sure.")
        _, second_reply = conversation.start_conversation(self.program, "New topic: schema")
        with mock.patch(LLM, return_value=final("Ok.")) as llm:
            agent.run_turn(second_reply.id)
        sent = [m["content"] for m in llm.call_args.args[0][1:]]
        self.assertEqual(sent, ["New topic: schema"])
        self.assertEqual(len(conversation.thread(first)), 2)


class ChatApiTests(ChatTestCase):
    @mock.patch(DISPATCH)
    def test_start_list_read_and_continue_a_chat(self, dispatch):
        base = f"/api/growth/programs/{self.pro_org.slug}/chats/"
        self._body = {"message": "Why is Perplexity ignoring us?"}
        started = self.signed("POST", base, PRO_EMAIL)
        self.assertEqual(started.status_code, 202)
        chat_id = started.data["conversation"]["id"]
        roles = [(m["role"], m["status"]) for m in started.data["messages"]]
        self.assertEqual(roles, [("user", "complete"), ("assistant", "pending")])
        dispatch.assert_called_once()
        listed = self.signed("GET", base, PRO_EMAIL).data["conversations"]
        self.assertEqual([c["id"] for c in listed], [chat_id])
        self.assertEqual(len(self.signed("GET", f"{base}{chat_id}/", PRO_EMAIL).data["messages"]), 2)
        self.assertEqual(
            self.signed("POST", f"{base}{chat_id}/", PRO_EMAIL).status_code, 409
        )  # still pending

    def test_cannot_read_someone_elses_chats(self):
        other = GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        conv, _ = conversation.start_conversation(other, "secret strategy")
        own_slug_path = f"/api/growth/programs/{self.pro_org.slug}/chats/{conv.public_id}/"
        self.assertEqual(self.signed("GET", own_slug_path, PRO_EMAIL).status_code, 404)
        other_path = f"/api/growth/programs/{self.other_org.slug}/chats/"
        self.assertEqual(self.signed("GET", other_path, PRO_EMAIL).status_code, 404)


class ConversationMigrationTests(ChatTestCase):
    def test_existing_thread_becomes_exactly_one_conversation(self):
        from importlib import import_module

        from django.apps import apps as django_apps

        migration = import_module("apps.growth_agent.migrations.0008_agent_conversation")
        for role, text in [("user", "first question"), ("assistant", "answer"), ("user", "follow up")]:
            AgentMessage.objects.create(program=self.program, role=role, content=text)
        migration.move_messages_into_conversations(django_apps, None)
        conversations = list(self.program.conversations.all())
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0].title, "first question")
        self.assertEqual(conversations[0].messages.count(), 3)
