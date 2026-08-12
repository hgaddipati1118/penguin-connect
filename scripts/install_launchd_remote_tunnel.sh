#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

LABEL="com.penguinconnect.remote-tunnel"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
DATA_DIR="${PENGUIN_CONNECT_DATA_DIR:-$HOME/penguinconnect-local-bridge-data}"
LOG_DIR="$DATA_DIR/logs"
OUT_LOG="$LOG_DIR/remote-tunnel.out.log"
ERR_LOG="$LOG_DIR/remote-tunnel.err.log"
LAUNCHD_DOMAIN="gui/$(id -u)"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$ROOT_DIR/scripts/run_penguin_connect_mcp_cloudflare.sh"
PYTHON_BIN="${PENGUIN_CONNECT_PYTHON_BIN:-$ROOT_DIR/server/venv/bin/python}"
CLOUDFLARED_BIN="${PENGUIN_CONNECT_CLOUDFLARED_BIN:-$(command -v cloudflared || true)}"

if [ -z "$CLOUDFLARED_BIN" ] || [ ! -x "$CLOUDFLARED_BIN" ]; then
  echo "cloudflared is required. Install it with: brew install cloudflared" >&2
  exit 1
fi
if [ ! -x "$PYTHON_BIN" ]; then
  echo "Missing PenguinConnect Python at $PYTHON_BIN" >&2
  exit 1
fi
if [ ! -x "$RUNNER" ]; then
  echo "Missing tunnel runner at $RUNNER" >&2
  exit 1
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"
if [ -f "$OUT_LOG" ]; then
  mv "$OUT_LOG" "$OUT_LOG.previous"
fi
if [ -f "$ERR_LOG" ]; then
  mv "$ERR_LOG" "$ERR_LOG.previous"
fi

"$PYTHON_BIN" - \
  "$PLIST_PATH" "$LABEL" "$RUNNER" "$ROOT_DIR" "$OUT_LOG" "$ERR_LOG" \
  "$PYTHON_BIN" "$CLOUDFLARED_BIN" <<'PY'
import os
import plistlib
import sys

(
    plist_path,
    label,
    runner,
    root_dir,
    out_log,
    err_log,
    python_bin,
    cloudflared_bin,
) = sys.argv[1:]
payload = {
    "Label": label,
    "ProgramArguments": [runner],
    "WorkingDirectory": root_dir,
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "StandardOutPath": out_log,
    "StandardErrorPath": err_log,
    "EnvironmentVariables": {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PENGUIN_CONNECT_PYTHON_BIN": python_bin,
        "PENGUIN_CONNECT_CLOUDFLARED_BIN": cloudflared_bin,
    },
}
for key in (
    "PENGUIN_CONNECT_CLOUDFLARE_CONFIG",
    "PENGUIN_CONNECT_CLOUDFLARE_TUNNEL",
    "PENGUIN_CONNECT_DATA_DIR",
    "PENGUIN_CONNECT_MCP_PORT",
    "PENGUIN_CONNECT_PUBLIC_MCP_URL",
):
    if os.environ.get(key):
        payload["EnvironmentVariables"][key] = os.environ[key]
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

echo "Installed PenguinConnect HTTPS tunnel launch agent: $LABEL"
echo "Logs: $LOG_DIR/remote-tunnel.{out,err}.log"
