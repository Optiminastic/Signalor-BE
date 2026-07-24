"""CI-only settings for migration safety linting.

Unlike ``test.py`` (which disables migrations and uses SQLite), this keeps real
migrations enabled and registers ``django-migration-linter`` so its
``lintmigrations`` command is available. It expects ``DATABASE_URL`` to point at
a throwaway Postgres + pgvector so pgvector migrations and Postgres-specific
lock checks resolve correctly. NEVER used to serve the app.

Usage (CI):
    DJANGO_SETTINGS_MODULE=config.settings.ci_migrations \
        python manage.py lintmigrations origin/main --warnings-as-errors
"""

from .development import *  # noqa: F401,F403

# Register the linter only here so it never lands in a production INSTALLED_APPS.
INSTALLED_APPS = [*INSTALLED_APPS, "django_migration_linter"]  # noqa: F405
