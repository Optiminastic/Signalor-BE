"""
Bearer-token authentication for the public API.

External apps (Webflow, Framer, custom integrations) call
``/api/v1/public/...`` with ``Authorization: Bearer sk_live_...``. The token
hashes to an ApiKey row, which carries the Organization context.

The "user" attached to the request is a stand-in PublicApiUser — we don't
have a real Django user for these requests (keys are org-scoped per
product decision), and DRF needs ``request.user.is_authenticated`` to be
truthy for the throttling layer to key per-user. The real authorization
lives on ``request.auth`` (the ApiKey) and ``request.auth.organization``.
"""

from __future__ import annotations

from rest_framework import authentication, exceptions

from .models import ApiKey


class PublicApiUser:
    """Anonymous-ish principal that DRF treats as authenticated.

    Carries the api_key / organization so views can pull them off
    request.user without going through request.auth.
    """

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False

    def __init__(self, api_key: ApiKey):
        self.api_key = api_key
        self.organization = api_key.organization
        # DRF UserRateThrottle uses .pk for the cache key; pin it to the
        # api key id so each key gets its own bucket.
        self.pk = f"api_key:{api_key.pk}"
        self.id = self.pk

    def __str__(self):
        return f"PublicApiUser(org={self.organization_id_safe})"

    @property
    def organization_id_safe(self):
        return getattr(self.organization, "pk", None)


class BearerTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) == 1:
            raise exceptions.AuthenticationFailed("Invalid bearer header: no token.")
        if len(header) > 2:
            raise exceptions.AuthenticationFailed("Invalid bearer header: token contains spaces.")
        try:
            token = header[1].decode()
        except UnicodeError as exc:
            raise exceptions.AuthenticationFailed("Invalid bearer header: token is not valid utf-8.") from exc

        api_key = ApiKey.authenticate(token)
        if api_key is None:
            raise exceptions.AuthenticationFailed("Invalid or revoked API key.")

        # Defer the DB write for last_used_at — touching every request is
        # cheap but synchronous. Lazy-bumping at end of view is enough.
        request._public_api_key = api_key

        user = PublicApiUser(api_key)
        return (user, api_key)

    def authenticate_header(self, request):
        return self.keyword
