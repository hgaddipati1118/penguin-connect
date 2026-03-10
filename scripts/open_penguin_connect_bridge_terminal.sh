#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$REPO_DIR/scripts/run_penguin_connect_bridge.sh"

if [ ! -x "$RUNNER" ]; then
  echo "Runner not found or not executable: $RUNNER"
  exit 1
fi

# Build and escape the command for AppleScript.
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
