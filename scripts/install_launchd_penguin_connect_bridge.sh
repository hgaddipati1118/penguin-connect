#!/bin/bash
set -euo pipefail

LABEL="com.penguinconnect.local.bridge"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
DATA_DIR="${PENGUIN_CONNECT_DATA_DIR:-$HOME/penguinconnect-local-bridge-data}"
LOG_DIR="$DATA_DIR/logs"
WATCHDOG_WRAPPER="$DATA_DIR/bridge-watchdog.sh"
WATCHDOG_INTERVAL_SECONDS=300

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LEGACY_WATCHDOG_PLIST_PATH="$LAUNCH_AGENTS_DIR/com.penguinconnect.local.bridge.watchdog.plist"
LAUNCHD_DOMAIN="gui/$(id -u)"

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

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR" "$DATA_DIR"

cat > "$WATCHDOG_WRAPPER" <<'WATCHDOG'
#!/bin/bash
set -euo pipefail

PORT="__PORT__"
REPO_DIR="__REPO_DIR__"
RUNNER="$REPO_DIR/scripts/run_penguin_connect_bridge.sh"

if lsof -ti :"$PORT" >/dev/null 2>&1; then
  echo "[watchdog] bridge already present on 127.0.0.1:$PORT"
  exit 0
fi

CMD="cd \"$REPO_DIR\" && \"$RUNNER\""
CMD_ESCAPED="${CMD//\\/\\\\}"
CMD_ESCAPED="${CMD_ESCAPED//\"/\\\"}"

/usr/bin/osascript <<OSA
tell application "Terminal"
  if not running then launch
  activate
  do script "$CMD_ESCAPED"
end tell
OSA

echo "[watchdog] bridge missing on 127.0.0.1:$PORT; launched Terminal starter"
WATCHDOG

/usr/bin/python3 - <<PY
from pathlib import Path
path = Path("$WATCHDOG_WRAPPER")
text = path.read_text(encoding="utf-8")
text = text.replace("__PORT__", "$PORT").replace("__REPO_DIR__", "$REPO_DIR")
path.write_text(text, encoding="utf-8")
PY

chmod +x "$WATCHDOG_WRAPPER"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$WATCHDOG_WRAPPER</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>StartInterval</key>
  <integer>$WATCHDOG_INTERVAL_SECONDS</integer>

  <key>KeepAlive</key>
  <false/>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/penguinconnect-bridge.out.log</string>

  <key>StandardErrorPath</key>
  <string>$LOG_DIR/penguinconnect-bridge.err.log</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
</dict>
</plist>
PLIST

launchctl bootout "$LAUNCHD_DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootout "$LAUNCHD_DOMAIN" "$LEGACY_WATCHDOG_PLIST_PATH" >/dev/null 2>&1 || launchctl unload "$LEGACY_WATCHDOG_PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "$LAUNCHD_DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || launchctl load "$PLIST_PATH"

if [ -f "$LEGACY_WATCHDOG_PLIST_PATH" ]; then
  rm -f "$LEGACY_WATCHDOG_PLIST_PATH"
fi

echo "Installed launchd watchdog agent: $LABEL"
echo "Plist: $PLIST_PATH"
echo "Wrapper: $WATCHDOG_WRAPPER"
echo "Logs:"
echo "- $LOG_DIR/penguinconnect-bridge.out.log"
echo "- $LOG_DIR/penguinconnect-bridge.err.log"
echo "The watchdog checks every $WATCHDOG_INTERVAL_SECONDS seconds and at login."
echo "It only starts a missing bridge in Terminal.app and never kills a running one."
echo "If you change PENGUIN_CONNECT_PORT later, rerun this installer so the watchdog uses the new port."
