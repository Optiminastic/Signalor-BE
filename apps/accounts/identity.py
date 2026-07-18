"""Single source of truth for "who is calling" — the scoping seam.

Historically each view read a client-supplied ``?email=`` and trusted it, which means
anyone could act as anyone (see the security audit: 87 call sites). This module
centralizes identity resolution so the whole app can migrate to verified identity by
flipping one flag instead of editing every view.

Resolution order:
  1. A cryptographically **verified** email from ``request.user`` (set by
     ``accounts.authentication.BetterAuthJWTAuthentication`` when the FE sends a valid
     better-auth JWT). Always preferred.
  2. Otherwise, if enforcement is on (``require_verified`` or settings
     ``REQUIRE_VERIFIED_IDENTITY``), reject with 401 — no unverified fallback.
  3. Otherwise, the legacy ``?email=`` / body ``email`` (current behavior), so nothing
     breaks before the FE ships tokens.

Rollout: build verifier (done) → FE sends tokens → set ``REQUIRE_VERIFIED_IDENTITY=true``
→ step 3 disappears and the ``email`` input is dead. Migrate views to call this helper
instead of reading ``email`` directly; the two most dangerous endpoints are done as the
reference pattern.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from .authentication import VerifiedUser


def verified_email(request) -> str | None:
    """The verified caller email, or ``None`` if the request is not JWT-authenticated."""
    user = getattr(request, "user", None)
    if isinstance(user, VerifiedUser) and user.email:
        return user.email
    return None


def _legacy_email(request) -> str:
    raw = request.query_params.get("email") or (request.data.get("email") if hasattr(request, "data") else "")
    return (raw or "").lower().strip()


def _enforced(require_verified: bool | None) -> bool:
    if require_verified is not None:
        return require_verified
    from django.conf import settings

    return bool(getattr(settings, "REQUIRE_VERIFIED_IDENTITY", False))


def resolve_request_email(request, *, require_verified: bool | None = None) -> tuple[str | None, Response | None]:
    """Return ``(email, error_response)``. Callers do ``email, err = ...; if err: return err``.

    ``require_verified`` overrides the global ``REQUIRE_VERIFIED_IDENTITY`` flag for a
    specific endpoint (e.g. always-True for destructive actions once the FE is ready).
    """
    email = verified_email(request)
    if email:
        return email, None

    if _enforced(require_verified):
        return None, Response(
            {"error": "Authentication required.", "code": "identity_unverified"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    legacy = _legacy_email(request)
    if not legacy:
        return None, Response(
            {"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST
        )
    return legacy, None
