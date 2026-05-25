from rest_framework.throttling import UserRateThrottle


class PublicApiReadThrottle(UserRateThrottle):
    """Reads: status polls, scores, recommendations."""

    scope = "public_api_read"


class PublicApiWriteThrottle(UserRateThrottle):
    """Writes: create analysis."""

    scope = "public_api_write"
