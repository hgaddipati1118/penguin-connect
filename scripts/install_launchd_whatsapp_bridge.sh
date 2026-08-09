#!/usr/bin/env bash
set -euo pipefail

LABEL="com.penguinconnect.whatsapp-bridge"
LAUNCHD_DOMAIN="gui/$(id -u)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
BRIDGE_DIR="${PENGUIN_CONNECT_WHATSAPP_BRIDGE_DIR:-$HOME/whatsapp-mcp/whatsapp-bridge}"
BRIDGE_BIN="$BRIDGE_DIR/whatsapp-bridge"
DATA_DIR="${PENGUIN_CONNECT_DATA_DIR:-$HOME/penguinconnect-local-bridge-data}"
LOG_DIR="$DATA_DIR/logs"

if [ ! -x "$BRIDGE_BIN" ]; then
  echo "Missing WhatsApp bridge binary at $BRIDGE_BIN" >&2
  echo "Build it first: cd \"$BRIDGE_DIR\" && go build -o whatsapp-bridge ." >&2
  exit 1
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

/usr/bin/python3 - "$PLIST_PATH" "$LABEL" "$BRIDGE_BIN" "$BRIDGE_DIR" "$LOG_DIR" <<'PY'
import plistlib
import sys
from pathlib import Path

plist_path, label, bridge_bin, bridge_dir, log_dir = sys.argv[1:]
payload = {
    "Label": label,
    "ProgramArguments": [bridge_bin],
    "WorkingDirectory": bridge_dir,
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "ProcessType": "Background",
    "StandardOutPath": str(Path(log_dir) / "whatsapp-bridge.out.log"),
    "StandardErrorPath": str(Path(log_dir) / "whatsapp-bridge.err.log"),
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

for _attempt in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:8080/api/capabilities" >/dev/null 2>&1; then
    echo "Installed loopback-only WhatsApp bridge launch agent: $LABEL"
    echo "Local API: http://127.0.0.1:8080/api"
    echo "Logs: $LOG_DIR/whatsapp-bridge.{out,err}.log"
    exit 0
  fi
  sleep 0.5
done

echo "Installed $LABEL, but its loopback health check did not become ready." >&2
echo "Inspect: $LOG_DIR/whatsapp-bridge.err.log" >&2
exit 1
