import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env from project root. override=True so values in this file win over empty
# or stale variables already in the process environment (a common cause of "missing" API keys).
_env_path = BASE_DIR / ".env"
_env_local = BASE_DIR / ".env.local"
if _env_path.exists():
    load_dotenv(_env_path, override=True)
if _env_local.exists():
    load_dotenv(_env_local, override=True)

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "apps.accounts.apps.AccountsConfig",
    "apps.organizations.apps.OrganizationsConfig",
    "apps.analyzer.apps.AnalyzerConfig",
    "apps.integrations.apps.IntegrationsConfig",
    "apps.visibility.apps.VisibilityConfig",
    "apps.recommendation.apps.RecommendationConfig",
    "apps.referrals.apps.ReferralsConfig",
    "apps.partners.apps.PartnersConfig",
    "apps.public_api.apps.PublicApiConfig",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    # Global per-IP rate limit. Runs before view dispatch so admin / oauth /
    # non-DRF paths are also bounded. No-op in dev (DEBUG=True) unless
    # IP_RATE_LIMIT_ENABLED is overridden.
    "core.middleware.GlobalIPRateLimitMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Per-IP global rate limit knobs. Defaults are tuned for a single-user-per-IP
# pattern; raise IP_RATE_LIMIT_PER_MINUTE if you have NATed corporate users.
IP_RATE_LIMIT_PER_MINUTE = int(os.getenv("IP_RATE_LIMIT_PER_MINUTE", 240))
IP_RATE_LIMIT_BURST = int(os.getenv("IP_RATE_LIMIT_BURST", 40))
# TRUSTED_PROXY_IPS: comma-separated IPs of proxies that may forward XFF.
# Empty list = trust XFF unconditionally (use only behind a known LB/CDN);
# None (default) = trust XFF only if request comes from REMOTE_ADDR == loopback.
_TRUSTED_PROXIES = os.getenv("TRUSTED_PROXY_IPS", "")
TRUSTED_PROXY_IPS = (
    {ip.strip() for ip in _TRUSTED_PROXIES.split(",") if ip.strip()} if _TRUSTED_PROXIES else None
)

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {
            "min_length": 8,
        },
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Discovery-engine PDF reports directory (override via env if needed)
DISCOVERY_REPORTS_DIR = os.getenv(
    "DISCOVERY_REPORTS_DIR",
    str(BASE_DIR.parent / "discovery-engine" / "reports"),
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Request-size guardrails. Defends against memory-exhaustion attacks via giant
# JSON bodies or form payloads. Largest legitimate POST today is the content
# editor save (raw HTML files) at ~500 KB; 1 MB gives 2x headroom.
# Override per-deploy with env vars if a specific endpoint needs more.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", 1024 * 1024))  # 1 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("FILE_UPLOAD_MAX_MEMORY_SIZE", 5 * 1024 * 1024))  # 5 MB
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(os.getenv("DATA_UPLOAD_MAX_NUMBER_FIELDS", 1000))
DATA_UPLOAD_MAX_NUMBER_FILES = int(os.getenv("DATA_UPLOAD_MAX_NUMBER_FILES", 10))

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # 'apps.auth.authentication.JWTAuthentication',
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    # Global ceilings act as defense-in-depth. Per-route limits are applied
    # via ScopedRateThrottle on the views themselves — see scopes below.
    "DEFAULT_THROTTLE_RATES": {
        # Global ceiling for any AllowAny view that doesn't pick a scoped throttle.
        # Tight enough that an unauth attacker can't cheaply hammer the API,
        # generous enough for a real visitor opening a few pages.
        "anon": "60/hour",
        # Authed user ceiling. Per-route scoped throttles below override these.
        "user": "600/hour",
        # Cost-incurring routes (LLM, full re-analysis, auto-fix, blog gen).
        "expensive": "10/minute",
        # AI chat — per-message Gemini cost; allow burst for normal convo.
        "ai_chat": "30/minute",
        # DataForSEO-backed routes (domain analytics, citation enrich,
        # audit starts that hit the vendor). Bounds vendor credit burn.
        "dataforseo": "20/minute",
        # Audit "start" endpoints — kick off background tasks, expensive
        # to spawn but not as costly as full analyze.
        "audit_start": "15/minute",
        # Status / list / detail polls. High enough that legit clients
        # never hit it; low enough that a runaway frontend loop is capped.
        "polling": "120/minute",
        # Auth-adjacent: email/OTP sends, password resets. Per-IP.
        "auth_send": "10/minute",
        # Public API (Bearer-token) — per-key ceilings; plan tiers can refine later.
        "public_api_read": "300/minute",
        "public_api_write": "30/minute",
    },
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
    },
    "handlers": {
        "console": {"level": "INFO", "class": "logging.StreamHandler", "formatter": "simple"},
        "file": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOGS_DIR / "django.log",
            "maxBytes": 1024 * 1024 * 15,  # 15MB
            "backupCount": 10,
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "core": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_ANALYTICS_REDIRECT_URI = os.getenv(
    "GOOGLE_ANALYTICS_REDIRECT_URI",
    "http://localhost:3000/settings/integrations/callback/google-analytics",
)
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

DATAFORSEO_LOGIN = os.getenv("DATAFORSEO_LOGIN", "")
DATAFORSEO_PASSWORD = os.getenv("DATAFORSEO_PASSWORD", "")

# Cloudflare Turnstile (anti-bot for public AI endpoints). When unset the
# server-side check is skipped — useful for dev/staging without a CF account.
# The frontend respects NEXT_PUBLIC_TURNSTILE_SITE_KEY independently.
TURNSTILE_SECRET = os.getenv("TURNSTILE_SECRET", "")

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("SMTP_PASS", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@example.com")

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 1209600  # 2 weeks

CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}
