#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/server/venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

echo "[check] backend compile"
"$PYTHON_BIN" -m py_compile \
  "$ROOT_DIR/server/app.py" \
  "$ROOT_DIR/server/db.py" \
  "$ROOT_DIR/server/watcher.py" \
  "$ROOT_DIR/server/browse_sources.py" \
  "$ROOT_DIR/server/penguin_connect.py" \
  "$ROOT_DIR/scripts/penguin_connect_setup.py" \
  "$ROOT_DIR/scripts/penguin_connect_connect.py" \
  "$ROOT_DIR/scripts/penguin_connect_doctor.py" \
  "$ROOT_DIR/scripts/penguinconnect_create_inbox.py" \
  "$ROOT_DIR/scripts/import_contacts.py"

echo "[check] backend tests"
(
  cd "$ROOT_DIR/server"
  "$PYTHON_BIN" -m unittest -v
)

echo "[check] shell script syntax"
bash -n "$ROOT_DIR/start.sh"
bash -n "$ROOT_DIR/scripts/run_penguin_connect_bridge.sh"
bash -n "$ROOT_DIR/scripts/open_penguin_connect_bridge_terminal.sh"
bash -n "$ROOT_DIR/scripts/install_launchd_penguinconnect_bridge.sh"

echo "[check] done"
