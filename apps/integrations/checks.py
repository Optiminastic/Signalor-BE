"""Deploy-time validation of the Google OAuth redirect URIs.

A wrong ``redirect_uri`` is invisible until a user clicks "Connect" and Google
rejects the consent with ``Error 400: redirect_uri_mismatch``. These Django
system checks fail the deploy instead — ``manage.py migrate`` (and ``check`` /
``runserver``) run them before the app serves traffic.

The two flows use different callbacks on purpose:
  * Google Analytics redirects to the **frontend** page, which POSTs the code to
    the backend  → ``/settings/integrations/callback/google-analytics``.
  * Search Console redirects to the **backend**, which exchanges the code
    server-side → ``/api/integrations/google-search-console/callback/``.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, register

# The frontend route Google must land on for GA (see the FE page of the same path).
GA_EXPECTED_PATH = "/settings/integrations/callback/google-analytics"
# The backend endpoint Google redirects to for GSC (exact, trailing slash required
# by the Django route and by Google's byte-for-byte redirect_uri match).
GSC_EXPECTED_PATH = "/api/integrations/google-search-console/callback/"


def _redirect_error(name: str, value: str, expected_path: str, code: str) -> Error:
    return Error(
        f"{name} must be an absolute URL whose path is '{expected_path}'.",
        hint=(
            f"Got {value!r}. Set {name} to '<origin>{expected_path}' and register the "
            "exact same URL in Google Cloud → OAuth client → Authorized redirect URIs. "
            "Google matches byte-for-byte, including the trailing slash."
        ),
        id=code,
    )


def _path_matches(value: str, expected_path: str, *, exact: bool) -> bool:
    parsed = urlparse(value)
    if not (parsed.scheme and parsed.netloc):
        return False
    path = parsed.path if exact else parsed.path.rstrip("/")
    return path == expected_path


@register()
def check_google_redirect_uris(app_configs: object, **kwargs: object) -> list[Error]:
    """Assert the GA/GSC redirect URIs point at the endpoints that actually exist."""
    errors: list[Error] = []

    ga = (getattr(settings, "GOOGLE_ANALYTICS_REDIRECT_URI", "") or "").strip()
    if not _path_matches(ga, GA_EXPECTED_PATH, exact=False):
        errors.append(
            _redirect_error(
                "GOOGLE_ANALYTICS_REDIRECT_URI", ga, GA_EXPECTED_PATH, "integrations.E001"
            )
        )

    gsc = (getattr(settings, "GOOGLE_SEARCH_CONSOLE_REDIRECT_URI", "") or "").strip()
    if not _path_matches(gsc, GSC_EXPECTED_PATH, exact=True):
        errors.append(
            _redirect_error(
                "GOOGLE_SEARCH_CONSOLE_REDIRECT_URI", gsc, GSC_EXPECTED_PATH, "integrations.E002"
            )
        )

    return errors
