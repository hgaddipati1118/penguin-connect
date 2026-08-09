#!/usr/bin/env bash
set -euo pipefail

LABEL="com.penguinconnect.remote-mcp"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
DATA_DIR="${PENGUIN_CONNECT_DATA_DIR:-$HOME/penguinconnect-local-bridge-data}"
LOG_DIR="$DATA_DIR/logs"
LAUNCHD_DOMAIN="gui/$(id -u)"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/server/venv/bin/python"
RUNNER="$ROOT_DIR/scripts/run_penguin_connect_remote_mcp.sh"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Missing virtualenv python at $PYTHON_BIN" >&2
  exit 1
fi

"$PYTHON_BIN" "$ROOT_DIR/scripts/penguin_connect_mcp_auth.py" --ensure

PORT="$($PYTHON_BIN - "$ROOT_DIR/.env" <<'PY'
import os
import sys
from dotenv import dotenv_values

configured = dotenv_values(sys.argv[1]) if os.path.exists(sys.argv[1]) else {}
print(os.environ.get("PENGUIN_CONNECT_MCP_PORT") or configured.get("PENGUIN_CONNECT_MCP_PORT") or "8765")
PY
)"

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

"$PYTHON_BIN" - "$PLIST_PATH" "$LABEL" "$RUNNER" "$ROOT_DIR" "$LOG_DIR" <<'PY'
import plistlib
import sys
from pathlib import Path

plist_path, label, runner, root_dir, log_dir = sys.argv[1:]
payload = {
    "Label": label,
    "ProgramArguments": [runner],
    "WorkingDirectory": root_dir,
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "StandardOutPath": str(Path(log_dir) / "remote-mcp.out.log"),
    "StandardErrorPath": str(Path(log_dir) / "remote-mcp.err.log"),
    "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
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

for _attempt in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "Installed authenticated remote MCP launch agent: $LABEL"
    echo "Local endpoint: http://127.0.0.1:$PORT/mcp"
    echo "Logs: $LOG_DIR/remote-mcp.{out,err}.log"
    exit 0
  fi
  sleep 1
done

echo "Installed $LABEL, but its health check did not become ready." >&2
echo "Inspect: $LOG_DIR/remote-mcp.err.log" >&2
exit 1
