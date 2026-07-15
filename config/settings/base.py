import os
from pathlib import Path

from corsheaders.defaults import default_headers as default_cors_headers
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

# ── Sentry (error monitoring) ──────────────────────────────────────────────
# No-op unless SENTRY_DSN is set, so local/dev/tests never report. Set the DSN
# in the staging/prod environment to capture unhandled exceptions + DRF 500s.
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
        # Attach request headers and user IP to events. See
        # https://docs.sentry.io/platforms/python/data-management/data-collected/
        send_default_pii=True,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
    )

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
    "apps.drip.apps.DripConfig",
    "apps.integrations.apps.IntegrationsConfig",
    "apps.github_agent.apps.GithubAgentConfig",
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
        # Onboarding write — per-email cap on /organizations/onboard/. Stops
        # rotating-IP attackers from creating dupes for a single email.
        "onboard_email": "5/hour",
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
            # ConcurrentRotatingFileHandler (not stdlib RotatingFileHandler) so
            # rotation is multiprocess-safe. On Windows the stdlib handler's
            # os.rename() during rollover fails with WinError 32 whenever a second
            # process (dev-server autoreloader) or the analyzer thread-pool still
            # holds django.log open, which then errors on every log emit.
            "class": "concurrent_log_handler.ConcurrentRotatingFileHandler",
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
# Search Console uses a server-side OAuth callback (the backend exchanges the
# code, then redirects the browser back to the frontend) — point this at the
# backend endpoint, not the frontend.
GOOGLE_SEARCH_CONSOLE_REDIRECT_URI = os.getenv(
    "GOOGLE_SEARCH_CONSOLE_REDIRECT_URI",
    "http://localhost:8000/api/integrations/google-search-console/callback/",
)
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

# GitHub App — autonomous GEO fixer (apps.github_agent). Empty defaults so the
# server still boots before the App is registered; services/auth.py raises a
# clear error at call time if these are missing. GITHUB_APP_PRIVATE_KEY may be a
# raw PEM or base64-encoded PEM (base64 survives single-line .env files).
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "")
GITHUB_APP_SLUG = os.getenv("GITHUB_APP_SLUG", "")
GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

# Knowledge base ingestion (Epic 3). Each analysis run chunks + embeds the pages
# it already crawled into BrandCorpusChunk rows (org-scoped, fail-soft). Bounds
# keep per-run embedding cost predictable; skip-unchanged keeps steady state cheap.
SIGNALOR_ENABLE_INGESTION = os.getenv("SIGNALOR_ENABLE_INGESTION", "true").lower() != "false"
CORPUS_EMBED_MODEL = os.getenv("CORPUS_EMBED_MODEL", "models/text-embedding-004")
CORPUS_CHUNK_MAX_CHARS = int(os.getenv("CORPUS_CHUNK_MAX_CHARS", 2800))
CORPUS_CHUNK_OVERLAP_CHARS = int(os.getenv("CORPUS_CHUNK_OVERLAP_CHARS", 200))
CORPUS_CHUNK_MIN_CHARS = int(os.getenv("CORPUS_CHUNK_MIN_CHARS", 40))
CORPUS_MAX_PAGES = int(os.getenv("CORPUS_MAX_PAGES", 25))
CORPUS_MAX_CHUNKS_PER_RUN = int(os.getenv("CORPUS_MAX_CHUNKS_PER_RUN", 300))

# Semantic response cache (Epic 7). Opt-in per call site via ask_llm(cache=True); this
# flag is the kill-switch. The similarity floor is deliberately conservative -- only
# near-identical prompts may reuse a response, and only within the same
# (purpose, model, organization) scope.
SIGNALOR_ENABLE_SEMANTIC_CACHE = os.getenv("SIGNALOR_ENABLE_SEMANTIC_CACHE", "true").lower() != "false"
SEMANTIC_CACHE_SIMILARITY = float(os.getenv("SEMANTIC_CACHE_SIMILARITY", 0.97))
SEMANTIC_CACHE_TTL_SECONDS = int(os.getenv("SEMANTIC_CACHE_TTL_SECONDS", 7 * 24 * 3600))

DATAFORSEO_LOGIN = os.getenv("DATAFORSEO_LOGIN", "")
DATAFORSEO_PASSWORD = os.getenv("DATAFORSEO_PASSWORD", "")

# Open PageRank — free, Common Crawl–based domain authority metric powering the
# public Domain Rating tool. Free key from domcop.com/openpagerank.
OPENPAGERANK_API_KEY = os.getenv("OPENPAGERANK_API_KEY", "")

# Scraping-API fallback for the crawler. When a direct crawl is hard-blocked
# (e.g. 403 from a Cloudflare/WAF against our datacenter IPs), the crawler
# re-fetches via this API from residential IPs. Disabled (no behavior change)
# until SCRAPER_API_KEY is set. Provider: "scrapingbee" (default) or "scraperapi".
# SCRAPER_RENDER_JS toggles JS rendering (more expensive; off by default since
# the common block is IP-reputation based, not a JS challenge).
# SCRAPER_STEALTH (on by default) routes the fallback through the provider's
# anti-bot proxy with JS rendering (ScrapingBee stealth_proxy / ScraperAPI
# ultra_premium) so Cloudflare managed challenges / Turnstile are solved
# server-side. Costs more provider credits, but only fires on blocked sites.
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")
SCRAPER_API_PROVIDER = os.getenv("SCRAPER_API_PROVIDER", "scrapingbee")
SCRAPER_RENDER_JS = os.getenv("SCRAPER_RENDER_JS", "false").lower() == "true"
SCRAPER_STEALTH = os.getenv("SCRAPER_STEALTH", "true").lower() == "true"

# Cloudflare Turnstile (anti-bot for public AI endpoints). When unset the
# server-side check is skipped — useful for dev/staging without a CF account.
# The frontend respects NEXT_PUBLIC_TURNSTILE_SITE_KEY independently.
TURNSTILE_SECRET = os.getenv("TURNSTILE_SECRET", "")

AMPLITUDE_API_KEY = os.getenv("AMPLITUDE_API_KEY", "")

# Drip + transactional emails relay through SendGrid SMTP when configured;
# otherwise the legacy SMTP_USER/SMTP_PASS path stays active.
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
if SENDGRID_API_KEY:
    EMAIL_HOST = "smtp.sendgrid.net"
    EMAIL_HOST_USER = "apikey"
    EMAIL_HOST_PASSWORD = SENDGRID_API_KEY
else:
    EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
    EMAIL_HOST_USER = os.getenv("SMTP_USER", "")
    EMAIL_HOST_PASSWORD = os.getenv("SMTP_PASS", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@example.com")
FOUNDER_FROM_EMAIL = os.getenv("FOUNDER_FROM_EMAIL", "rishi@signalor.ai")
FOUNDER_FROM_NAME = os.getenv("FOUNDER_FROM_NAME", "Rishi")

# Branding used by drip + welcome HTML email templates.
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
# Cloudinary-hosted with f_auto so email clients that don't render SVG
# (Outlook etc.) get a PNG fallback automatically.
SIGNALOR_LOGO_URL = (
    os.getenv("SIGNALOR_LOGO_URL")
    or "https://res.cloudinary.com/dui7h1n3d/image/upload/q_auto/f_auto/v1779273045/icon_mitiu2.svg"
)
SIGNALOR_BRAND_PRIMARY = "#e04a3d"

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
# The onboarding gate sends a custom X-Onboarding-Token header. It isn't in
# django-cors-headers' default allow-list, so without this the browser passes
# the preflight (OPTIONS 200) but blocks the actual request — the FE then shows
# "Cannot reach the server." Extend the defaults rather than replace them.
CORS_ALLOW_HEADERS = (*default_cors_headers, "x-onboarding-token")

# ── Satellite blog network (shared Neon DB) ───────────────────────────────────
# Domains of our 5 external Next.js blog sites (category == site). Used to build
# the live backlink URL (``<domain>/blog/<slug>``) shown in "Our backlinks".
SATELLITE_SITES = {
    "research": os.getenv("SATELLITE_SITE_RESEARCH_URL", "https://brightsfindings.com").rstrip("/"),
    "listicals": os.getenv("SATELLITE_SITE_LISTICALS_URL", "https://thepickpost.com").rstrip("/"),
    "market_trends": os.getenv("SATELLITE_SITE_MARKET_TRENDS_URL", "https://trendledgers.com").rstrip("/"),
    "comparison": os.getenv("SATELLITE_SITE_COMPARISON_URL", "https://betterversus.com").rstrip("/"),
    "step_guide": os.getenv("SATELLITE_SITE_STEP_GUIDE_URL", "https://guidefactories.com").rstrip("/"),
}
# Shared blog DB (Signalor writes BlogPost rows; the satellite sites read it).
# The "blog" connection is added per-env (development/production/staging) where
# DATABASES is defined. Router sends the BlogPost model to it.
BLOG_DATABASE_URL = os.getenv("BLOG_DATABASE_URL", "")
DATABASE_ROUTERS = ["config.db_router.BlogRouter"]
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# ── Celery ───────────────────────────────────────────────────────────────
# Today only the sitemap audit task (apps.analyzer.celery_tasks) is on
# Celery; everything else still uses threading.Thread. When CELERY_BROKER_URL
# is unset, tasks run eagerly in-process so dev / tests don't need a worker.
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", ""))
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "")
CELERY_TASK_ALWAYS_EAGER = not bool(CELERY_BROKER_URL)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 60 * 30  # 30-minute hard ceiling per task
CELERY_TASK_SOFT_TIME_LIMIT = 60 * 25
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
