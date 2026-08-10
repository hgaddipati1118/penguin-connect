#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PENGUIN_CONNECT_PYTHON_BIN:-$ROOT_DIR/server/venv/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Missing virtualenv python at $PYTHON_BIN" >&2
  echo "Run: cd \"$ROOT_DIR/server\" && python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

exec "$PYTHON_BIN" \
  "$ROOT_DIR/scripts/penguin_connect_mcp.py" \
  --transport streamable-http \
  "$@"
