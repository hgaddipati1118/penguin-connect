#!/bin/bash
set -euo pipefail

LABEL="com.penguinconnect.local.bridge"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
LOG_DIR="${PENGUIN_CONNECT_DATA_DIR:-$HOME/penguinconnect-local-bridge-data}/logs"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TERMINAL_LAUNCHER="$REPO_DIR/scripts/open_penguin_connect_bridge_terminal.sh"

if [ ! -x "$TERMINAL_LAUNCHER" ]; then
  echo "Terminal launcher not found or not executable: $TERMINAL_LAUNCHER"
  exit 1
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$TERMINAL_LAUNCHER</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

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

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"
launchctl start "$LABEL" || true

echo "Installed launchd agent: $LABEL"
echo "Plist: $PLIST_PATH"
echo "Logs:  $LOG_DIR/penguinconnect-bridge.out.log"
echo "Terminal.app will open and run the bridge command at login."
