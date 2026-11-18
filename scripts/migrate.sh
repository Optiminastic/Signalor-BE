#!/bin/sh
# Run migrations as the migration role, then re-apply the database role split.
#
# With MIGRATION_DATABASE_URL set (the split deployments), migrations run as
# signalor_owner and the running app never receives those credentials. Without
# it, migrations run exactly as before (today's single-role deployments).
set -eu

if [ -n "${MIGRATION_DATABASE_URL:-}" ]; then
    # config/settings/base.py loads .env / .env.local with override=True, which
    # would silently replace the URL below with whatever .env names. Refuse.
    for env_file in .env .env.local; do
        if [ -f "$env_file" ]; then
            echo "Refusing: $env_file exists and would override MIGRATION_DATABASE_URL." >&2
            exit 1
        fi
    done
    DATABASE_URL="$MIGRATION_DATABASE_URL" python manage.py migrate --no-input
    DATABASE_URL="$MIGRATION_DATABASE_URL" python manage.py growth_apply_role_split --require-roles
else
    python manage.py migrate --no-input
fi
