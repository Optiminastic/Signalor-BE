#!/usr/bin/env bash
set -o errexit
set -o pipefail

pip install --upgrade pip
pip install -r requirements.txt

# NOTE: We no longer install Playwright browsers here. Render's deploy
# snapshot wipes the chromium binary regardless of where we put it
# (~/.cache, .ms-playwright, .venv/ms-playwright, even inside the
# playwright package's driver/package/.local-browsers). Server-side
# screenshots are now disabled — see _SERVER_SCREENSHOTS_ENABLED in
# apps/analyzer/services/content_optimisation.py. The FE renders a
# placeholder when the preview is empty.
# To re-enable on a host where chromium actually persists, install via:
#   python -m playwright install --with-deps chromium chromium-headless-shell
# and set ENABLE_SERVER_SCREENSHOTS=1 in the runtime env.

python manage.py collectstatic --no-input

# Reconcile any known migration-drift cases before running migrate. See
# scripts/reconcile_migrations.py — safe on fresh and healthy databases too.
python scripts/reconcile_migrations.py

python manage.py migrate --no-input
