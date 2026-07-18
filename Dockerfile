# syntax=docker/dockerfile:1

###############################################################################
# Stage 1 — builder
#
# Carries the heavy build toolchain (gcc + -dev headers) needed to compile any
# sdist-only deps, and installs everything into an isolated venv. None of the
# compilers or header packages reach the final image — the runtime stage only
# receives the finished /opt/venv tree.
#
# Python 3.11 to match the rest of the Render config. We install chromium
# ourselves in the runtime stage so the binary lands IN the image and Render's
# deploy phase can't wipe it — the whole reason this service runs from Docker.
###############################################################################
FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# Build-only system deps. pycairo (via xhtml2pdf > svglib) needs a compiler +
# cairo/pango/gdk-pixbuf headers; slim has no `cc`. These stay in the builder.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        libcairo2-dev \
        libpango1.0-dev \
        libffi-dev \
        libgdk-pixbuf2.0-dev \
    && rm -rf /var/lib/apt/lists/*

# Self-contained venv so the runtime stage can grab a single copyable tree.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Requirements first → this layer caches until requirements.txt changes.
# The pip cache mount makes rebuilds fast without bloating the image layer.
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install -r requirements.txt


###############################################################################
# Stage 2 — runtime
#
# Slim base + the prebuilt venv + chromium. No build-essential, no -dev
# headers — that's the size win over the previous single-stage image.
###############################################################################
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PATH="/opt/venv/bin:$PATH"

# Runtime-only shared libs — the non-dev counterparts of the builder's headers,
# so the PDF/cairo path loads at runtime. (Chromium's own deps overlap these,
# but listing them explicitly keeps the runtime contract independent of which
# libs a given playwright version happens to pull.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        shared-mime-info \
        fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Bring the finished venv over from the builder (ships zero compilers).
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Install chromium + chrome-headless-shell with their system deps INTO the
# image (~1.5 GB). Runs before the source copy so code edits never bust this
# layer. `--with-deps` shells out to apt; the browser version matches the
# playwright pip package resolved in the builder (no package/binary drift).
RUN playwright install --with-deps chromium chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/*

# ── SiteOne crawler (optional technical-crawl engine) ──────────────────────
# Bake the Linux x64 binary into the image so run_single_page_analysis can shell
# out to it (siteone_crawl.py -> siteone-crawler on PATH). It stays *disabled* at
# runtime until SIGNALOR_USE_SITEONE=true, so this only affects whether the binary
# is present. Supply the verified release tarball at build time
# (--build-arg SITEONE_URL=... , optional SITEONE_SHA256=...); left unset the build
# skips it and SiteOne simply stays off. Placed before COPY so it stays cached.
ARG SITEONE_URL=""
ARG SITEONE_SHA256=""
RUN if [ -n "$SITEONE_URL" ]; then \
        set -eux; \
        apt-get update && apt-get install -y --no-install-recommends curl ca-certificates; \
        curl -fsSL -o /tmp/siteone.tar.gz "$SITEONE_URL"; \
        if [ -n "$SITEONE_SHA256" ]; then echo "$SITEONE_SHA256  /tmp/siteone.tar.gz" | sha256sum -c -; fi; \
        mkdir -p /opt/siteone; \
        tar -xzf /tmp/siteone.tar.gz -C /opt/siteone; \
        ln -sf "$(find /opt/siteone -type f -name 'siteone-crawler' | head -1)" /usr/local/bin/siteone-crawler; \
        chmod +x /usr/local/bin/siteone-crawler; \
        rm -rf /tmp/siteone.tar.gz /var/lib/apt/lists/*; \
        /usr/local/bin/siteone-crawler --version || true; \
    else \
        echo "SITEONE_URL not set — SiteOne crawler not baked (stays disabled)"; \
    fi

# App source last — keeps the deps + chromium layers cached across edits.
COPY . .

# Bake staticfiles into the image. Production settings require runtime secrets
# (SECRET_KEY, REDIS_URL, …) just to import, and none exist during a build;
# dev settings walk the same STATIC dirs and produce identical output. The
# container's runtime DJANGO_SETTINGS_MODULE stays production (ENV above).
RUN DJANGO_SETTINGS_MODULE=config.settings.development python manage.py collectstatic --no-input

# Render injects $PORT; fall back to 10000 for a local `docker run`.
CMD opentelemetry-instrument gunicorn config.wsgi:application \
    --bind 0.0.0.0:${PORT:-10000} \
    --workers 2 --threads 4 --timeout 600
