#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/server/venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "python3 not found"
  exit 1
fi

if ! command -v rg >/dev/null 2>&1; then
  echo "rg is required to run this check"
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "node is required to run frontend checks"
  exit 1
fi

PYTHON_SOURCES=()
while IFS= read -r file; do
  PYTHON_SOURCES+=("$file")
done < <(
  cd "$ROOT_DIR"
  rg --files server scripts -g '*.py' | rg -v '(^|/)(venv|__pycache__)/'
)

SHELL_SOURCES=()
while IFS= read -r file; do
  SHELL_SOURCES+=("$file")
done < <(
  cd "$ROOT_DIR"
  {
    printf '%s\n' "start.sh"
    rg --files scripts -g '*.sh'
  } | sort -u
)

if [ "${#PYTHON_SOURCES[@]}" -eq 0 ]; then
  echo "No Python sources found under server/ or scripts/"
  exit 1
fi

echo "[check] backend compile"
(
  cd "$ROOT_DIR"
  "$PYTHON_BIN" -m py_compile "${PYTHON_SOURCES[@]}"
)

echo "[check] backend tests"
(
  cd "$ROOT_DIR/server"
  "$PYTHON_BIN" -m unittest -v
)

echo "[check] frontend syntax and unit tests"
node --check "$ROOT_DIR/server/ui/inbox.js"
node --check "$ROOT_DIR/server/ui/agent-history.js"
node --check "$ROOT_DIR/server/ui/thread-layout.js"
node --check "$ROOT_DIR/server/ui/refresh-coordinator.js"
node --check "$ROOT_DIR/server/ui/composer-mentions.js"
node --check "$ROOT_DIR/server/ui/conversation-navigation.js"
node --check "$ROOT_DIR/server/ui/list-windowing.js"
node --check "$ROOT_DIR/server/ui/media-preview-queue.js"
node --test "$ROOT_DIR/server/ui/"*.test.cjs

if [ "$(uname -s)" = "Darwin" ] && command -v swiftc >/dev/null 2>&1; then
  echo "[check] macOS Vision OCR helper"
  swiftc -typecheck "$ROOT_DIR/server/macos_vision_ocr.swift"

  echo "[check] macOS desktop shell"
  DESKTOP_CHECK_DIR="$(mktemp -d)"
  trap 'rm -rf "$DESKTOP_CHECK_DIR"' EXIT
  swiftc \
    "$ROOT_DIR/desktop/PenguinDesktopSupport.swift" \
    "$ROOT_DIR/desktop/PenguinOnboarding.swift" \
    "$ROOT_DIR/desktop/PenguinDesktopSupportTests.swift" \
    -o "$DESKTOP_CHECK_DIR/PenguinDesktopSupportTests"
  "$DESKTOP_CHECK_DIR/PenguinDesktopSupportTests"
  swiftc \
    -typecheck \
    -parse-as-library \
    -framework Cocoa \
    -framework Contacts \
    -framework WebKit \
    "$ROOT_DIR/desktop/PenguinDesktopSupport.swift" \
    "$ROOT_DIR/desktop/PenguinOnboarding.swift" \
    "$ROOT_DIR/desktop/PenguinApp.swift"
fi

echo "[check] shell script syntax"
for script in "${SHELL_SOURCES[@]}"; do
  bash -n "$ROOT_DIR/$script"
done

echo "[check] done"
