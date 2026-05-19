#!/usr/bin/env sh
set -eu

MODE="${STATVIEW_MODE:-prod}"
HOST="${STATVIEW_HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
GUNICORN_THREADS="${GUNICORN_THREADS:-4}"
DATA_DIR="${STATVIEW_DATA_DIR:-/app/data}"

# Platforms like Railway mount persistent volumes as root, so a non-root app
# cannot write to them. When we boot as root, take ownership of the data
# directory and then re-exec this script as the statview user.
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown -R statview:statview "$DATA_DIR"
  exec gosu statview:statview "$0" "$@"
fi

if [ "${RUN_DB_MIGRATIONS:-1}" = "1" ]; then
  python -c "from app.config import Settings; from app.db import migrate_database; migrate_database(Settings().saved_db_path)"
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

case "$MODE" in
  prod|production)
    exec gunicorn \
      -w "$GUNICORN_WORKERS" \
      -k gthread \
      --threads "$GUNICORN_THREADS" \
      -b "${HOST}:${PORT}" \
      "app.main:create_app()"
    ;;
  dev|development)
    exec flask \
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
