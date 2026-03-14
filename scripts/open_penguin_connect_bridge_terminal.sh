#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$REPO_DIR/scripts/run_penguin_connect_bridge.sh"
ALLOW_MISSING_GMAIL_STARTUP=0

if [ "${1:-}" = "--allow-missing-gmail-startup" ]; then
  ALLOW_MISSING_GMAIL_STARTUP=1
  shift
fi

if [ "$#" -ne 0 ]; then
  echo "Usage: $0 [--allow-missing-gmail-startup]"
  exit 1
fi

if [ ! -x "$RUNNER" ]; then
  echo "Runner not found or not executable: $RUNNER"
  exit 1
fi

# Build and escape the command for AppleScript.
if [ "$ALLOW_MISSING_GMAIL_STARTUP" = "1" ]; then
  CMD="cd \"$REPO_DIR\" && env PENGUIN_CONNECT_ALLOW_MISSING_GMAIL_STARTUP=1 \"$RUNNER\""
else
  CMD="cd \"$REPO_DIR\" && \"$RUNNER\""
fi
CMD_ESCAPED="${CMD//\\/\\\\}"
CMD_ESCAPED="${CMD_ESCAPED//\"/\\\"}"

/usr/bin/osascript <<OSA
tell application "Terminal"
  if not running then launch
  activate
  set targetTab to do script ""
  delay 0.2
  do script "$CMD_ESCAPED" in targetTab
end tell
OSA
