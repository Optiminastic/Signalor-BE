"""Local mirror of production settings for the Docker Compose dev stack.

Runs the app exactly like production - Redis cache, real throttling,
DEBUG=False, DB_* Postgres, the same Celery brokers - with only the
TLS-dependent hardening relaxed, so the stack is reachable over plain
http://localhost:8000 without Caddy in front.

Selected via DJANGO_SETTINGS_MODULE=config.settings.local_prod in
deploy/stack.local.env. Never used in production.
"""

from .production import *  # noqa: F401,F403

# ── Relax TLS-only hardening (no Caddy/HTTPS in the local stack) ──────────
# In production these are enforced by Caddy terminating TLS and forwarding
# X-Forwarded-Proto=https. Locally there is no proxy, so keeping them on would
# 301-redirect every request to https and refuse to set session/CSRF cookies.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

# Don't send real email from a developer machine; print it to the container log.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
