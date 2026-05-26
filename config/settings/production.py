import os

import dj_database_url

from .base import *

DEBUG = False

# Render / reverse proxies: correct scheme and host for request.build_absolute_uri()
# (needed for Shopify OAuth redirect_uri matching Partners allowlist).
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable must be set")

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").split(",")

# Prefer DATABASE_URL (Render's default for Postgres add-ons, Neon, Supabase).
# Falls back to the 5 DB_* env vars for self-managed Postgres setups.
_DATABASE_URL = os.getenv("DATABASE_URL", "")
if _DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            _DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,
        ),
    }
    DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = 10
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME"),
            "USER": os.getenv("DB_USER"),
            "PASSWORD": os.getenv("DB_PASSWORD"),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": 600,
            "OPTIONS": {
                "connect_timeout": 10,
            },
        },
    }

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

CORS_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
if not CORS_ALLOWED_ORIGINS:
    raise ValueError(
        "CORS_ALLOWED_ORIGINS must be set in production (comma-separated origins). "
        "Refusing to boot with no allowed origins — that would silently block all "
        "browser traffic, but also masks misconfiguration."
    )
CORS_ALLOW_CREDENTIALS = True
# Never allow wildcard in prod. CSRF needs explicit origins too.
CORS_ALLOW_ALL_ORIGINS = False
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL")

# Redis is REQUIRED in production. Throttling, IP rate limits, and abuse
# protection all live in cache; locmem fragments per-worker (multiplying every
# limit by worker count) and resets on restart — i.e. throttles become advisory.
# Fail boot rather than silently weaken protection.
_REDIS_URL = os.getenv("REDIS_URL", "")
if not _REDIS_URL:
    raise ValueError(
        "REDIS_URL is required in production. Per-worker locmem cache breaks "
        "throttling and rate limiting under Gunicorn. Provision a Redis instance "
        "and set REDIS_URL."
    )
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": _REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SOCKET_CONNECT_TIMEOUT": 5,
            "SOCKET_TIMEOUT": 5,
            "RETRY_ON_TIMEOUT": True,
            "MAX_CONNECTIONS": 50,
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 50,
                "retry_on_timeout": True,
            },
        },
        "KEY_PREFIX": "geo_be",
        "TIMEOUT": 300,
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

ADMIN_URL = os.getenv("ADMIN_URL", "admin/")
