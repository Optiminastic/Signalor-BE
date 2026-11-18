"""
Social step 2 (+4): connect the brand's Buffer by signing in, read its
channels, and add approved posts to its Buffer queue. Buffer is faked.

Run: python manage.py test apps.growth_agent.tests.test_social_buffer --settings=config.settings.test
"""

from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase, override_settings

from apps.integrations.models import Integration, encrypt_token

from ..models import AgentActionLog
from ..services.execution import social_channel
from ..services.outreach.contact_finders import oauth
from ..services.social import buffer, check
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase

APP = "http://localhost:3002"
RETURN_URL = f"{APP}/dashboard/brand/agent-mode"
CHANNELS = [
    {"id": "c-li", "service": "linkedin", "name": "Brand Pro", "followers": 1200},
    {"id": "c-x", "service": "twitter", "name": "@brandpro", "followers": None},
]


def reply(body=None, status=200):
    answer = mock.Mock(status_code=status, ok=status < 400, headers={"Content-Type": "application/json"})
    answer.json.return_value = body or {}
    return answer


class BufferParsingTests(SimpleTestCase):
    def test_channels_are_read_from_wherever_the_answer_puts_them(self):
        records = buffer._records(
            {"data": {"channels": [{"id": 1, "service": "LinkedIn", "followerCount": 9}]}}
        )
        self.assertEqual(
            buffer.channel_summary(records[0]), {"id": "1", "service": "linkedin", "name": "", "followers": 9}
        )

    def test_platforms_map_to_buffer_services(self):
        self.assertEqual(buffer.channel_for(CHANNELS, "x")["id"], "c-x")
        self.assertIsNone(buffer.channel_for(CHANNELS, "instagram"))

    def test_posts_go_to_the_queue_with_only_declared_arguments(self):
        tool = {
            "name": "create_post",
            "inputSchema": {
                "properties": {
                    "text": {},
                    "channelIds": {"type": "array"},
                    "mode": {"enum": ["shareNow", "addToQueue"]},
                }
            },
        }
        self.assertEqual(
            buffer._post_args(tool, "c-li", "Hello"),
            {"text": "Hello", "channelIds": ["c-li"], "mode": "addToQueue"},
        )

    def test_a_missing_tool_names_what_the_server_offers(self):
        with self.assertRaisesMessage(buffer.BufferError, "offered: get_account"):
            buffer._pick([{"name": "get_account"}], ("create", "post"), "create post")


@override_settings(GROWTH_APP_ORIGINS=[APP], BACKEND_BASE_URL="http://api.test")
class BufferConnectTests(ExecutionTestCase):
    def connect(self, channels=CHANNELS):
        return Integration.objects.create(
            organization=self.pro_org,
            provider="buffer",
            access_token_encrypted=encrypt_token("tok"),
            metadata={"auth": "oauth", "channels": channels},
        )

    def test_sign_in_returns_buffers_login_page_with_only_the_needed_permissions(self):
        self._body = {"return_url": RETURN_URL}
        with mock.patch.object(oauth.requests, "post", return_value=reply({"client_id": "cid"}, 201)):
            response = self.signed(
                "POST", f"/api/growth/programs/{self.pro_org.slug}/social/buffer/sign-in/", PRO_EMAIL
            )
        url = urlparse(response.json()["authorize_url"])
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", "https://auth.buffer.com/auth")
        self.assertEqual(
            set(params["scope"].split()),
            {"posts:read", "posts:write", "account:read", "insights:read", "offline_access"},
        )
        self.assertIn(
            self.signed(
                "POST", f"/api/growth/programs/{self.pro_org.slug}/social/buffer/sign-in/", OTHER_EMAIL
            ).status_code,
            (403, 404),
        )

    def test_the_callback_saves_channels_and_comes_back_with_buffer_params(self):
        self._body = {"return_url": RETURN_URL}
        with mock.patch.object(oauth.requests, "post", return_value=reply({"client_id": "cid"}, 201)):
            response = self.signed(
                "POST", f"/api/growth/programs/{self.pro_org.slug}/social/buffer/sign-in/", PRO_EMAIL
            )
        state = parse_qs(urlparse(response.json()["authorize_url"]).query)["state"][0]
        tokens = reply({"access_token": "at", "refresh_token": "rt", "expires_in": 3600})
        with (
            mock.patch.object(oauth.requests, "post", return_value=tokens),
            mock.patch.object(buffer, "channels", return_value=CHANNELS),
        ):
            back = self.client.get("/api/growth/oauth/finders/callback/", {"state": state, "code": "c"})
        self.assertEqual(back["Location"], f"{RETURN_URL}?buffer_connected=buffer")
        self.assertEqual(buffer.saved_channels(self.pro_org)[0]["id"], "c-li")
        self.assertTrue(AgentActionLog.objects.filter(action="buffer_connected").exists())
        self.assertTrue(check.report(self.program)["buffer"]["connected"])

    def test_approved_posts_wait_until_buffer_is_connected(self):
        channel = social_channel.OwnedSocialChannel()
        self.assertTrue(channel.send_blocker(self.program))
        self.connect()
        self.assertEqual(channel.send_blocker(self.program), "")

    def version(self, platform):
        return SimpleNamespace(
            payload={"platform": platform, "text": "Hello"}, destination={"platform": platform}
        )

    def test_an_approved_post_is_added_to_the_matching_channels_queue(self):
        self.connect()
        task = self.make_task(channel="owned_social", tactic="social_post")
        with mock.patch.object(buffer, "add_to_queue", return_value={"id": "post-1"}) as add:
            outcome = social_channel.OwnedSocialChannel().execute(task, self.version("linkedin"))
        self.assertTrue(outcome.succeeded)
        add.assert_called_once_with(mock.ANY, "c-li", "Hello")
        self.assertEqual(outcome.payload["buffer_answer"], {"id": "post-1"})

    def test_no_matching_buffer_channel_fails_without_any_outside_call(self):
        self.connect()
        task = self.make_task(channel="owned_social", tactic="social_post")
        with (
            mock.patch.object(buffer, "channels", return_value=CHANNELS),
            mock.patch.object(buffer, "add_to_queue") as add,
        ):
            outcome = social_channel.OwnedSocialChannel().execute(task, self.version("instagram"))
        self.assertFalse(outcome.succeeded)
        self.assertFalse(outcome.external_call_made)
        add.assert_not_called()

    def test_disconnecting_forgets_the_sign_in(self):
        self.connect()
        self._body = {}
        response = self.signed(
            "POST", f"/api/growth/programs/{self.pro_org.slug}/social/buffer/disconnect/", PRO_EMAIL
        )
        self.assertFalse(response.json()["buffer"]["connected"])
        self.assertTrue(AgentActionLog.objects.filter(action="buffer_disconnected").exists())

    def test_the_weekly_check_uses_buffers_follower_counts(self):
        self.connect()
        with mock.patch.object(buffer, "channels", return_value=CHANNELS):
            result = check.measure(self.program)
        self.assertEqual(result["platforms"]["linkedin"]["profile"]["followers"], 1200)
