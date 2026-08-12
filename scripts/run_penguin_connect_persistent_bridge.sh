#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PENGUIN_CONNECT_PORT:-9000}"
HEALTH_URL="http://127.0.0.1:$PORT/penguin-connect/health"

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "[PenguinConnect] Invalid loopback bridge port: $PORT" >&2
  exit 2
fi

# Remote setup can run while the foreground Penguin app already owns the bridge.
# Wait without killing it; this launch agent takes ownership if that process exits.
announced_wait=0
while /usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; do
  if [ "$announced_wait" -eq 0 ]; then
    if /usr/bin/curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
      echo "[PenguinConnect] Existing healthy loopback bridge owns port $PORT; standing by."
    else
      echo "[PenguinConnect] Port $PORT is occupied; refusing to replace its listener and standing by." >&2
    fi
    announced_wait=1
  fi
  sleep 2
done

if [ -x "$ROOT_DIR/bin/uv" ] && [ -f "$ROOT_DIR/server/requirements.txt" ]; then
  exec "$ROOT_DIR/scripts/bootstrap_packaged_runtime.sh"
fi

exec "$ROOT_DIR/scripts/run_penguin_connect_bridge.sh"
