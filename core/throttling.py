"""
Per-route throttle classes.

Each subclass pins a ``scope`` string that ``DEFAULT_THROTTLE_RATES`` in
``config/settings/base.py`` looks up. Falls back to anon-rate keying for
unauthenticated requests so public endpoints (analyze, etc.) are still
bounded — DRF's UserRateThrottle uses request.user.pk when authenticated
and the client IP otherwise.

Rates live in ``config/settings/base.py``. Don't hardcode rates here.
"""
from rest_framework.throttling import UserRateThrottle


class ResourceKeyedThrottle(UserRateThrottle):
    """
    Cost-bearing throttle keyed on a STABLE resource (run slug or account
    email) instead of the client IP.

    Why: the analyzer's expensive endpoints are ``AllowAny`` and DRF's
    default keys an anonymous caller by IP. A botnet with rotating IPs gets
    a fresh bucket per request and bypasses the cap entirely (same attack
    ``OnboardEmailThrottle`` and the onboarding-token gate were built to
    stop). Keying on the run slug / owner email instead means every request
    that targets the same run or account shares one bucket no matter how
    many IPs it comes from. To get a fresh bucket an attacker must create a
    new run — which is itself Turnstile-gated via /onboarding-start/.

    Key precedence:
      1. authenticated user  → ``user:<pk>``           (DRF default)
      2. run slug in the URL → ``run:<slug>``           (per-run cap)
      3. email in the body   → ``email:<normalized>``   (per-account cap)
      4. neither             → ``ip:<ident>``           (legacy IP fallback)
    """

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f"user:{request.user.pk}"
        else:
            ident = self._resource_ident(request, view)
        return self.cache_format % {"scope": self.scope, "ident": ident}

    def _resource_ident(self, request, view) -> str:
        slug = ""
        try:
            slug = (getattr(view, "kwargs", None) or {}).get("slug") or ""
        except Exception:
            slug = ""
        if slug:
            return f"run:{slug}"

        email = ""
        try:
            email = (request.data.get("email") or "").strip().lower()
        except Exception:
            email = ""
        if email:
            return f"email:{email}"

        return f"ip:{self.get_ident(request)}"


class ExpensiveThrottle(ResourceKeyedThrottle):
    """Full re-analysis, auto-fix runs, blog generation, geo re-fix."""

    scope = "expensive"


class AiChatThrottle(ResourceKeyedThrottle):
    """Per-message AI chat (Gemini-backed)."""

    scope = "ai_chat"


class DataForSEOThrottle(ResourceKeyedThrottle):
    """Routes that hit DataForSEO (domain analytics, citation enrich)."""

    scope = "dataforseo"


class AuditStartThrottle(ResourceKeyedThrottle):
    """Sitemap/schema/rank audit kickoffs."""

    scope = "audit_start"


class PollingThrottle(UserRateThrottle):
    """Status/list/detail reads — high but finite ceiling. Cheap, so IP-keyed."""

    scope = "polling"


class AuthSendThrottle(UserRateThrottle):
    """OTP / email-link sends. Keyed per user; for anon, falls back to IP."""

    scope = "auth_send"
