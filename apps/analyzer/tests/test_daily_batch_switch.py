"""
The old daily 5-a-day satellite batch is switched off (replaced by the Growth
Agent's approved satellite articles), and client links on the satellite sites
are always rel="sponsored". The blog store and the writer model are faked.

Run: python manage.py test apps.analyzer.tests.test_daily_batch_switch --settings=config.settings.test
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.analyzer.models import AnalysisRun
from apps.analyzer.services import backlink_engine
from apps.analyzer.services.satellite_publish import sponsor_client_links
from apps.organizations.models import Organization

STORE = "apps.analyzer.blog_store"


class SponsorLinkTests(SimpleTestCase):
    def test_client_links_become_sponsored_and_other_links_stay(self):
        html = '<a href="https://www.brand.com/p" rel="nofollow" class="x">B</a> <a href="https://other.com">O</a>'
        fixed = sponsor_client_links(html, "https://brand.com")
        self.assertIn('<a href="https://www.brand.com/p" class="x" rel="sponsored noopener">B</a>', fixed)
        self.assertIn('<a href="https://other.com">O</a>', fixed)

    def test_nothing_changes_without_a_client_url_or_links(self):
        self.assertEqual(sponsor_client_links("<p>hi</p>", "https://brand.com"), "<p>hi</p>")
        self.assertEqual(
            sponsor_client_links('<a href="https://brand.com">B</a>', ""), '<a href="https://brand.com">B</a>'
        )


@override_settings(SATELLITE_DAILY_BATCH_ENABLED=False, REQUIRE_VERIFIED_IDENTITY=False)
class SwitchedOffTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Brand", owner_email="o@brand.com", url="https://brand.com")
        self.run = AnalysisRun.objects.create(url="https://brand.com", organization=org, email="o@brand.com")

    def test_the_engine_publishes_nothing(self):
        with mock.patch(f"{STORE}.put_post") as put:
            result = backlink_engine.run_auto_backlinks(self.run)
        self.assertEqual(result["disabled"], True)
        put.assert_not_called()
        self.assertFalse(backlink_engine.auto_can_add_today(self.run))

    def test_the_add_button_explains_what_replaced_it(self):
        response = self.client.post(f"/api/analyzer/runs/s/{self.run.slug}/blog/auto-publish-all/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Agent", response.json()["error"])

    def test_the_schedule_reports_replaced_and_cannot_be_turned_back_on(self):
        path = f"/api/analyzer/runs/s/{self.run.slug}/backlinks/schedule/"
        body = self.client.get(path).json()
        self.assertEqual((body["is_active"], body["replaced_by_agent"]), (False, True))
        self.assertEqual(
            self.client.post(path, {"is_active": True}, content_type="application/json").status_code, 409
        )
        self.assertEqual(
            self.client.post(path, {"is_active": False}, content_type="application/json").status_code, 200
        )

    def test_the_scheduler_does_nothing(self):
        out = StringIO()
        with mock.patch.object(backlink_engine, "run_auto_backlinks") as run:
            call_command("run_backlink_schedules", stdout=out)
        run.assert_not_called()
        self.assertIn("switched off", out.getvalue())


class MarkSponsoredCommandTests(SimpleTestCase):
    POST = {
        "slug": "p",
        "brand_url": "https://brand.com",
        "content_html": '<a href="https://brand.com">B</a>',
    }

    def run_command(self, *args):
        out = StringIO()
        with (
            mock.patch(f"{STORE}.is_configured", return_value=True),
            mock.patch(
                f"{STORE}.list_index",
                side_effect=lambda site: [{"slug": "p"}] if site == "comparison" else [],
            ),
            mock.patch(f"{STORE}.get_post", return_value=dict(self.POST)),
            mock.patch(f"{STORE}.update_post") as update,
        ):
            call_command("mark_satellite_links_sponsored", *args, stdout=out)
        return out.getvalue(), update

    def test_dry_run_reports_and_changes_nothing(self):
        out, update = self.run_command()
        update.assert_not_called()
        self.assertIn("Would rewrite 1 of 1", out)

    def test_apply_rewrites_the_client_link(self):
        out, update = self.run_command("--apply")
        site, slug, fields = update.call_args.args
        self.assertEqual((site, slug), ("comparison", "p"))
        self.assertIn('rel="sponsored noopener"', fields["content_html"])
        self.assertIn("Rewrote 1 of 1", out)
