#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=tools/common.sh
. "$SCRIPT_DIR/common.sh"

DB_PATH=$(resolve_saved_db_path)
DB_DIR=$(dirname "$DB_PATH")
mkdir -p "$DB_DIR"

if [ ! -f "$DB_PATH" ]; then
  uv run alembic upgrade head
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 is required but not installed." >&2
  exit 1
fi

if [ "$#" -gt 0 ]; then
  exec sqlite3 "$DB_PATH" "$@"
fi

exec sqlite3 "$DB_PATH"
