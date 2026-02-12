#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

resolve_saved_db_path() {
  if [ -n "${SAVED_DB_PATH:-}" ]; then
    printf '%s\n' "$SAVED_DB_PATH"
    return
  fi

  data_dir="${STATVIEW_DATA_DIR:-${APP_DATA_DIR:-./data}}"
  db_file="${STATVIEW_DB_FILENAME:-statview.sqlite3}"
  printf '%s/%s\n' "${data_dir%/}" "$db_file"
}
