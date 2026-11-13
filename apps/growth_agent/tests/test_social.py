"""
Social presence (step 1): the score per platform and overall, the weekly check,
and the Social tab endpoint. YouTube and Reddit are faked.

Run: python manage.py test apps.growth_agent.tests.test_social --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.analyzer.models import AnalysisRun, BrandVisibility

from ..models import AgentActionLog
from ..services.chat import read_tools
from ..services.social import check, reddit, score, youtube
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase

STORED = {
    "linkedin": {"url": "https://www.linkedin.com/company/brand/", "source": "website", "followers": None},
    "twitter": {"url": "https://x.com/brand", "source": "website", "followers": None},
    "youtube": {"url": "https://www.youtube.com/@Brand", "source": "serper", "followers": 84},
    "instagram": {"url": "https://www.instagram.com/brand/", "source": "serper", "followers": None},
    "facebook": {"url": None, "source": "none", "followers": None},
}
DISPATCH = "apps.growth_agent.analysis_tasks.dispatch_social_check"


def reply(body, status=200):
    answer = mock.Mock(status_code=status, ok=status < 400)
    answer.json.return_value = body
    return answer


class ScoreTests(SimpleTestCase):
    def test_a_profile_linked_from_the_site_gets_full_presence_and_unknowns_stay_unmeasured(self):
        result = score.profile_score({"url": "https://x.com/b", "on_site": True})
        self.assertEqual(result["score"], 20.0)
        self.assertEqual(result["not_measured"], ["audience", "activity", "engagement"])

    def test_audience_and_activity_add_points_when_measured(self):
        result = score.profile_score({"url": "u", "on_site": False, "followers": 100_000, "posts_30d": 2})
        self.assertEqual(result["parts"], {"presence": 12.0, "audience": 30.0, "activity": 15.0})
        self.assertEqual(result["not_measured"], ["engagement"])

    def test_no_profile_scores_zero(self):
        self.assertEqual(score.profile_score({"url": ""})["score"], 0.0)

    def test_reddit_is_scored_on_mentions_and_communities(self):
        result = score.reddit_score({"mentions_90d": 7, "community_count": 3})
        self.assertEqual(result["parts"], {"mentions": 45.0, "communities": 24.0})
        self.assertEqual(score.reddit_score(None)["not_measured"], ["mentions"])

    def test_overall_averages_all_five_platforms(self):
        platforms = {
            "linkedin": {"score": 50.0, "not_measured": []},
            "youtube": {"score": 100.0, "not_measured": []},
        }
        self.assertEqual(score.overall(platforms), {"score": 30.0, "partial": False})


class YouTubeTests(SimpleTestCase):
    def test_channel_urls_are_looked_up_the_right_way(self):
        self.assertEqual(
            youtube.channel_query("https://www.youtube.com/@Signalor"), {"forHandle": "@Signalor"}
        )
        self.assertEqual(youtube.channel_query("https://www.youtube.com/channel/UC123"), {"id": "UC123"})
        self.assertEqual(youtube.channel_query("https://www.youtube.com/"), {})

    def test_stats_and_recent_uploads(self):
        recent = (timezone.now() - timedelta(days=3)).isoformat()
        old = (timezone.now() - timedelta(days=60)).isoformat()
        channel = {
            "items": [
                {
                    "statistics": {"subscriberCount": "84", "videoCount": "5", "viewCount": "900"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
                }
            ]
        }
        uploads = {
            "items": [
                {"contentDetails": {"videoPublishedAt": recent}},
                {"contentDetails": {"videoPublishedAt": old}},
            ]
        }
        with mock.patch.object(youtube.requests, "get", side_effect=[reply(channel), reply(uploads)]) as get:
            stats = youtube.channel_stats("https://www.youtube.com/@Signalor", "yt-key")
        self.assertEqual((stats["subscribers"], stats["videos"], stats["posts_30d"]), (84, 5, 1))
        self.assertEqual(get.call_args_list[0].kwargs["params"]["forHandle"], "@Signalor")


class RedditTests(SimpleTestCase):
    def test_only_mentions_in_the_last_90_days_count(self):
        now = timezone.now()
        posts = [
            {
                "subreddit": "SEO",
                "created_utc": (now - timedelta(days=5)).timestamp(),
                "title": "a",
                "permalink": "/r/SEO/1",
            },
            {
                "subreddit": "SEO",
                "created_utc": (now - timedelta(days=20)).timestamp(),
                "title": "b",
                "permalink": "/r/SEO/2",
            },
            {"subreddit": "marketing", "created_utc": (now - timedelta(days=200)).timestamp(), "title": "c"},
        ]
        summary = reddit.summarize(posts, now)
        self.assertEqual((summary["mentions_90d"], summary["community_count"]), (2, 1))
        self.assertEqual(summary["communities"], [{"name": "SEO", "mentions": 2}])
        self.assertEqual(summary["sample"][0]["url"], "https://www.reddit.com/r/SEO/1")


@override_settings(YOUTUBE_API_KEY="", REDDIT_CLIENT_ID="", REDDIT_CLIENT_SECRET="")
class SocialCheckTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        run = AnalysisRun.objects.create(
            url="https://brand-pro.com", organization=self.pro_org, email=PRO_EMAIL, status="complete"
        )
        BrandVisibility.objects.create(analysis_run=run, social_presence_details=STORED)

    def test_without_keys_the_score_uses_the_stored_profiles_and_says_what_is_missing(self):
        result = check.measure(self.program)
        self.assertEqual(result["platforms"]["linkedin"]["score"], 20.0)
        self.assertEqual(result["platforms"]["youtube"]["parts"]["audience"], score._audience(84))
        self.assertEqual(result["platforms"]["reddit"]["not_measured"], ["mentions"])
        self.assertTrue(result["overall"]["partial"])
        self.assertTrue(any("YouTube Data API key" in note for note in result["notes"]))
        self.assertTrue(any("Reddit API app" in note for note in result["notes"]))

    @override_settings(YOUTUBE_API_KEY="yt", REDDIT_CLIENT_ID="id", REDDIT_CLIENT_SECRET="secret")
    def test_with_keys_youtube_and_reddit_fill_in(self):
        stats = {"subscribers": 84, "videos": 5, "views": 900, "posts_30d": 4, "last_post": ""}
        mentions = {"mentions_90d": 3, "community_count": 2, "communities": [], "sample": []}
        with (
            mock.patch.object(youtube, "channel_stats", return_value=stats),
            mock.patch.object(reddit, "mentions", return_value=mentions),
        ):
            result = check.measure(self.program)
        self.assertEqual(result["platforms"]["youtube"]["parts"]["activity"], 30.0)
        self.assertTrue(result["platforms"]["reddit"]["found"])
        self.assertEqual(result["notes"], [])

    def test_the_report_shows_the_change_since_last_week(self):
        check.run_check(self.program)
        first = AgentActionLog.objects.get(action=check.CHECKED)
        AgentActionLog.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(days=7))
        with (
            override_settings(YOUTUBE_API_KEY="yt"),
            mock.patch.object(
                youtube,
                "channel_stats",
                return_value={"subscribers": 84, "videos": 5, "views": 1, "posts_30d": 4, "last_post": ""},
            ),
        ):
            check.run_check(self.program)
        report = check.report(self.program)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["change"], 6.0)  # +30 activity on YouTube, averaged over 5 platforms

    def test_weekly_queues_one_check_a_week(self):
        with mock.patch(DISPATCH) as dispatch, self.captureOnCommitCallbacks(execute=True):
            check.weekly(self.program)
            check.weekly(self.program)
        dispatch.assert_called_once_with(self.program.id)

    def test_endpoint_is_owner_only_and_check_now_waits_between_runs(self):
        path = f"/api/growth/programs/{self.pro_org.slug}/social/"
        self.assertEqual(self.signed("GET", path, PRO_EMAIL).json()["status"], "never_checked")
        self.assertIn(self.signed("GET", path, OTHER_EMAIL).status_code, (403, 404))
        self._body = {}
        with mock.patch(DISPATCH), self.captureOnCommitCallbacks(execute=True):
            first = self.signed("POST", f"{path}check/", PRO_EMAIL)
            second = self.signed("POST", f"{path}check/", PRO_EMAIL)
        self.assertEqual((first.status_code, second.status_code), (202, 409))

    def test_the_chat_agent_can_read_the_score(self):
        self.assertFalse(read_tools.get_social(self.program, {})["available"])
        check.run_check(self.program)
        result = read_tools.get_social(self.program, {})
        self.assertTrue(result["partial"])
        self.assertEqual(result["platforms"]["linkedin"]["score"], 20.0)
