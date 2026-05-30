# syntax=docker/dockerfile:1

# Python 3.11 to match the previous Render Python-runtime config. Playwright
# image variants only ship 3.10 (jammy) and 3.12 (noble), so we build on a
# clean python:3.11 base and install chromium ourselves — chromium lands
# IN the image so Render's deploy phase can't wipe it (the entire reason
# we abandoned the non-Docker build).
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Python deps first so this layer caches independently of source changes.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Install chromium + chrome-headless-shell with system deps. --with-deps
# needs root (we're root inside Docker). Runs AFTER pip install so the
# chromium build number matches whatever playwright pip resolved — no
# version drift between package and binary.
RUN playwright install --with-deps chromium chromium-headless-shell

# App source last so code changes don't bust the chromium install layer.
COPY . .

# Collect static at build time so the runtime container is read-only-ish.
RUN python manage.py collectstatic --no-input

# Render injects $PORT; fall back to 10000 for local docker run.
CMD opentelemetry-instrument gunicorn config.wsgi:application \
    --bind 0.0.0.0:${PORT:-10000} \
    --workers 2 --threads 4 --timeout 600
