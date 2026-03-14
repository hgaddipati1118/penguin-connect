#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$REPO_DIR/server"

load_env_defaults() {
  local env_file="$1"
  local line key

  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*)
        continue
        ;;
    esac

    if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)= ]]; then
      key="${BASH_REMATCH[1]}"
      if [ -z "${!key+x}" ]; then
        eval "export $line"
      fi
    fi
  done < "$env_file"
}

if [ -f "$REPO_DIR/.env" ]; then
  load_env_defaults "$REPO_DIR/.env"
fi

PORT="${PENGUIN_CONNECT_PORT:-9000}"
RESTART_ON_CRASH="${PENGUIN_CONNECT_RESTART_ON_CRASH:-1}"
PYTHON_BIN="$REPO_DIR/server/venv/bin/python"

if lsof -ti :"$PORT" >/dev/null 2>&1; then
  echo "[PenguinConnect] Port $PORT already in use; not starting duplicate server."
  exit 0
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "[PenguinConnect] Missing virtualenv python at $PYTHON_BIN"
  echo "[PenguinConnect] Run: cd \"$REPO_DIR/server\" && python3 -m venv venv && venv/bin/pip install -r requirements.txt"
  exit 1
fi

while true; do
  echo "[PenguinConnect] Running startup preflight..."
  if ! "$PYTHON_BIN" startup_checks.py; then
    exit 1
  fi

  "$PYTHON_BIN" -m uvicorn app:app --host 127.0.0.1 --port "$PORT"
  code=$?

  if [ "$code" -eq 130 ] || [ "$RESTART_ON_CRASH" != "1" ]; then
    exit "$code"
  fi

  echo "[PenguinConnect] Server exited ($code). Restarting in 2s..."
  sleep 2
done
