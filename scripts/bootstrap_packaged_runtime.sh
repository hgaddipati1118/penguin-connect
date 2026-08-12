#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="${PENGUIN_CONNECT_RUNTIME_DIR:-$HOME/Library/Application Support/PenguinConnect/runtime}"
VENV_DIR="$RUNTIME_DIR/venv"
PYTHON_BIN="$VENV_DIR/bin/python"
UV_BIN="$REPO_DIR/bin/uv"
REQUIREMENTS="$REPO_DIR/server/requirements.txt"
STAMP="$RUNTIME_DIR/requirements.txt"

if [ ! -x "$UV_BIN" ]; then
  echo "[PenguinConnect] The packaged Python installer is missing." >&2
  exit 1
fi
if [ ! -f "$REQUIREMENTS" ]; then
  echo "[PenguinConnect] Packaged server requirements are missing." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR"
if [ ! -x "$PYTHON_BIN" ] || [ ! -f "$STAMP" ] || ! cmp -s "$REQUIREMENTS" "$STAMP"; then
  echo "[PenguinConnect] Preparing the private app runtime..." >&2
  "$UV_BIN" venv "$VENV_DIR" --python 3.13
  "$UV_BIN" pip install --python "$PYTHON_BIN" --requirement "$REQUIREMENTS"
  cp "$REQUIREMENTS" "$STAMP"
  chmod 600 "$STAMP"
fi

export PENGUIN_CONNECT_PYTHON_BIN="$PYTHON_BIN"
export PENGUIN_CONNECT_CLOUDFLARED_BIN="$REPO_DIR/bin/cloudflared"
export PENGUIN_CONNECT_WHATSAPP_BRIDGE_BIN="$REPO_DIR/bin/whatsapp-bridge"
export PENGUIN_CONNECT_CONTACTS_HELPER_BIN="$REPO_DIR/../../Helpers/PenguinContactsHelper"
export PENGUIN_CONNECT_WHATSAPP_BRIDGE_DIR="${PENGUIN_CONNECT_WHATSAPP_BRIDGE_DIR:-$HOME/Library/Application Support/PenguinConnect/whatsapp-bridge}"
export PENGUIN_CONNECT_WHATSAPP_DB_PATH="${PENGUIN_CONNECT_WHATSAPP_DB_PATH:-$PENGUIN_CONNECT_WHATSAPP_BRIDGE_DIR/store/messages.db}"

exec "$REPO_DIR/scripts/run_penguin_connect_bridge.sh"
