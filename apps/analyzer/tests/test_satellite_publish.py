"""
Publishing one post to one satellite site (shared by the publish-to-network
endpoint and the Growth Agent). The blog store is faked.

Run: python manage.py test apps.analyzer.tests.test_satellite_publish --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.analyzer.services import satellite_publish

SITES = {"comparison": "https://betterversus.com/", "research": "https://brightsfindings.com"}
STORE = "apps.analyzer.blog_store"


@override_settings(SATELLITE_SITES=SITES)
class SatellitePublishTests(SimpleTestCase):
    def test_a_taken_slug_gets_the_next_free_number(self):
        taken = {"guide", "guide-2"}
        with mock.patch(f"{STORE}.slug_exists", side_effect=lambda site, slug: slug in taken):
            self.assertEqual(satellite_publish.unique_slug("comparison", "guide"), "guide-3")

    def test_publish_stores_the_post_under_its_slug_and_returns_the_live_url(self):
        with mock.patch(f"{STORE}.put_post") as put, mock.patch(f"{STORE}.new_id", return_value=7):
            post = satellite_publish.publish_post(
                site="comparison",
                slug="a-vs-b",
                title="A vs B",
                content_html="<p>x</p>",
                brand_url="https://brand.com",
                brand_ref="org:1",
                source="growth_agent",
            )
        stored = put.call_args.args[0]
        self.assertEqual(
            (stored["slug"], stored["status"], stored["source"]), ("a-vs-b", "published", "growth_agent")
        )
        self.assertEqual(post["url"], "https://betterversus.com/a-vs-b")

    def test_an_unknown_site_is_refused_before_anything_is_stored(self):
        with mock.patch(f"{STORE}.put_post") as put:
            with self.assertRaises(ValueError):
                satellite_publish.publish_post(
                    site="evil", slug="x", title="x", content_html="", brand_url="", brand_ref=""
                )
        put.assert_not_called()
