#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=tools/common.sh
. "$SCRIPT_DIR/common.sh"

uv run ruff check .
uv run ruff format --check .
