"""better-auth JWT authentication (JWKS-verified) for the main app API.

The frontend runs better-auth and, with its JWT plugin enabled, sends
``Authorization: Bearer <jwt>``. This class verifies that token against better-auth's
published public keys (its ``/api/auth/jwks`` endpoint) and, on success, attaches a
verified principal to ``request.user`` carrying the caller's email.

Design goals:
- **Non-breaking / dormant.** If ``BETTER_AUTH_JWKS_URL`` is unset, or no Bearer token
  is present, or the token is anything other than a valid better-auth JWT, this returns
  ``None`` — DRF then falls through to the existing (anonymous) behavior. It NEVER raises,
  so wiring it into ``DEFAULT_AUTHENTICATION_CLASSES`` changes nothing until the FE starts
  sending tokens. Enforcement is a separate, explicit step (see ``accounts.identity`` and
  the ``REQUIRE_VERIFIED_IDENTITY`` flag).
- **No shared secret.** Verification uses better-auth's asymmetric public keys (EdDSA by
  default), fetched and cached from the JWKS endpoint via PyJWT's ``PyJWKClient``.
- **Coexists with the Bearer API key.** ``sk_live_`` keys (apps/public_api) are not JWTs;
  a non-JWT bearer simply returns ``None`` here and is handled by that app's own auth.
"""

from __future__ import annotations

import logging

import jwt
from django.conf import settings
from jwt import PyJWKClient
from rest_framework import authentication

logger = logging.getLogger("apps")

# One PyJWKClient per JWKS URL (it caches keys in-process with a lifespan). Lazily
# created so an unconfigured deployment never touches the network.
_jwks_clients: dict[str, PyJWKClient] = {}


class VerifiedUser:
    """A cryptographically verified principal. DRF-authenticated, email-scoped.

    Mirrors public_api.PublicApiUser: DRF needs ``is_authenticated`` truthy, and the real
    authorization key is the verified ``email`` (never a client-supplied value).
    """

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False

    def __init__(self, *, email: str, subject: str = "", claims: dict | None = None):
        self.email = (email or "").lower().strip()
        self.subject = subject or ""
        self.claims = claims or {}
        # DRF UserRateThrottle keys on .pk — give each user their own bucket.
        self.pk = f"user:{self.email}"
        self.id = self.pk

    def __str__(self):
        return f"VerifiedUser({self.email})"


def _client(url: str) -> PyJWKClient:
    client = _jwks_clients.get(url)
    if client is None:
        # lifespan: how long cached keys are trusted before refetch. timeout: bound the
        # network call so a slow better-auth can't hang a request thread.
        client = PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=5)
        _jwks_clients[url] = client
    return client


def _looks_like_jwt(token: str) -> bool:
    # A compact JWS has exactly three dot-separated, non-empty segments. This lets an
    # ``sk_live_`` API key (or any non-JWT bearer) fall through to `None` instead of
    # being treated as a malformed token.
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


class BetterAuthJWTAuthentication(authentication.BaseAuthentication):
    keyword = "bearer"

    def authenticate(self, request):
        jwks_url = getattr(settings, "BETTER_AUTH_JWKS_URL", "") or ""
        if not jwks_url:
            return None  # dormant until configured

        header = authentication.get_authorization_header(request).split()
        if len(header) != 2 or header[0].lower() != self.keyword.encode():
            return None
        token = header[1].decode("utf-8", errors="ignore")
        if not _looks_like_jwt(token):
            return None  # e.g. an sk_live_ API key — not ours to verify

        try:
            signing_key = _client(jwks_url).get_signing_key_from_jwt(token)
            decode_kwargs: dict = {
                "algorithms": getattr(settings, "BETTER_AUTH_JWT_ALGORITHMS", ["EdDSA"]),
                "leeway": 30,  # tolerate minor clock skew
            }
            # Only enforce iss/aud when configured, so setup isn't a chicken-and-egg.
            if getattr(settings, "BETTER_AUTH_ISSUER", ""):
                decode_kwargs["issuer"] = settings.BETTER_AUTH_ISSUER
            if getattr(settings, "BETTER_AUTH_AUDIENCE", ""):
                decode_kwargs["audience"] = settings.BETTER_AUTH_AUDIENCE
            claims = jwt.decode(token, signing_key.key, **decode_kwargs)
        except Exception as exc:
            # Invalid/expired/unfetchable → not authenticated. Never raise: an invalid
            # token becomes an anonymous request, which the permission layer then handles.
            logger.debug("better-auth JWT verification failed: %s", exc)
            return None

        email_claim = getattr(settings, "BETTER_AUTH_EMAIL_CLAIM", "email")
        email = claims.get(email_claim) or ""
        if not email:
            logger.warning("better-auth JWT verified but has no %r claim", email_claim)
            return None

        user = VerifiedUser(email=email, subject=str(claims.get("sub", "")), claims=claims)
        return (user, token)
