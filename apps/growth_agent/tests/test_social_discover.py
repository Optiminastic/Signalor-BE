"""
Finding social profiles through Google search, for the brand and its competitors.

Run: python manage.py test apps.growth_agent.tests.test_social_discover --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase

from ..services.social import discover
from .test_execution_flow import ExecutionTestCase


def reply(*links_and_titles):
    return {"organic": [{"link": link, "title": title} for link, title in links_and_titles]}


class ProfileMatchTests(SimpleTestCase):
    def find(self, platform, results, name="The 3D Printing Store", domain="the3dprintingstore.in"):
        with mock.patch.object(discover.serper, "search", return_value=results):
            return discover.find_profile(name, domain, platform)

    def test_a_company_page_counts_but_a_post_does_not(self):
        results = reply(
            (
                "https://www.linkedin.com/posts/someone_3d-printing-store-activity-1",
                "The 3D Printing Store post",
            ),
            ("https://in.linkedin.com/company/the3dprintingstore", "The 3D Printing Store | LinkedIn"),
        )
        self.assertEqual(self.find("linkedin", results), "https://in.linkedin.com/company/the3dprintingstore")

    def test_handles_and_channels_count_but_search_pages_do_not(self):
        self.assertEqual(self.find("x", reply(("https://x.com/search?q=3d", "3D printing store"))), "")
        self.assertEqual(
            self.find(
                "x",
                reply(("https://twitter.com/the3dprintstore", "The 3D Printing Store (@the3dprintstore)")),
            ),
            "https://twitter.com/the3dprintstore",
        )
        self.assertEqual(
            self.find(
                "youtube", reply(("https://www.youtube.com/@the3dprintingstore", "The 3D Printing Store"))
            ),
            "https://www.youtube.com/@the3dprintingstore",
        )
        self.assertEqual(
            self.find("instagram", reply(("https://www.instagram.com/p/abc123/", "printing store"))), ""
        )

    def test_a_profile_of_some_other_brand_is_not_taken(self):
        results = reply(("https://www.linkedin.com/company/acme-widgets", "Acme Widgets | LinkedIn"))
        self.assertEqual(self.find("linkedin", results), "")

    def test_a_failed_search_finds_nothing(self):
        self.assertEqual(self.find("linkedin", None), "")

    def test_no_key_means_no_searches(self):
        with (
            mock.patch.object(discover.serper, "is_configured", return_value=False),
            mock.patch.object(discover.serper, "search") as search,
        ):
            self.assertEqual(discover.find_profiles("Brand", "brand.com"), {})
        search.assert_not_called()


class SocialCheckWithSearchTests(ExecutionTestCase):
    def test_search_fills_the_brand_and_lists_competitors(self):
        from ..services.keywords.gaps import Rival
        from ..services.social import check

        found = {
            "Rival Co": {"linkedin": "https://linkedin.com/company/rival-co", "x": "https://x.com/rivalco"},
        }

        def profiles(name, domain):
            return found.get(name, {"linkedin": "https://linkedin.com/company/brand"})

        rivals = [Rival("Brand", "brand.com"), Rival("Rival Co", "rivalco.com")]
        with (
            mock.patch.object(check.discover, "find_profiles", side_effect=profiles),
            mock.patch("apps.growth_agent.services.rank_tracking.check.competitors", return_value=rivals),
        ):
            result = check.measure(self.program)
        linkedin = result["platforms"]["linkedin"]["profile"]
        self.assertEqual(
            (linkedin["url"], linkedin["source"]), ("https://linkedin.com/company/brand", "search")
        )
        self.assertEqual(
            result["competitors"],
            [
                {"name": "Rival Co", "domain": "rivalco.com", "profiles": found["Rival Co"]},
            ],
        )
