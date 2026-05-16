import os

import dj_database_url

from .base import *

DEBUG = True

SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-dev-key-change-in-production")

# Local defaults only; add deploy hosts via ALLOWED_HOSTS in .env (comma-separated).
_default_hosts = [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
]
_env_hosts = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()]
ALLOWED_HOSTS = list(dict.fromkeys(_default_hosts + _env_hosts))

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


CORS_ALLOW_ALL_ORIGINS = True
# Chrome 117+ requires this on the preflight for cross-port localhost requests
# (Private Network Access). Without it, browsers block the request before
# sending it, surfacing as "Provisional headers are shown" + ERR_NETWORK.
CORS_ALLOW_PRIVATE_NETWORK = True

# Render/Cloudflare proxy support
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Email backend for development
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Debug toolbar (optional)
INSTALLED_APPS += ["django_extensions"]

# Cache
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Disable throttling in development. Each scope is set to None so that
# view-level scoped throttles (PollingThrottle, ExpensiveThrottle, etc.)
# still resolve their key but DRF treats `None` as no rate limit.
REST_FRAMEWORK = {
    **globals().get("REST_FRAMEWORK", {}),
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {
        "anon": None,
        "user": None,
        "expensive": None,
        "ai_chat": None,
        "dataforseo": None,
        "audit_start": None,
        "polling": None,
        "auth_send": None,
    },
}
