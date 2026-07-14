"""
Test settings: force a fast, local, in-memory SQLite database so the suite never
touches the remote Postgres. Inherits everything else from development.

Run with:  manage.py test --settings=config.settings.test
"""

from .development import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# No cross-DB routing in tests (the optional blog DB is not created).
DATABASE_ROUTERS = []


# Build the schema directly from the models instead of replaying migrations. Some
# historical migrations use Postgres-only raw SQL (e.g. drop_orphaned_tables) that
# SQLite can't parse; skipping migrations keeps the test DB fast and portable.
class _DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = _DisableMigrations()

# Speed: cheap password hashing + in-memory cache.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
