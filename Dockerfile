# syntax=docker/dockerfile:1.7

# ---- builder: install Python deps into a venv ----
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project


# ---- runtime: minimal image with the prebuilt venv ----
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# gosu lets the entrypoint drop privileges from root to the statview user after
# fixing ownership on any mounted volume (Railway, k8s, etc. mount volumes as root).
RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1000 statview \
 && useradd --system --uid 1000 --gid statview --create-home --home-dir /home/statview --shell /usr/sbin/nologin statview

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY app ./app
COPY alembic ./alembic
COPY scripts ./scripts
COPY alembic.ini ./

RUN chmod +x /app/scripts/entrypoint.sh \
 && mkdir -p /app/data \
 && chown -R statview:statview /app

# Container starts as root so the entrypoint can chown the data volume before
# dropping privileges. If a USER is set externally (e.g. docker-compose `user:`),
# the entrypoint detects it and skips the chown/drop step.

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
