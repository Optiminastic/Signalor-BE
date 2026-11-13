"""
Connecting the brand's own Hunter or Apollo account.

The owner pastes their API key once. It's checked with the provider, stored
encrypted on the brand's Integration row, and never returned (only its last
four characters). Contact lookups then use the brand's own account first,
and Signalor's shared key only if the brand hasn't connected one.

Run: python manage.py test apps.growth_agent.tests.test_finder_accounts --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.integrations.models import Integration, decrypt_token

from ..models import AgentActionLog
from ..services.outreach.contact_finders import accounts, apollo, hunter, registry
from ..services.outreach.contact_finders.base import FinderError
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase

KEY = "hk_live_1234567890abcd"


class FinderAccountTests(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.base = f"/api/growth/programs/{self.pro_org.slug}/finders"
        from ..models import GrowthProgram

        GrowthProgram.objects.create(organization=self.pro_org, created_by_email=PRO_EMAIL)

    def connect(self, provider="hunter", key=KEY, email=PRO_EMAIL, valid=True):
        self._body = {"api_key": key}
        module = hunter if provider == "hunter" else apollo
        check = mock.patch.object(module, "check_key", side_effect=None if valid else FinderError("bad key"))
        with check:
            return self.signed("POST", f"{self.base}/{provider}/connect/", email)

    def test_connecting_stores_the_key_encrypted_and_shows_only_its_last_four(self):
        response = self.connect()
        self.assertEqual(response.status_code, 200)
        row = Integration.objects.get(organization=self.pro_org, provider="hunter")
        self.assertNotIn(KEY, row.access_token_encrypted)
        self.assertEqual(decrypt_token(row.access_token_encrypted), KEY)
        self.assertNotIn(KEY, str(response.json()))
        hunter_entry = next(f for f in response.json()["finders"] if f["provider"] == "hunter")
        self.assertEqual(
            hunter_entry,
            {
                "provider": "hunter",
                "label": "Hunter",
                "connected": True,
                "method": "api_key",
                "key_hint": "abcd",
                "source": "account",
            },
        )

    def test_an_invalid_key_is_refused_and_nothing_is_stored(self):
        response = self.connect(valid=False)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Integration.objects.filter(provider="hunter").exists())

    def test_the_key_never_reaches_the_audit_log(self):
        self.connect()
        entry = AgentActionLog.objects.filter(action="finder_connected").get()
        self.assertEqual(entry.actor_email, PRO_EMAIL)
        self.assertNotIn(KEY, str(entry.detail))

    def test_listing_shows_what_is_connected(self):
        self.connect(provider="apollo", key="ap_key_9999")
        finders = self.signed("GET", f"{self.base}/", PRO_EMAIL).json()["finders"]
        by = {f["provider"]: f for f in finders}
        self.assertTrue(by["apollo"]["connected"])
        self.assertEqual(by["apollo"]["key_hint"], "9999")
        self.assertFalse(by["hunter"]["connected"])

    def test_disconnecting_removes_the_key(self):
        self.connect()
        self._body = {}
        response = self.signed("POST", f"{self.base}/hunter/disconnect/", PRO_EMAIL)
        self.assertEqual(response.status_code, 200)
        row = Integration.objects.get(organization=self.pro_org, provider="hunter")
        self.assertFalse(row.is_active)
        self.assertEqual(row.access_token_encrypted, "")

    def test_an_unknown_provider_is_not_found(self):
        self._body = {"api_key": KEY}
        self.assertEqual(self.signed("POST", f"{self.base}/zoominfo/connect/", PRO_EMAIL).status_code, 404)

    def test_another_brands_owner_cannot_connect_or_list(self):
        self.assertIn(self.connect(email=OTHER_EMAIL).status_code, (403, 404))
        self.assertIn(self.signed("GET", f"{self.base}/", OTHER_EMAIL).status_code, (403, 404))

    @override_settings(HUNTER_API_KEY="platform-key", APOLLO_API_KEY="")
    def test_the_brands_own_key_is_used_before_signalors(self):
        self.assertEqual(accounts.key_for(self.pro_org, "hunter"), "platform-key")
        self.connect()
        self.assertEqual(accounts.key_for(self.pro_org, "hunter"), KEY)
        self.assertEqual(registry.configured(self.pro_org), ["hunter"])
        with mock.patch.object(hunter, "find", return_value=[]) as find:
            registry.find_all("site.com", self.pro_org)
        find.assert_called_once_with("site.com", KEY)

    @override_settings(HUNTER_API_KEY="", APOLLO_API_KEY="")
    def test_a_brand_with_no_account_and_no_shared_key_has_no_finders(self):
        self.assertEqual(registry.configured(self.pro_org), [])

    @override_settings(HUNTER_API_KEY="", APOLLO_API_KEY="")
    def test_a_disconnected_brand_falls_back_to_no_finders(self):
        self.connect()
        self._body = {}
        self.signed("POST", f"{self.base}/hunter/disconnect/", PRO_EMAIL)
        self.assertEqual(registry.configured(self.pro_org), [])

    def test_a_blank_key_is_refused(self):
        self.assertEqual(self.connect(key="   ").status_code, 400)


def reply(status):
    answer = mock.Mock(status_code=status, ok=status < 400)
    answer.json.return_value = {}
    return answer


class KeyCheckTests(SimpleTestCase):
    def test_hunter_checks_the_key_on_the_free_account_endpoint_in_a_header(self):
        with mock.patch.object(hunter.requests, "get", return_value=reply(200)) as get:
            hunter.check_key("k1")
        args, kwargs = get.call_args
        self.assertEqual(args[0], hunter.ACCOUNT_URL)
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "k1")

    def test_hunter_rejects_a_refused_key(self):
        with mock.patch.object(hunter.requests, "get", return_value=reply(401)):
            with self.assertRaises(FinderError):
                hunter.check_key("bad")

    def test_apollo_checks_with_a_one_result_search(self):
        with mock.patch.object(apollo.requests, "post", return_value=reply(200)) as post:
            apollo.check_key("k2")
        args, kwargs = post.call_args
        self.assertEqual(args[0], apollo.SEARCH_URL)
        self.assertEqual(kwargs["json"]["per_page"], 1)
        self.assertEqual(kwargs["headers"]["x-api-key"], "k2")

    def test_apollo_rejects_a_refused_key(self):
        with mock.patch.object(apollo.requests, "post", return_value=reply(403)):
            with self.assertRaises(FinderError):
                apollo.check_key("bad")
