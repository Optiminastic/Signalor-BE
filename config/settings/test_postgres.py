"""
Test settings on a real, throwaway Postgres.

SQLite ignores row locks (``SELECT ... FOR UPDATE``), so the default test
settings can't show whether concurrent requests are serialized correctly.
These settings run the same tests on Postgres, with real migrations, so they
can.

The database comes ONLY from ``TEST_DATABASE_URL`` (never ``DATABASE_URL``, which
may point at a shared database), and only a local or CI host is accepted.
Django's test runner creates and drops its own ``test_<name>`` database there.

Run (CI, or against the local Docker stack):
    TEST_DATABASE_URL=postgres://user:pass@localhost:5432/signalor_ci \
        python manage.py test apps.growth_agent --settings=config.settings.test_postgres
"""

import os
from urllib.parse import urlsplit

import dj_database_url

# The file name matches the test runner's test*.py pattern, so a plain
# ``manage.py test`` (SQLite settings) imports it while discovering tests. Only
# check and switch the database when these are the settings actually in use.
# Read before importing the base settings: they load .env with override=True,
# which may replace DJANGO_SETTINGS_MODULE.
_ACTIVE = os.environ.get("DJANGO_SETTINGS_MODULE") == __name__

from .test import *  # noqa: E402, F401, F403

# Hosts a throwaway test database may live on: this machine, or a CI/compose
# service container. Anything else is refused.
ALLOWED_TEST_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres", "db"})

_url = os.getenv("TEST_DATABASE_URL", "")
if _ACTIVE and not _url:
    raise RuntimeError("config.settings.test_postgres needs TEST_DATABASE_URL (a throwaway Postgres).")
_host = urlsplit(_url).hostname or ""
if _ACTIVE and _host not in ALLOWED_TEST_DB_HOSTS:
    raise RuntimeError(
        f"Refusing to run tests against database host {_host!r}: "
        "only a local or CI Postgres is allowed for config.settings.test_postgres."
    )

if _ACTIVE:
    DATABASES = {"default": dj_database_url.parse(_url)}

# Real migrations, unlike the SQLite settings: they are part of what this checks.
MIGRATION_MODULES = {}
