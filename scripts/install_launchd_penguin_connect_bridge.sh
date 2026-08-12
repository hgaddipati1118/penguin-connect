#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

LABEL="com.penguinconnect.local.bridge"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
DATA_DIR="${PENGUIN_CONNECT_DATA_DIR:-$HOME/penguinconnect-local-bridge-data}"
LOG_DIR="$DATA_DIR/logs"
LAUNCHD_DOMAIN="gui/$(id -u)"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$ROOT_DIR/scripts/run_penguin_connect_persistent_bridge.sh"
PYTHON_BIN="${PENGUIN_CONNECT_PYTHON_BIN:-}"

for candidate in \
  "${PENGUIN_CONNECT_RUNTIME_DIR:-$HOME/Library/Application Support/PenguinConnect/runtime}/venv/bin/python" \
  "$ROOT_DIR/server/venv/bin/python"; do
  if [ -z "$PYTHON_BIN" ] && [ -x "$candidate" ]; then
    PYTHON_BIN="$candidate"
  fi
done
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "Penguin's private Python runtime is not ready; open Penguin and finish setup first." >&2
  exit 1
fi

PORT="${PENGUIN_CONNECT_PORT:-}"
if [ -z "$PORT" ] && [ -f "$ROOT_DIR/.env" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^[[:space:]]*PENGUIN_CONNECT_PORT=([^#[:space:]]+) ]]; then
      PORT="${BASH_REMATCH[1]}"
      PORT="${PORT%\"}"
      PORT="${PORT#\"}"
      PORT="${PORT%\'}"
      PORT="${PORT#\'}"
      break
    fi
  done < "$ROOT_DIR/.env"
fi
PORT="${PORT:-9000}"
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "Invalid PENGUIN_CONNECT_PORT: $PORT" >&2
  exit 2
fi

APP_EXECUTABLE="${PENGUIN_CONNECT_APP_EXECUTABLE:-}"
for candidate in \
  "$ROOT_DIR/../../MacOS/Penguin" \
  "/Applications/Penguin.app/Contents/MacOS/Penguin" \
  "$ROOT_DIR/dist/Penguin.app/Contents/MacOS/Penguin"; do
  if [ -z "$APP_EXECUTABLE" ] && [ -x "$candidate" ]; then
    APP_EXECUTABLE="$candidate"
  fi
done
if [ -z "$APP_EXECUTABLE" ] || [ ! -x "$APP_EXECUTABLE" ]; then
  echo "Penguin's app executable is missing; install and open Penguin before enabling remote access." >&2
  exit 1
fi
if [ ! -x "$RUNNER" ]; then
  echo "Persistent bridge runner is missing: $RUNNER" >&2
  exit 1
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR" "$DATA_DIR"

"$PYTHON_BIN" - "$PLIST_PATH" "$LABEL" "$APP_EXECUTABLE" "$ROOT_DIR" "$LOG_DIR" "$PORT" <<'PY'
import os
import plistlib
import sys
from pathlib import Path

plist_path, label, app_executable, root_dir, log_dir, port = sys.argv[1:]
environment = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "PENGUIN_CONNECT_PORT": port,
}
for key in (
    "PENGUIN_CONNECT_ALLOW_MISSING_GMAIL_STARTUP",
    "PENGUIN_CONNECT_CONTACTS_HELPER_BIN",
    "PENGUIN_CONNECT_DATA_DIR",
    "PENGUIN_CONNECT_RUNTIME_DIR",
    "PENGUIN_CONNECT_WHATSAPP_API_URL",
    "PENGUIN_CONNECT_WHATSAPP_BRIDGE_BIN",
    "PENGUIN_CONNECT_WHATSAPP_BRIDGE_DIR",
    "PENGUIN_CONNECT_WHATSAPP_DB_PATH",
):
    if os.environ.get(key):
        environment[key] = os.environ[key]

payload = {
    "Label": label,
    "AssociatedBundleIdentifiers": ["com.penguinconnect.desktop"],
    "ProgramArguments": [app_executable, "--bridge-agent"],
    "WorkingDirectory": root_dir,
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "ProcessType": "Background",
    "StandardOutPath": str(Path(log_dir) / "penguinconnect-bridge.out.log"),
    "StandardErrorPath": str(Path(log_dir) / "penguinconnect-bridge.err.log"),
    "EnvironmentVariables": environment,
}
with open(plist_path, "wb") as handle:
    plistlib.dump(payload, handle)
PY

launchctl bootout "$LAUNCHD_DOMAIN/$LABEL" >/dev/null 2>&1 || true
for _attempt in $(seq 1 20); do
  if ! launchctl print "$LAUNCHD_DOMAIN/$LABEL" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
launchctl enable "$LAUNCHD_DOMAIN/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$LAUNCHD_DOMAIN" "$PLIST_PATH"

for _attempt in $(seq 1 180); do
  if /usr/bin/curl -fsS --max-time 2 "http://127.0.0.1:$PORT/penguin-connect/health" >/dev/null 2>&1; then
    echo "Installed persistent local bridge: $LABEL"
    echo "Loopback API: http://127.0.0.1:$PORT"
    echo "Logs: $LOG_DIR/penguinconnect-bridge.{out,err}.log"
    exit 0
  fi
  sleep 1
done

echo "Installed $LABEL, but its loopback health check did not become ready." >&2
echo "Open Penguin and verify Full Disk Access, then inspect $LOG_DIR/penguinconnect-bridge.err.log" >&2
exit 1
