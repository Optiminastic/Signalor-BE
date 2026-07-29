"""Tests for IndexNow submission.

Everything else in the product optimises what an engine finds once it looks.
This gets it to look: ChatGPT's live search reads Bing, and Bing cannot answer
from a page it has not indexed.

Two contracts:

1. **Never submit without verifying the key file.** An unverified submission
   returns 403 and burns quota, while still looking like a success to anything
   counting HTTP calls rather than outcomes.
2. **Accepted is not indexed.** A 200 means the engine took the request. Saying
   more than that would be the same class of lie as a self-reported outreach
   tracker.
"""

from unittest.mock import patch

from django.test import TestCase

from apps.analyzer.models import AnalysisRun
from apps.analyzer.services import indexnow
from apps.organizations.models import Organization


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class KeyTests(TestCase):
    def test_key_is_stable_for_an_org(self):
        self.assertEqual(indexnow.key_for_org(7), indexnow.key_for_org(7))

    def test_key_differs_between_orgs(self):
        self.assertNotEqual(indexnow.key_for_org(7), indexnow.key_for_org(8))

    def test_key_meets_the_indexnow_character_rules(self):
        key = indexnow.key_for_org(1)
        self.assertGreaterEqual(len(key), 8)
        self.assertLessEqual(len(key), 128)
        self.assertTrue(all(c in "0123456789abcdef" for c in key))

    def test_key_file_url_sits_at_the_host_root(self):
        self.assertEqual(
            indexnow.key_file_url("https://acme.com/some/page", "abc"), "https://acme.com/abc.txt"
        )

    def test_key_file_url_is_empty_without_a_host(self):
        self.assertEqual(indexnow.key_file_url("", "abc"), "")


class VerificationTests(TestCase):
    def test_matching_key_file_verifies(self):
        with patch.object(indexnow.requests, "get", return_value=_Resp(200, "abc\n")):
            ok, _ = indexnow.verify_key_file("https://acme.com", "abc")
        self.assertTrue(ok)

    def test_missing_key_file_fails_with_the_expected_location(self):
        with patch.object(indexnow.requests, "get", return_value=_Resp(404)):
            ok, message = indexnow.verify_key_file("https://acme.com", "abc")
        self.assertFalse(ok)
        self.assertIn("acme.com/abc.txt", message)

    def test_wrong_contents_fail(self):
        with patch.object(indexnow.requests, "get", return_value=_Resp(200, "different")):
            ok, _ = indexnow.verify_key_file("https://acme.com", "abc")
        self.assertFalse(ok)

    def test_network_failure_is_reported_not_raised(self):
        with patch.object(
            indexnow.requests, "get", side_effect=indexnow.requests.RequestException("timeout")
        ):
            ok, message = indexnow.verify_key_file("https://acme.com", "abc")
        self.assertFalse(ok)
        self.assertIn("timeout", message)


class SubmissionTests(TestCase):
    def _submit(self, urls, *, verified=True, status=200):
        with patch.object(
            indexnow, "verify_key_file", return_value=(verified, "msg")
        ), patch.object(indexnow.requests, "post", return_value=_Resp(status)) as post:
            result = indexnow.submit("https://acme.com", urls, key="abc")
        return result, post

    def test_a_valid_batch_is_submitted(self):
        result, post = self._submit(["https://acme.com/a", "https://acme.com/b"])
        self.assertTrue(result.ok)
        self.assertEqual(result.submitted, 2)
        self.assertEqual(post.call_args.kwargs["json"]["host"], "acme.com")

    def test_an_unverified_key_never_submits(self):
        """A 403 burns quota and still looks like a sent request."""
        with patch.object(indexnow, "verify_key_file", return_value=(False, "no key file")), patch.object(
            indexnow.requests, "post", side_effect=AssertionError("should not submit")
        ):
            result = indexnow.submit("https://acme.com", ["https://acme.com/a"], key="abc")
        self.assertFalse(result.ok)
        self.assertEqual(result.submitted, 0)

    def test_urls_from_another_host_are_filtered_out(self):
        """One stray URL would otherwise fail the whole batch."""
        result, post = self._submit(["https://acme.com/a", "https://other.com/b"])
        self.assertEqual(post.call_args.kwargs["json"]["urlList"], ["https://acme.com/a"])
        self.assertEqual(result.submitted, 1)

    def test_duplicates_are_collapsed(self):
        result, _ = self._submit(["https://acme.com/a", "https://acme.com/a"])
        self.assertEqual(result.submitted, 1)

    def test_non_http_urls_are_rejected(self):
        result, _ = self._submit(["ftp://acme.com/a", "https://acme.com/a"])
        self.assertEqual(result.submitted, 1)

    def test_empty_list_does_not_call_the_api(self):
        with patch.object(indexnow.requests, "post", side_effect=AssertionError("should not submit")):
            result = indexnow.submit("https://acme.com", [], key="abc")
        self.assertFalse(result.ok)

    def test_batch_is_capped(self):
        urls = [f"https://acme.com/{i}" for i in range(indexnow.MAX_URLS + 50)]
        result, _ = self._submit(urls)
        self.assertEqual(result.submitted, indexnow.MAX_URLS)

    def test_202_counts_as_accepted(self):
        result, _ = self._submit(["https://acme.com/a"], status=202)
        self.assertTrue(result.ok)

    def test_403_is_reported_with_a_readable_reason(self):
        result, _ = self._submit(["https://acme.com/a"], status=403)
        self.assertFalse(result.ok)
        self.assertIn("Key not valid", result.message)

    def test_accepted_does_not_claim_indexing(self):
        """Submitted is not indexed, and the wording must not imply otherwise."""
        result, _ = self._submit(["https://acme.com/a"])
        self.assertNotIn("indexed", result.message.lower())

    def test_a_network_failure_is_not_an_exception(self):
        with patch.object(indexnow, "verify_key_file", return_value=(True, "")), patch.object(
            indexnow.requests, "post", side_effect=indexnow.requests.RequestException("down")
        ):
            result = indexnow.submit("https://acme.com", ["https://acme.com/a"], key="abc")
        self.assertFalse(result.ok)


class EndpointTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Acme", owner_email="o@acme.com")
        self.run = AnalysisRun.objects.create(url="https://acme.com", organization=org)

    def _url(self):
        from django.urls import reverse

        return reverse("analyzer:indexnow", args=[self.run.slug])

    def test_get_returns_setup_instructions(self):
        with patch.object(indexnow, "verify_key_file", return_value=(False, "not hosted")):
            resp = self.client.get(self._url())
        body = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(body["key"])
        self.assertIn(".txt", body["key_file_url"])
        self.assertFalse(body["verified"])

    def test_post_submits_the_runs_pages(self):
        with patch.object(indexnow, "submit_run_pages", return_value={"ok": True, "submitted": 3}):
            resp = self.client.post(self._url(), data={}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_a_run_without_an_organization_is_handled(self):
        from django.urls import reverse

        orphan = AnalysisRun.objects.create(url="https://x.com")
        resp = self.client.get(reverse("analyzer:indexnow", args=[orphan.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["configured"])
