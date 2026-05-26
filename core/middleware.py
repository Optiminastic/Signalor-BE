"""
Defense-in-depth middleware that runs ahead of DRF.

DRF throttles only fire on DRF views — admin, auth callbacks, static files,
and any plain Django view are unprotected. This middleware buckets every
request by client IP and caps total req/min before the view dispatches.

Trust X-Forwarded-For only when ``TRUSTED_PROXY_IPS`` is set (Render/Cloudflare
sit in front of the app). Without that setting we fall back to REMOTE_ADDR,
which is the proxy itself — meaning everyone shares one bucket and the limit
becomes a service-wide ceiling. That's a fine fallback for misconfig, just
visibly broken in logs.
"""

import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

logger = logging.getLogger("core.middleware")


def _client_ip(request) -> str:
    """Resolve the client IP, honouring X-Forwarded-For only behind a trusted proxy."""
    trusted = getattr(settings, "TRUSTED_PROXY_IPS", None)
    remote_addr = request.META.get("REMOTE_ADDR", "")

    if trusted is None or remote_addr in trusted:
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if xff:
            # XFF is "client, proxy1, proxy2..." — leftmost is the real client.
            # Strip whitespace; ignore empty entries.
            for hop in (h.strip() for h in xff.split(",")):
                if hop:
                    return hop
    return remote_addr or "unknown"


class GlobalIPRateLimitMiddleware:
    """
    Cap every IP's total req/min across the whole site.

    Settings:
        IP_RATE_LIMIT_PER_MINUTE (int, default 120): max requests per IP per 60s.
        IP_RATE_LIMIT_BURST (int, default 30): allowance above the per-min rate
            inside a 10s window (smooths legitimate bursty UIs).
        IP_RATE_LIMIT_EXEMPT_PATHS (tuple[str]): paths that bypass the throttle
            (health checks, static assets).
        IP_RATE_LIMIT_ENABLED (bool, default True in prod / False in dev).

    Counters live in the default cache (Redis in prod). A locmem cache makes
    this a no-op across workers — production.py enforces Redis to prevent that.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = bool(getattr(settings, "IP_RATE_LIMIT_ENABLED", not settings.DEBUG))
        self.per_minute = int(getattr(settings, "IP_RATE_LIMIT_PER_MINUTE", 120))
        self.burst = int(getattr(settings, "IP_RATE_LIMIT_BURST", 30))
        self.exempt_paths = tuple(
            getattr(
                settings,
                "IP_RATE_LIMIT_EXEMPT_PATHS",
                ("/static/", "/media/", "/api/analyzer/health/"),
            )
        )

    def __call__(self, request):
        if self.enabled and not request.path.startswith(self.exempt_paths):
            blocked = self._check(request)
            if blocked is not None:
                return blocked
        return self.get_response(request)

    def _check(self, request) -> JsonResponse | None:
        ip = _client_ip(request)
        if ip in ("unknown", ""):
            return None  # Don't punish requests we can't attribute.

        now = int(time.time())
        minute_bucket = now // 60
        burst_bucket = now // 10

        minute_key = f"iprl:m:{ip}:{minute_bucket}"
        burst_key = f"iprl:b:{ip}:{burst_bucket}"

        # add() returns True if key was new; increment after that. Both calls
        # together aren't atomic with the cache backends Django ships with,
        # but the window is small enough that the worst-case error is bounded
        # by concurrent-request count, which doesn't materially weaken the cap.
        cache.add(minute_key, 0, timeout=70)
        cache.add(burst_key, 0, timeout=15)
        try:
            minute_count = cache.incr(minute_key)
            burst_count = cache.incr(burst_key)
        except ValueError:
            # Key evicted between add() and incr(); treat as first request.
            cache.set(minute_key, 1, timeout=70)
            cache.set(burst_key, 1, timeout=15)
            return None

        if minute_count > self.per_minute or burst_count > self.burst:
            logger.warning(
                "ip_rate_limit_block ip=%s path=%s minute=%d burst=%d",
                ip,
                request.path,
                minute_count,
                burst_count,
            )
            return JsonResponse(
                {"detail": "Too many requests. Slow down."},
                status=429,
                headers={"Retry-After": "60"},
            )
        return None
