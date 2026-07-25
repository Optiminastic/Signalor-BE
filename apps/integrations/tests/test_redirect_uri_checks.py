"""Regression: catch a bad Google OAuth redirect_uri at deploy, not at click.

The GA client had `.../api/auth/callback/google-analytics` registered while the
app uses `.../settings/integrations/callback/google-analytics`, so every Connect
died with `Error 400: redirect_uri_mismatch`. These tests lock the deploy-time
system check that would have caught it.
"""

from django.test import SimpleTestCase, override_settings

from apps.integrations.checks import check_google_redirect_uris

_GOOD_GA = "https://signalor.ai/settings/integrations/callback/google-analytics"
_GOOD_GSC = "https://api.signalor.ai/api/integrations/google-search-console/callback/"


def _ids(errors: list) -> set[str]:
    return {e.id for e in errors}


class GoogleRedirectUriCheckTests(SimpleTestCase):
    @override_settings(
        GOOGLE_ANALYTICS_REDIRECT_URI=_GOOD_GA,
        GOOGLE_SEARCH_CONSOLE_REDIRECT_URI=_GOOD_GSC,
    )
    def test_correct_uris_pass(self):
        self.assertEqual(check_google_redirect_uris(None), [])

    @override_settings(
        GOOGLE_ANALYTICS_REDIRECT_URI="https://signalor.ai/api/auth/callback/google-analytics",
        GOOGLE_SEARCH_CONSOLE_REDIRECT_URI=_GOOD_GSC,
    )
    def test_ga_wrong_path_flagged(self):
        self.assertIn("integrations.E001", _ids(check_google_redirect_uris(None)))

    @override_settings(
        GOOGLE_ANALYTICS_REDIRECT_URI=_GOOD_GA,
        # Missing the required trailing slash → Google's byte match fails.
        GOOGLE_SEARCH_CONSOLE_REDIRECT_URI="https://api.signalor.ai/api/integrations/google-search-console/callback",
    )
    def test_gsc_missing_trailing_slash_flagged(self):
        self.assertIn("integrations.E002", _ids(check_google_redirect_uris(None)))

    @override_settings(
        GOOGLE_ANALYTICS_REDIRECT_URI="/settings/integrations/callback/google-analytics",
        GOOGLE_SEARCH_CONSOLE_REDIRECT_URI="",
    )
    def test_relative_or_empty_flagged(self):
        self.assertEqual(
            _ids(check_google_redirect_uris(None)), {"integrations.E001", "integrations.E002"}
        )

    @override_settings(
        # A trailing slash on GA is tolerated (the FE route resolves either way).
        GOOGLE_ANALYTICS_REDIRECT_URI=_GOOD_GA + "/",
        GOOGLE_SEARCH_CONSOLE_REDIRECT_URI=_GOOD_GSC,
    )
    def test_ga_trailing_slash_tolerated(self):
        self.assertEqual(check_google_redirect_uris(None), [])
