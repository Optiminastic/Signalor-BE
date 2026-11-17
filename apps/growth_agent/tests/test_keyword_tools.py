"""
Keyword tools in the + panel: built-in data, Search Console, and the brand's own
Semrush or Ahrefs key (used for keyword reads when connected). Providers are faked.

Run: python manage.py test apps.growth_agent.tests.test_keyword_tools --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.integrations.models import GSCDataSnapshot, Integration

from ..services.keywords import accounts, providers, refresh
from ..services.keywords.report import report
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_keywords import KeywordRefreshTests, kw

SEMRUSH_CSV = (
    "Keyword;Position;Search Volume;Url\ngeo audit;3;900;https://x.com/a\nllm seo;7;300;https://x.com/b\n"
)


class ProviderParsingTests(SimpleTestCase):
    def test_semrush_csv_rows(self):
        rows = providers.semrush_rows(SEMRUSH_CSV)
        self.assertEqual(
            rows[0], {"keyword": "geo audit", "position": 3, "search_volume": 900, "url": "https://x.com/a"}
        )

    def test_semrush_error_text_is_refused(self):
        with self.assertRaises(providers.ProviderError):
            providers.semrush_rows("ERROR 120 :: WRONG KEY - ID PAIR")

    def test_ahrefs_json_rows(self):
        payload = {
            "keywords": [
                {"keyword": "geo audit", "best_position": 2, "volume": 900, "best_position_url": "u"}
            ]
        }
        self.assertEqual(providers.ahrefs_rows(payload)[0]["position"], 2)

    def test_semrush_key_check_wants_a_unit_balance(self):
        with mock.patch.object(providers, "_get") as get:
            get.return_value.text = "1,000"
            providers.semrush_check_key("good")
            get.return_value.text = "ERROR 120 :: WRONG KEY"
            with self.assertRaises(providers.ProviderError):
                providers.semrush_check_key("bad")


@override_settings(DATAFORSEO_LOGIN="login", DATAFORSEO_PASSWORD="pw")
class KeywordToolTests(KeywordRefreshTests):
    def setUp(self):
        super().setUp()
        self.tools = f"/api/growth/programs/{self.pro_org.slug}/keyword-tools/"

    def connect(self, provider="semrush", key="sk-secret-1234", ok=True):
        error = None if ok else providers.ProviderError("Semrush didn't accept that API key.")
        with mock.patch.dict(providers.CHECKS, {provider: mock.Mock(side_effect=error)}):
            self._body = {"api_key": key}
            return self.signed("POST", f"{self.tools}{provider}/connect/", PRO_EMAIL)

    def test_the_panel_lists_all_four_tools_with_built_in_in_use(self):
        tools = self.signed("GET", self.tools, PRO_EMAIL).json()["tools"]
        self.assertEqual([t["provider"] for t in tools], ["signalor", "search_console", "semrush", "ahrefs"])
        self.assertTrue(tools[0]["in_use"])

    def test_a_good_key_is_stored_encrypted_and_shown_only_by_its_last_four(self):
        response = self.connect()
        self.assertEqual(response.status_code, 200)
        semrush = next(t for t in response.json()["tools"] if t["provider"] == "semrush")
        self.assertEqual((semrush["connected"], semrush["in_use"], semrush["key_hint"]), (True, True, "1234"))
        self.assertNotIn("sk-secret", response.content.decode())
        account = Integration.objects.get(provider="semrush")
        self.assertNotIn("sk-secret", account.access_token_encrypted)

    def test_a_refused_key_is_not_stored(self):
        response = self.connect(ok=False)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Integration.objects.filter(provider="semrush").exists())

    def test_reads_use_the_brands_own_account_when_connected(self):
        self.connect()
        with (
            mock.patch.dict(
                providers.FETCHERS, {"semrush": mock.Mock(side_effect=lambda d, key: self.fake_fetch(d, 0))}
            ),
            mock.patch.object(refresh, "fetch_ranked_keywords") as dataforseo,
        ):
            self.assertTrue(refresh.run_refresh(self.program))
        dataforseo.assert_not_called()
        self.assertEqual(report(self.program)["source"], "semrush")

    def test_a_refused_own_account_is_explained(self):
        self.connect()
        refused = mock.Mock(side_effect=providers.ProviderError("no units"))
        with mock.patch.dict(providers.FETCHERS, {"semrush": refused}):
            self.assertFalse(refresh.run_refresh(self.program))
        self.assertIn("Semrush or Ahrefs", report(self.program)["notes"][0])

    def test_disconnect_returns_to_built_in(self):
        self.connect()
        self.signed("POST", f"{self.tools}semrush/disconnect/", PRO_EMAIL)
        self.assertIsNone(accounts.own_source(self.pro_org))

    def test_search_console_queries_show_when_connected(self):
        gsc = Integration.objects.create(
            organization=self.pro_org, provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE
        )
        GSCDataSnapshot.objects.create(
            integration=gsc,
            date_start="2026-10-01",
            date_end="2026-10-30",
            sync_status="complete",
            top_queries=[{"query": "signalor", "clicks": 5, "impressions": 90, "ctr": 0.05, "position": 1.2}],
        )
        self.assertEqual(report(self.program)["search_console"][0]["query"], "signalor")
        tools = self.signed("GET", self.tools, PRO_EMAIL).json()["tools"]
        self.assertTrue(next(t for t in tools if t["provider"] == "search_console")["connected"])

    def test_only_the_owner_can_see_or_change_tools(self):
        self.assertIn(self.signed("GET", self.tools, OTHER_EMAIL).status_code, (403, 404))
        self._body = {"api_key": "x"}
        response = self.signed("POST", f"{self.tools}semrush/connect/", OTHER_EMAIL)
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.signed("POST", f"{self.tools}hunter/connect/", PRO_EMAIL).status_code, 404)


del KeywordRefreshTests  # imported as a base; don't run its tests twice
_ = kw
