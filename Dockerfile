# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 - build the virtualenv.
# Dependencies are resolved in a throwaway layer so the runtime image carries
# no compiler, no pip cache and no build metadata.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==22.0.0

# ---------------------------------------------------------------------------
# Stage 2 - runtime.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=routeable_app.settings \
    PORT=8000

# curl is here only for the healthcheck below; nothing else needs it.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 ledger \
    && useradd --system --uid 10001 --gid ledger --home /app --shell /usr/sbin/nologin ledger

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=ledger:ledger manage.py ./
COPY --chown=ledger:ledger routeable_app ./routeable_app
COPY --chown=ledger:ledger api ./api

# SQLite lives on a volume, not in the image layer.
RUN mkdir -p /data && chown ledger:ledger /data
ENV DJANGO_DB_PATH=/data/db.sqlite3

USER ledger

EXPOSE 8000

# /healthz answers without touching a ledger provider, so the probe stays
# honest about this container rather than about Intuit's uptime.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:${PORT}/healthz || exit 1

# Two workers by default: this process is I/O-bound on the provider APIs, and
# the shared requests session pools connections inside each worker.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn routeable_app.wsgi:application --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 60 --access-logfile - --error-logfile -"]
