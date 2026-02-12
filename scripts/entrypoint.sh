#!/usr/bin/env sh
set -eu

MODE="${STATVIEW_MODE:-prod}"
HOST="${STATVIEW_HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
GUNICORN_THREADS="${GUNICORN_THREADS:-4}"

if [ "${RUN_DB_MIGRATIONS:-1}" = "1" ]; then
  uv run alembic upgrade head
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

case "$MODE" in
  prod|production)
    exec uv run gunicorn \
      -w "$GUNICORN_WORKERS" \
      -k gthread \
      --threads "$GUNICORN_THREADS" \
      -b "${HOST}:${PORT}" \
      "app.main:create_app()"
    ;;
  dev|development)
    exec uv run flask \
      --app app.main:create_app \
      run \
      --host "$HOST" \
      --port "$PORT" \
      --debug
    ;;
  *)
    echo "Unsupported STATVIEW_MODE: $MODE" >&2
    exit 1
    ;;
esac
