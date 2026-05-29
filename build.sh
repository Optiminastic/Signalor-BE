#!/usr/bin/env bash
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

# Chromium for Playwright-based page screenshots (content optimisation preview).
# --with-deps installs the system libraries Chromium needs on Render's Linux image.
# Playwright 1.49+ ships chrome-headless-shell as a SEPARATE browser binary,
# used by default when launching headless. Without it the launch fails with
# "Executable doesn't exist at .../chromium_headless_shell-XXXX/..." even though
# chromium itself is installed.
python -m playwright install --with-deps chromium
python -m playwright install chromium-headless-shell

python manage.py collectstatic --no-input

# Reconcile any known migration-drift cases before running migrate. See
# scripts/reconcile_migrations.py — safe on fresh and healthy databases too.
python scripts/reconcile_migrations.py

python manage.py migrate --no-input
