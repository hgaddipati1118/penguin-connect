#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PENGUIN_CONNECT_PYTHON_BIN:-$ROOT_DIR/server/venv/bin/python}"
CLOUDFLARED_BIN="${PENGUIN_CONNECT_CLOUDFLARED_BIN:-$(command -v cloudflared || true)}"

if [ -z "$CLOUDFLARED_BIN" ] || [ ! -x "$CLOUDFLARED_BIN" ]; then
  echo "cloudflared is not installed. Run: brew install cloudflared" >&2
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Missing virtualenv python at $PYTHON_BIN" >&2
  exit 1
fi

dotenv_value() {
  "$PYTHON_BIN" - "$ROOT_DIR/.env" "$1" <<'PY'
import os
import sys
from dotenv import dotenv_values

configured = dotenv_values(sys.argv[1]) if os.path.exists(sys.argv[1]) else {}
value = os.environ.get(sys.argv[2]) or configured.get(sys.argv[2]) or ""
print(value)
PY
}

PORT="$(dotenv_value PENGUIN_CONNECT_MCP_PORT)"
PORT="${PORT:-8765}"
CLOUDFLARE_TUNNEL="$(dotenv_value PENGUIN_CONNECT_CLOUDFLARE_TUNNEL)"
CLOUDFLARE_CONFIG="$(dotenv_value PENGUIN_CONNECT_CLOUDFLARE_CONFIG)"
if [ -n "$CLOUDFLARE_CONFIG" ]; then
  CLOUDFLARE_CONFIG="$($PYTHON_BIN - "$CLOUDFLARE_CONFIG" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).expanduser())
PY
)"
fi

if ! curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null; then
  echo "Remote MCP is not healthy on 127.0.0.1:$PORT." >&2
  echo "Start it first: ./scripts/install_launchd_remote_mcp.sh" >&2
  exit 1
fi

if [ -n "$CLOUDFLARE_TUNNEL" ]; then
  config_args=()
  if [ -n "$CLOUDFLARE_CONFIG" ]; then
    config_args=(--config "$CLOUDFLARE_CONFIG")
  fi
  exec "$CLOUDFLARED_BIN" tunnel \
    "${config_args[@]}" \
    run "$CLOUDFLARE_TUNNEL"
fi

echo "Starting a temporary TryCloudflare endpoint for development." >&2
echo "Use a named tunnel for a stable production hostname." >&2
exec "$CLOUDFLARED_BIN" tunnel \
  --url "http://127.0.0.1:$PORT" \
  --http-host-header "127.0.0.1:$PORT"
