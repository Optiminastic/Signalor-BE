"""The satellite blog store must degrade on reads and refuse on writes.

Unset credentials left ``bucket`` as an empty string, and nothing checked before
calling S3 — so boto3 raised ``ParamValidationError: Invalid bucket name ""``
*inside* the call. In production that printed a full traceback on every
backlinks page load, from a deployment that had simply never configured a bucket.

Reads and writes need opposite treatment. "No bucket" and "no posts yet" are the
same answer to a reader. To a writer they are not: a post the caller believes it
published and a reader can never find is the same class of lie as reporting an
IndexNow submission as an index.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer import blog_store

UNSET = {
    "BACKLINKS_BLOG_AWS_ACCESS_KEY_ID": "",
    "BACKLINKS_BLOG_AWS_SECRET_ACCESS_KEY": "",
    "BACKLINKS_BLOG_AWS_BUCKET": "",
}
SET = {
    "BACKLINKS_BLOG_AWS_ACCESS_KEY_ID": "AKIA_test",
    "BACKLINKS_BLOG_AWS_SECRET_ACCESS_KEY": "secret_test",
    "BACKLINKS_BLOG_AWS_BUCKET": "signalor-test",
}


class IsConfiguredTests(SimpleTestCase):
    def test_all_three_present_is_configured(self):
        with patch.dict("os.environ", SET, clear=False):
            self.assertTrue(blog_store.is_configured())

    def test_nothing_set_is_not_configured(self):
        with patch.dict("os.environ", UNSET, clear=False):
            self.assertFalse(blog_store.is_configured())

    def test_a_missing_bucket_alone_is_not_configured(self):
        """The exact production state: credentials present, bucket empty."""
        env = {**SET, "BACKLINKS_BLOG_AWS_BUCKET": ""}
        with patch.dict("os.environ", env, clear=False):
            self.assertFalse(blog_store.is_configured())

    def test_missing_credentials_alone_is_not_configured(self):
        env = {**SET, "BACKLINKS_BLOG_AWS_SECRET_ACCESS_KEY": ""}
        with patch.dict("os.environ", env, clear=False):
            self.assertFalse(blog_store.is_configured())


class ReadsDegradeTests(SimpleTestCase):
    """No bucket means no posts — which is what an empty list says."""

    def test_list_index_returns_empty_without_calling_s3(self):
        with patch.dict("os.environ", UNSET, clear=False):
            with patch.object(blog_store, "_client", side_effect=AssertionError("must not call S3")):
                self.assertEqual(blog_store.list_index("research"), [])

    def test_get_post_returns_none_without_calling_s3(self):
        with patch.dict("os.environ", UNSET, clear=False):
            with patch.object(blog_store, "_client", side_effect=AssertionError("must not call S3")):
                self.assertIsNone(blog_store.get_post("research", "a-slug"))

    def test_list_for_brand_is_empty_across_every_site(self):
        """The regression: this looped 5 sites and raised on the first."""
        with patch.dict("os.environ", UNSET, clear=False):
            with patch.object(blog_store, "_client", side_effect=AssertionError("must not call S3")):
                self.assertEqual(blog_store.list_for_brand("brand-1"), [])

    def test_slug_exists_is_false_rather_than_raising(self):
        with patch.dict("os.environ", UNSET, clear=False):
            self.assertFalse(blog_store.slug_exists("research", "a-slug"))


class WritesRefuseTests(SimpleTestCase):
    """A silent write failure would report a post as published that does not exist."""

    def test_put_post_refuses_with_an_actionable_message(self):
        with patch.dict("os.environ", UNSET, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                blog_store.put_post({"site": "research", "slug": "s", "title": "t"})
        # The message must name the variables to set, not just say "not configured".
        self.assertIn("BACKLINKS_BLOG_AWS_BUCKET", str(ctx.exception))

    def test_put_post_never_reaches_s3_unconfigured(self):
        with patch.dict("os.environ", UNSET, clear=False):
            with patch.object(blog_store, "_client", side_effect=AssertionError("must not call S3")):
                with self.assertRaises(RuntimeError):
                    blog_store.put_post({"site": "research", "slug": "s"})

    def test_delete_post_refuses_too(self):
        with patch.dict("os.environ", UNSET, clear=False):
            with self.assertRaises(RuntimeError):
                blog_store.delete_post("research", "s")

    def test_update_post_is_a_no_op_because_the_read_finds_nothing(self):
        """Reads degrade, so update short-circuits before it can attempt a write."""
        with patch.dict("os.environ", UNSET, clear=False):
            self.assertIsNone(blog_store.update_post("research", "s", {"title": "x"}))


class AutoCanAddTodayTests(SimpleTestCase):
    """The exact production traceback, now a clean answer.

    ``auto_can_add_today`` swallowed the ParamValidationError and returned True,
    so behaviour was accidentally correct — but it logged a full traceback on
    every backlinks page load, which is how it was found.
    """

    def test_it_returns_true_without_raising_when_unconfigured(self):
        from types import SimpleNamespace

        from apps.analyzer.services.backlink_engine import auto_can_add_today

        with patch.dict("os.environ", UNSET, clear=False):
            with patch("apps.analyzer.views._brand_ref_for_run", return_value="brand-1"):
                self.assertTrue(auto_can_add_today(SimpleNamespace(slug="abc", id=1)))

    def test_it_no_longer_logs_an_exception(self):
        """The traceback was the actual symptom; assert it is gone."""
        from types import SimpleNamespace

        from apps.analyzer.services import backlink_engine

        with patch.dict("os.environ", UNSET, clear=False):
            with patch("apps.analyzer.views._brand_ref_for_run", return_value="brand-1"):
                with patch.object(backlink_engine.logger, "exception") as logged:
                    backlink_engine.auto_can_add_today(SimpleNamespace(slug="abc", id=1))
        logged.assert_not_called()
