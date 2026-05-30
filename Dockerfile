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
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# System packages: the python:3.11-slim base strips out compilers, but
# several deps need to build from source on install or load shared libs
# at runtime:
#   - pycairo (via xhtml2pdf > svglib): build-essential, pkg-config,
#     libcairo2-dev — slim has no `cc`, install fails with "Unknown
#     compiler(s): [['cc'], ['gcc'], ...]"
#   - weasyprint: libpango, libgdk-pixbuf (loaded at runtime)
#   - playwright `install --with-deps` shells out to apt-get itself, which
#     needs the package manager available
# Keep build tools in the final image for now — chromium dominates size
# (~1.5 GB), shaving 200 MB off doesn't change the deploy economics.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    libcairo2-dev \
    libpango1.0-dev \
    libffi-dev \
    libgdk-pixbuf2.0-dev \
    shared-mime-info \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

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

# Collect static at build time so the staticfiles ship with the image.
# Override DJANGO_SETTINGS_MODULE for this step only: production settings
# require SECRET_KEY, CORS_ALLOWED_ORIGINS, REDIS_URL to even import, and
# none of those exist during a Docker build (Render injects them at
# runtime). collectstatic just walks STATIC dirs — both settings produce
# the same output. The container's runtime DJANGO_SETTINGS_MODULE
# remains production (set by ENV above).
RUN DJANGO_SETTINGS_MODULE=config.settings.development python manage.py collectstatic --no-input

# Render injects $PORT; fall back to 10000 for local docker run.
CMD opentelemetry-instrument gunicorn config.wsgi:application \
    --bind 0.0.0.0:${PORT:-10000} \
    --workers 2 --threads 4 --timeout 600
