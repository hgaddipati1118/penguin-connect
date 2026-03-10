#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -x "$REPO_DIR/scripts/run_penguin_connect_bridge.sh" ]; then
  echo "Missing runner script: $REPO_DIR/scripts/run_penguin_connect_bridge.sh"
  exit 1
fi

exec "$REPO_DIR/scripts/run_penguin_connect_bridge.sh"
