"""
Sign in with Hunter or Apollo (OAuth + their MCP servers), like Claude and ChatGPT.

No network: HTTP calls to the providers are faked. Shapes follow the
providers' published OAuth metadata and the MCP Streamable HTTP spec.

Run: python manage.py test apps.growth_agent.tests.test_finder_oauth --settings=config.settings.test
"""

import base64
import hashlib
import json
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.integrations.models import Integration, decrypt_token, encrypt_token

from ..models import AgentActionLog, GrowthProgram
from ..services.outreach.contact_finders import accounts, mcp_client, mcp_finders, oauth, registry
from ..services.outreach.contact_finders.base import FinderError
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase

APP = "http://localhost:3002"
RETURN_URL = f"{APP}/dashboard/brand-slug/agent-mode/chat-1"
CALLBACK = "/api/growth/oauth/finders/callback/"


def reply(body=None, status=200, headers=None, text=None):
    answer = mock.Mock(
        status_code=status, ok=status < 400, headers=headers or {"Content-Type": "application/json"}
    )
    answer.json.return_value = body if body is not None else {}
    answer.text = text if text is not None else json.dumps(body or {})
    return answer


@override_settings(GROWTH_APP_ORIGINS=[APP], BACKEND_BASE_URL="http://api.test")
class ReturnUrlTests(SimpleTestCase):
    def test_only_agent_mode_pages_on_our_origins_are_accepted(self):
        self.assertTrue(oauth.valid_return_url(RETURN_URL))
        self.assertTrue(oauth.valid_return_url(f"{APP}/dashboard/brand-slug/agent-mode"))
        self.assertFalse(oauth.valid_return_url("https://evil.example/dashboard/x/agent-mode"))
        self.assertFalse(oauth.valid_return_url(f"{APP}/settings"))
        self.assertFalse(oauth.valid_return_url("javascript:alert(1)"))
        self.assertFalse(oauth.valid_return_url(""))


@override_settings(
    GROWTH_APP_ORIGINS=[APP], BACKEND_BASE_URL="http://api.test", HUNTER_API_KEY="", APOLLO_API_KEY=""
)
class SignInFlowTests(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(organization=self.pro_org, created_by_email=PRO_EMAIL)
        self.base = f"/api/growth/programs/{self.pro_org.slug}/finders"

    def sign_in(self, provider="hunter", email=PRO_EMAIL, return_url=RETURN_URL, register=None):
        self._body = {"return_url": return_url}
        registration = register or reply({"client_id": "client-123"}, 201)
        with mock.patch.object(oauth.requests, "post", return_value=registration) as post:
            response = self.signed("POST", f"{self.base}/{provider}/sign-in/", email)
        return response, post

    def authorize_params(self, response):
        url = urlparse(response.json()["authorize_url"])
        return url, {k: v[0] for k, v in parse_qs(url.query).items()}

    def test_sign_in_registers_signalor_and_returns_the_providers_login_page(self):
        response, post = self.sign_in()
        self.assertEqual(response.status_code, 200)
        url, params = self.authorize_params(response)
        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", "https://hunter.io/oauth/authorize")
        self.assertEqual(params["client_id"], "client-123")
        self.assertEqual(params["code_challenge_method"], "S256")
        self.assertEqual(params["redirect_uri"], f"http://api.test{CALLBACK}")
        self.assertEqual(params["resource"], "https://mcp.hunter.io")
        registration = post.call_args.kwargs["json"]
        self.assertEqual(registration["token_endpoint_auth_method"], "none")
        self.assertEqual(registration["redirect_uris"], [f"http://api.test{CALLBACK}"])
        self.assertNotIn(PRO_EMAIL, params["state"])  # the state is encrypted

    def test_apollo_asks_only_for_the_scopes_contact_finding_needs(self):
        _, params = self.authorize_params(self.sign_in("apollo")[0])
        self.assertEqual(set(params["scope"].split()), set(oauth.SERVERS["apollo"].scopes))
        self.assertNotIn("emailer_messages_send_now", params["scope"])

    def test_a_return_page_outside_our_app_is_refused(self):
        response, post = self.sign_in(return_url="https://evil.example/dashboard/x/agent-mode")
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()

    def test_unknown_provider_and_other_owners_are_refused(self):
        self.assertEqual(self.sign_in("zoominfo")[0].status_code, 404)
        self.assertIn(self.sign_in(email=OTHER_EMAIL)[0].status_code, (403, 404))

    def test_registration_failure_is_reported_plainly(self):
        response, _ = self.sign_in(register=reply({}, 500))
        self.assertEqual(response.status_code, 502)
        self.assertIn("API key", response.json()["detail"])

    def callback(self, state, code="code-1", tokens=None, status=200, **extra):
        token_reply = reply(
            tokens or {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600}, status
        )
        with mock.patch.object(oauth.requests, "post", return_value=token_reply) as post:
            response = self.client.get(CALLBACK, {"state": state, "code": code, **extra})
        return response, post

    def started_state(self, provider="hunter"):
        _, params = self.authorize_params(self.sign_in(provider)[0])
        return params

    def test_callback_stores_encrypted_tokens_and_returns_to_agent_mode(self):
        params = self.started_state()
        response, post = self.callback(params["state"])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{RETURN_URL}?finder_connected=hunter")
        row = Integration.objects.get(organization=self.pro_org, provider="hunter")
        self.assertEqual(decrypt_token(row.access_token_encrypted), "at-1")
        self.assertEqual(decrypt_token(row.refresh_token_encrypted), "rt-1")
        self.assertNotIn("at-1", row.access_token_encrypted)
        self.assertEqual(row.metadata["auth"], "oauth")
        self.assertEqual(row.metadata["client_id"], "client-123")
        self.assertIsNotNone(row.token_expiry)
        form = post.call_args.kwargs["data"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(
            b"="
        )
        self.assertEqual(challenge.decode(), params["code_challenge"])  # PKCE round trip
        entry = AgentActionLog.objects.get(action="finder_connected")
        self.assertEqual(entry.detail, {"provider": "hunter", "method": "oauth"})
        self.assertNotIn("at-1", json.dumps(entry.detail))

    def test_after_sign_in_the_brand_shows_as_connected_by_sign_in(self):
        self.callback(self.started_state()["state"])
        hunter_entry = accounts.describe(self.pro_org)[0]
        self.assertEqual((hunter_entry["connected"], hunter_entry["method"]), (True, "oauth"))
        self.assertEqual(registry.configured(self.pro_org), ["hunter"])

    def test_a_tampered_state_stores_nothing(self):
        response, post = self.callback("not-a-real-state")
        self.assertIn("finder_error=invalid_state", response["Location"])
        post.assert_not_called()
        self.assertFalse(Integration.objects.filter(provider="hunter").exists())

    def test_an_expired_state_is_refused(self):
        state = self.started_state()["state"]
        later = oauth.time.time() + oauth.STATE_MAX_AGE_SECONDS + 1
        with mock.patch.object(oauth.time, "time", return_value=later):
            response, post = self.callback(state)
        self.assertEqual(response["Location"], f"{RETURN_URL}?finder_error=expired")
        post.assert_not_called()

    def test_if_the_brand_changed_owner_meanwhile_nothing_is_stored(self):
        state = self.started_state()["state"]
        self.pro_org.owner_email = "new-owner@brand.com"
        self.pro_org.save()
        response, _ = self.callback(state)
        self.assertIn("finder_error=not_owner", response["Location"])
        self.assertFalse(Integration.objects.filter(provider="hunter").exists())

    def test_declining_on_the_providers_page_comes_back_with_a_reason(self):
        response, post = self.callback(self.started_state()["state"], error="access_denied")
        self.assertIn("finder_error=denied", response["Location"])
        post.assert_not_called()

    def test_a_failed_token_exchange_stores_nothing(self):
        response, _ = self.callback(self.started_state()["state"], status=400)
        self.assertIn("finder_error=token_exchange", response["Location"])
        self.assertFalse(Integration.objects.filter(provider="hunter").exists())

    def test_an_api_key_replaces_a_sign_in_and_disconnect_forgets_both(self):
        self.callback(self.started_state()["state"])
        with mock.patch.object(accounts.hunter, "check_key"):
            accounts.connect(self.pro_org, "hunter", "hk_1234")
        row = Integration.objects.get(provider="hunter")
        self.assertEqual((row.metadata["auth"], row.refresh_token_encrypted), ("api_key", ""))
        accounts.disconnect(self.pro_org, "hunter")
        row.refresh_from_db()
        self.assertEqual((row.access_token_encrypted, row.is_active), ("", False))
        self.assertNotIn("auth", row.metadata)

    def test_an_expired_token_is_refreshed_before_use(self):
        self.callback(self.started_state()["state"])
        row = Integration.objects.get(provider="hunter")
        row.token_expiry = timezone.now() - timedelta(minutes=1)
        row.save()
        fresh = reply({"access_token": "at-2", "expires_in": 3600})
        with mock.patch.object(oauth.requests, "post", return_value=fresh) as post:
            self.assertEqual(oauth.access_token(row), "at-2")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(decrypt_token(row.refresh_token_encrypted), "rt-1")  # kept when not rotated


class McpClientTests(SimpleTestCase):
    def test_initialize_then_call_a_tool_carrying_the_session_id(self):
        replies = [
            reply(
                {"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={"Content-Type": "application/json", "Mcp-Session-Id": "s-9"},
            ),
            reply(status=202),
            reply(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"content": [{"type": "text", "text": '{"data": {"emails": []}}'}]},
                }
            ),
        ]
        with mock.patch.object(mcp_client.requests, "post", side_effect=replies) as post:
            session = mcp_client.McpSession("https://mcp.example/mcp", "tok")
            session.initialize()
            result = session.call_tool("domain_search", {"domain": "site.com"})
        self.assertEqual(result, {"data": {"emails": []}})
        last = post.call_args_list[-1].kwargs
        self.assertEqual(last["headers"]["Mcp-Session-Id"], "s-9")
        self.assertEqual(last["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(last["json"]["method"], "tools/call")

    def test_reads_an_event_stream_answer(self):
        body = (
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"domain_search"}]}}\n\n'
        )
        stream = reply(headers={"Content-Type": "text/event-stream"}, text=body)
        with mock.patch.object(mcp_client.requests, "post", return_value=stream):
            tools = mcp_client.McpSession("https://mcp.example/mcp", "tok").list_tools()
        self.assertEqual(tools, [{"name": "domain_search"}])

    def test_a_refused_token_and_a_tool_error_raise(self):
        with mock.patch.object(mcp_client.requests, "post", return_value=reply(status=401)):
            with self.assertRaises(mcp_client.McpAuthError):
                mcp_client.McpSession("u", "t").list_tools()
        failed = reply(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"isError": True, "content": [{"type": "text", "text": "no credits"}]},
            }
        )
        with mock.patch.object(mcp_client.requests, "post", return_value=failed):
            with self.assertRaises(FinderError):
                mcp_client.McpSession("u", "t").call_tool("domain_search", {})


class FakeSession:
    """Stands in for McpSession: fixed tool list, records calls, returns canned output."""

    def __init__(self, tools, outputs, refuse_first=False):
        self.tools, self.outputs, self.calls, self.refuse = tools, outputs, [], refuse_first

    def __call__(self, url, token):
        self.token = token
        return self

    def initialize(self):
        if self.refuse:
            self.refuse = False
            raise mcp_client.McpAuthError("expired")

    def list_tools(self):
        return self.tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.outputs[name]


HUNTER_TOOLS = [
    {"name": "email_finder", "inputSchema": {"properties": {"domain": {}, "full_name": {}}}},
    {
        "name": "domain_search",
        "inputSchema": {"properties": {"domain": {"type": "string"}, "limit": {}, "type": {}}},
    },
]
APOLLO_TOOLS = [
    {
        "name": "apollo_mixed_people_api_search",
        "inputSchema": {
            "properties": {
                "q_organization_domains_list": {"type": "array"},
                "person_titles": {},
                "per_page": {},
            }
        },
    },
    {"name": "apollo_people_bulk_match", "inputSchema": {"properties": {"details": {}}}},
    {"name": "apollo_people_match", "inputSchema": {"properties": {"id": {}}}},
]


class McpFinderTests(SimpleTestCase):
    def integration(self, provider):
        return Integration(
            provider=provider, access_token_encrypted=encrypt_token("tok"), metadata={"auth": "oauth"}
        )

    def run_with(self, fake, work):
        with mock.patch.object(mcp_finders, "McpSession", fake):
            return work()

    def test_hunter_uses_domain_search_with_only_the_declared_arguments(self):
        output = {
            "data": {
                "emails": [
                    {
                        "value": "jane@site.com",
                        "type": "personal",
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "confidence": 90,
                    }
                ]
            }
        }
        fake = FakeSession(HUNTER_TOOLS, {"domain_search": output})
        found = self.run_with(fake, lambda: mcp_finders.find(self.integration("hunter"), "site.com"))
        self.assertEqual([c.email for c in found], ["jane@site.com"])
        self.assertEqual(
            fake.calls, [("domain_search", {"domain": "site.com", "limit": 10, "type": "personal"})]
        )

    def test_apollo_searches_people_then_reveals_one_email_with_people_match(self):
        fake = FakeSession(
            APOLLO_TOOLS,
            {
                "apollo_mixed_people_api_search": {
                    "people": [
                        {
                            "id": "p1",
                            "first_name": "Sam",
                            "last_name_obfuscated": "Ro***s",
                            "title": "Editor",
                            "has_email": True,
                        }
                    ]
                },
                "apollo_people_match": {"person": {"email": "sam@site.com"}},
            },
        )
        integration = self.integration("apollo")
        found = self.run_with(fake, lambda: mcp_finders.find(integration, "site.com"))
        self.assertEqual([c.ref for c in found], ["p1"])
        self.assertEqual(fake.calls[0][1]["q_organization_domains_list"], ["site.com"])
        email = self.run_with(fake, lambda: mcp_finders.reveal(integration, "p1"))
        self.assertEqual(email, "sam@site.com")
        self.assertEqual(fake.calls[-1], ("apollo_people_match", {"id": "p1"}))  # not the bulk tool

    def test_a_missing_tool_names_what_the_server_offers(self):
        fake = FakeSession([{"name": "lists_create"}], {})
        with self.assertRaisesMessage(FinderError, "offered: lists_create"):
            self.run_with(fake, lambda: mcp_finders.find(self.integration("apollo"), "site.com"))

    def test_a_refused_token_is_refreshed_once_and_retried(self):
        fake = FakeSession(HUNTER_TOOLS, {"domain_search": {"data": {"emails": []}}}, refuse_first=True)
        with mock.patch.object(oauth, "access_token", side_effect=["old", "new"]) as token:
            self.run_with(fake, lambda: mcp_finders.find(self.integration("hunter"), "site.com"))
        self.assertEqual(token.call_args_list[1].kwargs, {"force_refresh": True})
        self.assertEqual(fake.token, "new")

    def test_the_registry_sends_signed_in_brands_through_mcp(self):
        connection = accounts.Connection(accounts.METHOD_OAUTH, integration=self.integration("hunter"))
        with (
            mock.patch.object(accounts, "connection_for", return_value=connection),
            mock.patch.object(mcp_finders, "find", return_value=[]) as find,
            mock.patch.object(registry.hunter, "find") as rest_find,
        ):
            registry.find_all("site.com", organization=object())
        find.assert_called()
        rest_find.assert_not_called()
