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


class ExpensiveThrottle(UserRateThrottle):
    """Full re-analysis, auto-fix runs, blog generation, geo re-fix."""

    scope = "expensive"


class AiChatThrottle(UserRateThrottle):
    """Per-message AI chat (Gemini-backed)."""

    scope = "ai_chat"


class DataForSEOThrottle(UserRateThrottle):
    """Routes that hit DataForSEO (domain analytics, citation enrich)."""

    scope = "dataforseo"


class AuditStartThrottle(UserRateThrottle):
    """Sitemap/schema/rank audit kickoffs."""

    scope = "audit_start"


class PollingThrottle(UserRateThrottle):
    """Status/list/detail reads — high but finite ceiling."""

    scope = "polling"


class AuthSendThrottle(UserRateThrottle):
    """OTP / email-link sends. Keyed per user; for anon, falls back to IP."""

    scope = "auth_send"
