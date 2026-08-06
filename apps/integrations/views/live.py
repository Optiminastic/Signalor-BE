"""Live visitors: who is on the site right now, human or AI crawler.

Feeds the top-bar indicator, which every dashboard page polls. Two consequences
shape this module:

* **It must not 500.** Every non-authorization failure degrades to a 200 with a
  `reason` code, because an error here is visible on every page in the app.
* **It must not hammer GA.** Responses are cached per org so N open tabs collapse
  to one upstream call, and failures are cached too — see `_FAIL_TTL`.
"""

from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Max
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analyzer.crawler_bots import BOT_LABELS
from apps.analyzer.models import CrawlerHit
from core.permissions.throttling import PollingThrottle

from ..models import Integration
from ..services.ga4_realtime import (
    LIVE_WINDOW_MINUTES,
    NoPropertySelected,
    fetch_realtime_snapshot,
    fetch_today_sources,
)
from ._shared import _org_id_param, _resolve_org, logger

# Realtime is the only genuinely live half, so it gets the short TTL. Today's
# source mix changes far more slowly than 20s and costs the same quota.
_LIVE_TTL = 20
_SOURCES_TTL = 120

# Negative caching is load-bearing, not an optimisation. GA allows only ~10
# realtime *errors* per property per hour; a revoked or misconfigured property
# erroring on every poll would burn that in about four minutes and lock the
# property out of realtime — including for the existing daily sync.
_FAIL_TTL = 300

# Guards the thundering herd: several workers can miss the same cold key in the
# same millisecond, and GA allows only 10 concurrent realtime requests.
_LOCK_TTL = 15

_BOT_LIMIT = 8

# Closed set. The upstream exception text must never reach the client.
REASON_NOT_CONNECTED = "not_connected"
REASON_NO_PROPERTY = "no_property"
REASON_AUTH_EXPIRED = "auth_expired"
REASON_API_ERROR = "api_error"


def _unavailable(reason: str) -> dict:
    return {
        "available": False,
        "reason": reason,
        "active_users": 0,
        "countries": [],
        "sources": {"available": False, "scope": "today", "rows": []},
    }


def _ga_integration(org) -> Integration | None:
    return Integration.objects.filter(
        organization=org, provider=Integration.Provider.GOOGLE_ANALYTICS, is_active=True
    ).first()


def _cached(key: str, ttl: int, produce):
    """Cache-aside with a stampede lock.

    On a lock miss we return None rather than queue behind the winner: a stale
    or absent number in the top bar is far cheaper than holding a request open.
    """
    hit = cache.get(key)
    if hit is not None:
        return hit
    if not cache.add(f"{key}:lock", 1, _LOCK_TTL):
        return None
    value = produce()
    cache.set(key, value, ttl)
    return value


def _humans(org) -> dict:
    """The GA4 half. Never raises — every failure becomes a `reason`."""
    failed = cache.get(f"live_visitors:fail:{org.id}")
    if failed:
        return _unavailable(failed)

    integration = _ga_integration(org)
    if integration is None:
        return _unavailable(REASON_NOT_CONNECTED)

    try:
        snapshot = _cached(
            f"live_visitors:rt:{org.id}", _LIVE_TTL, lambda: fetch_realtime_snapshot(integration)
        )
    except NoPropertySelected:
        return _unavailable(REASON_NO_PROPERTY)
    except Exception as exc:  # noqa: BLE001 - upstream client raises many types
        reason = REASON_AUTH_EXPIRED if _is_auth_error(exc) else REASON_API_ERROR
        cache.set(f"live_visitors:fail:{org.id}", reason, _FAIL_TTL)
        logger.warning("live-visitors: GA realtime failed for org=%s reason=%s", org.id, reason)
        return _unavailable(reason)

    if snapshot is None:  # lock held by another worker; report nothing this tick
        return _unavailable(REASON_API_ERROR)
    return {
        "available": True,
        "reason": "",
        "active_users": snapshot["active_users"],
        "countries": snapshot["countries"],
        "sources": _sources(org, integration),
    }


def _sources(org, integration: Integration) -> dict:
    """Today's source mix. A failure here must not blank the live count."""
    try:
        rows = _cached(f"live_visitors:src:{org.id}", _SOURCES_TTL, lambda: fetch_today_sources(integration))
    except Exception:  # noqa: BLE001
        logger.warning("live-visitors: GA sources failed for org=%s", org.id)
        rows = None
    if rows is None:
        return {"available": False, "scope": "today", "rows": []}
    return {"available": True, "scope": "today", "rows": rows}


def _is_auth_error(exc: Exception) -> bool:
    """Distinguish "reconnect Google" from "Google is having a bad day"."""
    text = str(exc).lower()
    return any(hint in text for hint in ("invalid_grant", "unauthorized", "401", "permission"))


def _bots(org) -> dict:
    """AI crawler hits in the live window.

    Deliberately uncached: one aggregate over an indexed range is cheap, and a
    stale bot list is more annoying than the query. No overlap with the GA half
    either — GA4 filters known crawlers out, and ingest only ever stores a hit
    whose user agent the server itself matched to a known bot.
    """
    since = timezone.now() - timedelta(minutes=LIVE_WINDOW_MINUTES)
    recent = (
        CrawlerHit.objects.filter(organization=org, hit_at__gte=since)
        .values("bot", "path")
        .annotate(hits=Count("id"), last_seen=Max("hit_at"))
        .order_by("-last_seen")[:_BOT_LIMIT]
    )
    rows = [
        {
            "bot": r["bot"],
            "label": BOT_LABELS.get(r["bot"], r["bot"]),
            "path": r["path"] or "/",
            "hits": r["hits"],
            "last_seen": r["last_seen"].isoformat(),
        }
        for r in recent
    ]
    return {
        "available": True,
        # Separates "quiet right now" from "the snippet was never installed",
        # which need different empty states.
        "ever_seen": CrawlerHit.objects.filter(organization=org).exists(),
        "total_hits": sum(r["hits"] for r in rows),
        "rows": rows,
    }


class LiveVisitorsView(APIView):
    """GET live-visitors/?email=&org_id= — humans (GA4) + AI bots (crawler hits)."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email parameter is required."}, status=status.HTTP_400_BAD_REQUEST)
        org, err = _resolve_org(email, _org_id_param(request))
        if err:
            return err

        humans = _humans(org)
        bots = _bots(org)
        return Response(
            {
                "generated_at": timezone.now().isoformat(),
                "window_minutes": LIVE_WINDOW_MINUTES,
                "live_total": humans["active_users"] + bots["total_hits"],
                "humans": humans,
                "bots": bots,
            }
        )
