"""
Satellite step 2: the agent writes a planned article for one of signalor.ai's
sites, the owner approves it, then it's published with a sponsored client link.

No network: the writer model, the blog store and IndexNow are faked.

Run: python manage.py test apps.growth_agent.tests.test_satellite_channel --settings=config.settings.test
"""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.analyzer.services.indexnow import SubmissionResult

from ..models import GrowthAction, GrowthTask
from ..services.control import actions
from ..services.execution import executor, satellite_channel
from ..services.execution.article import ArticleDraft, FaqItem, Section
from ..services.execution.safety import SafetyVerdict
from .test_execution_flow import ExecutionTestCase
from .test_planning_satellite import SITES

STORE = "apps.analyzer.blog_store"
ARTICLE = ArticleDraft(
    title="How to get cited by ChatGPT",
    meta_description="What decides which brands AI answers cite.",
    answer="AI answers cite pages that answer the question directly. <a href='https://evil.example'>x</a>",
    sections=[Section(heading="What matters", paragraphs=["Clear answers."], bullets=["Structure"])],
    faq=[FaqItem(question="Is it fast?", answer="It takes weeks.")],
)


class HtmlTests(SimpleTestCase):
    def test_the_only_link_is_the_client_link_and_it_is_sponsored(self):
        html = satellite_channel.article_html(ARTICLE, "Brand Pro", "https://brand-pro.com")
        self.assertEqual(html.count("<a "), 1)
        self.assertIn('<a href="https://brand-pro.com" rel="sponsored noopener">Brand Pro</a>', html)
        self.assertIn("&lt;a href=", html)  # the model's own markup is shown as text, never a link

    @override_settings(SATELLITE_SITES=SITES)
    def test_site_addresses_map_back_to_site_keys(self):
        self.assertEqual(satellite_channel.site_key("https://betterversus.com"), "comparison")
        self.assertEqual(satellite_channel.site_key("https://evil.example"), "")


@override_settings(SATELLITE_SITES=SITES, SATELLITE_INDEXNOW_KEY="")
class SatelliteChannelTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        self.pro_org.url = "https://brand-pro.com"
        self.pro_org.save()
        self.task = self.make_task(
            channel="satellite_content",
            tactic="satellite_article",
            target="ahrefs vs semrush",
            target_url="https://betterversus.com",
        )
        self.channel = satellite_channel.SatelliteContentChannel()

    def prepare(self, verdict=True, task=None):
        with (
            mock.patch.object(satellite_channel, "write_article", return_value=ARTICLE),
            mock.patch.object(satellite_channel, "gather_knowledge", return_value="facts"),
            mock.patch.object(satellite_channel, "check_article", return_value=SafetyVerdict(verdict, ())),
            mock.patch(f"{STORE}.slug_exists", return_value=False),
        ):
            return self.channel.prepare(task or self.task)

    def test_prepare_writes_a_draft_for_the_chosen_site_and_publishes_nothing(self):
        with mock.patch("apps.analyzer.services.satellite_publish.publish_post") as publish:
            prepared = self.prepare()
        publish.assert_not_called()
        self.assertEqual(prepared.kind, GrowthAction.Kind.PUBLISH_ARTICLE)
        self.assertEqual(prepared.connector, "satellite")
        self.assertEqual(prepared.payload["site"], "comparison")
        self.assertEqual(prepared.payload["slug"], "how-to-get-cited-by-chatgpt")
        self.assertEqual(
            prepared.destination,
            {"platform": "satellite", "site": "comparison", "site_url": SITES["comparison"]},
        )

    def test_a_task_not_pointing_at_our_sites_is_refused(self):
        task = self.make_task(
            channel="satellite_content", tactic="satellite_article", target_url="https://evil.example"
        )
        outcome = self.prepare(task=task)
        self.assertFalse(outcome.succeeded)
        self.assertFalse(outcome.retryable)

    def test_a_draft_failing_the_safety_check_is_blocked(self):
        self.assertFalse(self.prepare(verdict=False).succeeded)

    def version(self, prepared):
        return SimpleNamespace(payload=prepared.payload, destination=prepared.destination)

    def execute(self, prepared, existing=None, configured=True):
        with (
            mock.patch(f"{STORE}.is_configured", return_value=configured),
            mock.patch(f"{STORE}.get_post", return_value=existing),
            mock.patch(f"{STORE}.put_post") as put,
            mock.patch(f"{STORE}.new_id", return_value=1),
        ):
            outcome = self.channel.execute(self.task, self.version(prepared))
        return outcome, put

    def test_execute_publishes_the_approved_article_under_its_slug(self):
        outcome, put = self.execute(self.prepare())
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.evidence_url, "https://betterversus.com/how-to-get-cited-by-chatgpt")
        post = put.call_args.args[0]
        self.assertEqual(
            (post["site"], post["slug"], post["source"]),
            ("comparison", "how-to-get-cited-by-chatgpt", "growth_agent"),
        )
        self.assertIn('rel="sponsored noopener"', post["content_html"])
        self.assertEqual(post["brand_ref"], f"org:{self.pro_org.id}")
        self.assertEqual(outcome.payload["bing"], "not_sent")  # no satellite IndexNow key set

    @override_settings(SATELLITE_INDEXNOW_KEY="sat-key")
    def test_with_a_satellite_key_the_new_page_is_sent_to_bing(self):
        accepted = SubmissionResult(1, 200, True, "Accepted.")
        with mock.patch.object(satellite_channel.indexnow, "submit", return_value=accepted) as submit:
            outcome, _ = self.execute(self.prepare())
        submit.assert_called_once_with(SITES["comparison"], [outcome.evidence_url], key="sat-key")
        self.assertEqual(outcome.payload["bing"], "sent")

    def test_a_retry_after_publishing_does_not_publish_twice(self):
        existing = {"brand_ref": f"org:{self.pro_org.id}"}
        outcome, put = self.execute(self.prepare(), existing=existing)
        self.assertTrue(outcome.succeeded)
        put.assert_not_called()

    def test_a_slug_taken_by_another_brand_is_refused_without_publishing(self):
        outcome, put = self.execute(self.prepare(), existing={"brand_ref": "org:999"})
        self.assertFalse(outcome.succeeded)
        self.assertFalse(outcome.external_call_made)
        put.assert_not_called()

    def test_nothing_is_published_while_the_store_is_not_set_up(self):
        outcome, put = self.execute(self.prepare(), configured=False)
        self.assertFalse(outcome.succeeded)
        put.assert_not_called()

    def test_the_executor_turns_a_due_topic_into_a_draft_awaiting_approval(self):
        with (
            mock.patch(f"{STORE}.is_configured", return_value=True),
            mock.patch.object(satellite_channel, "write_article", return_value=ARTICLE),
            mock.patch.object(satellite_channel, "gather_knowledge", return_value="facts"),
            mock.patch.object(satellite_channel, "check_article", return_value=SafetyVerdict(True, ())),
            mock.patch(f"{STORE}.slug_exists", return_value=False),
        ):
            executor.run_task(self.task.id)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        action = GrowthAction.objects.get(task=self.task)
        shown = actions.present(action, action.current_version)
        self.assertEqual(shown["site_url"], SITES["comparison"])
        self.assertEqual(shown["title"], ARTICLE.title)


@override_settings(SATELLITE_SITES=SITES, SATELLITE_INDEXNOW_KEY="")
class SatelliteRefreshTests(ExecutionTestCase):
    URL = "https://betterversus.com/how-to-get-cited-by-chatgpt"

    def setUp(self):
        super().setUp()
        self.pro_org.url = "https://brand-pro.com"
        self.pro_org.save()
        self.task = self.make_task(
            channel="satellite_content",
            tactic="satellite_refresh",
            target="get cited by ChatGPT",
            target_url=self.URL,
        )
        self.channel = satellite_channel.SatelliteContentChannel()
        self.ours = {
            "brand_ref": f"org:{self.pro_org.id}",
            "content_html": "<p>Old text about citations.</p>",
        }

    def prepare(self, current):
        with (
            mock.patch(f"{STORE}.get_post", return_value=current),
            mock.patch.object(satellite_channel, "write_article", return_value=ARTICLE) as write,
            mock.patch.object(satellite_channel, "gather_knowledge", return_value="facts"),
            mock.patch.object(
                satellite_channel, "check_article", return_value=SafetyVerdict(True, ())
            ) as check,
        ):
            return self.channel.prepare(self.task), write, check

    def test_a_refresh_rewrites_the_live_article_at_the_same_address(self):
        prepared, write, check = self.prepare(self.ours)
        self.assertEqual(
            (prepared.payload["mode"], prepared.payload["slug"]), ("refresh", "how-to-get-cited-by-chatgpt")
        )
        knowledge = write.call_args.args[1]
        self.assertIn("CURRENT ARTICLE:", knowledge)
        self.assertIn("Old text about citations.", knowledge)
        self.assertNotIn("<p>", knowledge)
        self.assertEqual(check.call_args.args[2], knowledge)  # the safety check sees the same text

    def test_a_refresh_of_an_article_that_isnt_ours_is_refused(self):
        prepared, _, _ = self.prepare({"brand_ref": "org:999"})
        self.assertFalse(prepared.succeeded)
        prepared, _, _ = self.prepare(None)
        self.assertFalse(prepared.succeeded)

    def test_executing_a_refresh_updates_the_post_in_place_with_a_sponsored_link(self):
        prepared, _, _ = self.prepare(self.ours)
        version = SimpleNamespace(payload=prepared.payload, destination=prepared.destination)
        with (
            mock.patch(f"{STORE}.is_configured", return_value=True),
            mock.patch(f"{STORE}.get_post", return_value=self.ours),
            mock.patch(
                f"{STORE}.update_post", return_value={"slug": "how-to-get-cited-by-chatgpt"}
            ) as update,
            mock.patch(f"{STORE}.put_post") as put,
        ):
            outcome = self.channel.execute(self.task, version)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.evidence_url, self.URL)
        put.assert_not_called()  # never a second post
        site, slug, fields = update.call_args.args
        self.assertEqual((site, slug), ("comparison", "how-to-get-cited-by-chatgpt"))
        self.assertIn('rel="sponsored noopener"', fields["content_html"])
