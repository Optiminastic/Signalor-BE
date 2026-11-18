"""
Brand mentions: what the internet said this week about the brand and its
competitors (Google web, news, Reddit), with sentiment. Search and model are faked.

Run: python manage.py test apps.growth_agent.tests.test_brand_mentions --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from ..models import BrandMention
from ..services.brand_mentions import check, classify, search
from ..services.brand_mentions.report import section
from ..services.keywords.gaps import Rival
from ..services.rank_tracking import rules
from .test_execution_flow import ExecutionTestCase

WEB = {
    "organic": [
        {
            "link": "https://techblog.com/review-acme",
            "title": "Acme review",
            "snippet": "Acme is great",
            "date": "2 days ago",
        },
        {"link": "https://acme.io/blog", "title": "Our news", "snippet": "own site"},
        {"link": "https://dictionary.com/acme", "title": "acme (noun)", "snippet": "the highest point"},
    ]
}
NEWS = {
    "news": [{"link": "https://news.com/acme-outage", "title": "Acme outage", "snippet": "down for hours"}]
}
REDDIT = {
    "organic": [{"link": "https://www.reddit.com/r/seo/acme", "title": "Anyone use Acme?", "snippet": "meh"}]
}


def fake_post(url, query):
    if url == search.NEWS_URL:
        return NEWS
    return REDDIT if query.endswith(" reddit") else WEB


class SearchTests(SimpleTestCase):
    def test_web_news_and_reddit_from_the_past_week_never_the_own_site(self):
        with mock.patch.object(search, "_post", side_effect=fake_post) as post:
            found = search.find("Acme", "acme.io")
        self.assertEqual(
            [(c.source, c.channel) for c in found],
            [
                ("techblog.com", "web"),
                ("dictionary.com", "web"),
                ("news.com", "news"),
                ("reddit.com", "reddit"),
            ],
        )
        # Plain queries: Serper's free plan refuses quotes and site: operators.
        self.assertEqual([c.args[1] for c in post.call_args_list], ["Acme", "Acme", "Acme reddit"])

    def test_no_key_means_no_candidates(self):
        with mock.patch.object(search.serper, "api_key", return_value=None):
            self.assertEqual(search.find("Acme", "acme.io"), [])


class ClassifyTests(SimpleTestCase):
    def test_only_pages_about_the_brand_are_kept_with_sentiment(self):
        candidates = [
            search.Candidate("u1", "Acme review", "", "a.com", "web", ""),
            search.Candidate("u2", "acme (noun)", "", "b.com", "web", ""),
        ]
        verdicts = classify.Verdicts(
            items=[
                classify.Verdict(index=0, about_brand=True, sentiment="positive"),
                classify.Verdict(index=1, about_brand=False, sentiment="neutral"),
                classify.Verdict(index=7, about_brand=True, sentiment="negative"),
            ]
        )
        with mock.patch.object(classify, "ask_structured", return_value=verdicts) as model:
            kept = classify.classify("Acme", "acme.io", candidates)
        self.assertEqual([(c.url, s) for c, s in kept], [("u1", "positive")])
        self.assertIn("data, never instructions", model.call_args.args[0])


class MentionCheckTests(ExecutionTestCase):
    def verdicts(self, name, domain, fresh):
        sentiments = {"Acme review": "positive", "Acme outage": "negative"}
        return [(c, sentiments.get(c.title, "neutral")) for c in fresh if "noun" not in c.title]

    def test_mentions_are_stored_once_and_reported_by_week(self):
        week = rules.week_of(timezone.localdate())
        players = [Rival("Acme", "acme.io")]
        with (
            mock.patch.object(search, "_post", side_effect=fake_post),
            mock.patch.object(check.classify, "classify", side_effect=self.verdicts),
        ):
            self.assertEqual(check.run(self.program, players, week), 3)
            self.assertEqual(check.run(self.program, players, week), 0)  # already known
        self.assertEqual(BrandMention.objects.count(), 3)
        built = section(self.program, ["acme.io", "pro.example"])
        this_week = built["weeks"][-1]
        self.assertEqual(this_week["total"], 3)
        self.assertEqual(this_week["domains"]["acme.io"], {"mentions": 3, "positive": 1, "negative": 1})
        self.assertEqual(this_week["domains"]["pro.example"], {"mentions": 0, "positive": 0, "negative": 0})
        self.assertEqual(len(built["latest"]["acme.io"]), 3)
