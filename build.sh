#!/usr/bin/env bash
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

# Chromium for Playwright-based page screenshots (content optimisation preview).
# --with-deps installs the system libraries Chromium needs on Render's Linux image.
python -m playwright install --with-deps chromium

python manage.py collectstatic --no-input
python manage.py migrate --no-input
