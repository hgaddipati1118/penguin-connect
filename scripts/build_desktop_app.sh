#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$REPO_DIR/dist"
APP_BUNDLE="$DIST_DIR/Penguin.app"
CONTENTS_DIR="$APP_BUNDLE/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
ICON_SOURCE="$REPO_DIR/desktop/Assets/PenguinIcon.png"
INSTALL_TARGET="/Applications/Penguin.app"
RELEASE_BUILD=0
INSTALL_BUILD=0

for argument in "$@"; do
  case "$argument" in
    --release)
      RELEASE_BUILD=1
      ;;
    --install)
      INSTALL_BUILD=1
      ;;
    *)
      echo "Unknown argument: $argument" >&2
      echo "Usage: $0 [--release] [--install]" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$DIST_DIR"
if [ -d "$APP_BUNDLE" ]; then
  rm -rf "$APP_BUNDLE"
fi
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

cp "$REPO_DIR/desktop/Info.plist" "$CONTENTS_DIR/Info.plist"
cp "$ICON_SOURCE" "$RESOURCES_DIR/PenguinIcon.png"

if [ "$RELEASE_BUILD" -eq 1 ]; then
  PACKAGED_ROOT="$RESOURCES_DIR/PenguinConnect"
  UV_SOURCE="${PENGUIN_CONNECT_UV_BIN:-$(command -v uv || true)}"
  CLOUDFLARED_SOURCE="${PENGUIN_CONNECT_CLOUDFLARED_BIN:-$(command -v cloudflared || true)}"
  WHATSAPP_SOURCE="${PENGUIN_CONNECT_WHATSAPP_BRIDGE_BIN:-$HOME/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge}"
  for required in "$UV_SOURCE" "$CLOUDFLARED_SOURCE" "$WHATSAPP_SOURCE"; do
    if [ -z "$required" ] || [ ! -x "$required" ]; then
      echo "Missing release dependency: ${required:-unconfigured}" >&2
      echo "Set PENGUIN_CONNECT_UV_BIN, PENGUIN_CONNECT_CLOUDFLARED_BIN, or PENGUIN_CONNECT_WHATSAPP_BRIDGE_BIN." >&2
      exit 1
    fi
  done

  mkdir -p "$PACKAGED_ROOT/bin" "$PACKAGED_ROOT/server" "$PACKAGED_ROOT/scripts"
  rsync -a \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude 'test_*.py' \
    "$REPO_DIR/server/" "$PACKAGED_ROOT/server/"
  rsync -a \
    --exclude '__pycache__/' \
    --exclude 'install_launchd_headless_bridge.sh' \
    "$REPO_DIR/scripts/" "$PACKAGED_ROOT/scripts/"
  SENSITIVE_SERVER_FILE="$(find "$PACKAGED_ROOT/server" -type f \( \
    -name '.env' -o -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \
    -o -name '*credentials*.json' -o -name '*token*.json' \
  \) -print -quit)"
  if [ -n "$SENSITIVE_SERVER_FILE" ]; then
    echo "Refusing to package sensitive runtime file: $SENSITIVE_SERVER_FILE" >&2
    exit 1
  fi
  cp "$REPO_DIR/.env.example" "$PACKAGED_ROOT/.env.example"
  cp -L "$UV_SOURCE" "$PACKAGED_ROOT/bin/uv"
  cp -L "$CLOUDFLARED_SOURCE" "$PACKAGED_ROOT/bin/cloudflared"
  cp -L "$WHATSAPP_SOURCE" "$PACKAGED_ROOT/bin/whatsapp-bridge"
  chmod 755 "$PACKAGED_ROOT/bin/uv" "$PACKAGED_ROOT/bin/cloudflared" "$PACKAGED_ROOT/bin/whatsapp-bridge"
  /usr/libexec/PlistBuddy -c "Set :PenguinRepoPath __BUNDLED__" "$CONTENTS_DIR/Info.plist"
else
  /usr/libexec/PlistBuddy -c "Set :PenguinRepoPath $REPO_DIR" "$CONTENTS_DIR/Info.plist"
fi

ICONSET_DIR="$(mktemp -d)/PenguinIcon.iconset"
mkdir -p "$ICONSET_DIR"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON_SOURCE" --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
  double_size=$((size * 2))
  sips -z "$double_size" "$double_size" "$ICON_SOURCE" --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET_DIR" -o "$RESOURCES_DIR/PenguinIcon.icns"

swiftc \
  -O \
  -parse-as-library \
  -framework Cocoa \
  -framework WebKit \
  "$REPO_DIR/desktop/PenguinDesktopSupport.swift" \
  "$REPO_DIR/desktop/PenguinApp.swift" \
  -o "$MACOS_DIR/Penguin"

SIGNING_IDENTITY="${PENGUIN_CONNECT_CODESIGN_IDENTITY:--}"
codesign --force --deep --options runtime --sign "$SIGNING_IDENTITY" "$APP_BUNDLE"
echo "[Penguin] Built $APP_BUNDLE"
if [ "$RELEASE_BUILD" -eq 1 ]; then
  if [ "$SIGNING_IDENTITY" = "-" ]; then
    echo "[Penguin] Release bundle is ad-hoc signed for local testing only."
    echo "[Penguin] Set PENGUIN_CONNECT_CODESIGN_IDENTITY and notarize before public distribution."
  else
    echo "[Penguin] Signed with $SIGNING_IDENTITY; notarization is still required for distribution."
  fi
fi

if [ "$INSTALL_BUILD" -eq 1 ]; then
  if [ -d "$INSTALL_TARGET" ]; then
    TRASH_TARGET="$HOME/.Trash/Penguin-$(date +%Y%m%d-%H%M%S).app"
    mv "$INSTALL_TARGET" "$TRASH_TARGET"
    echo "[Penguin] Moved the previous app to $TRASH_TARGET"
  fi
  ditto "$APP_BUNDLE" "$INSTALL_TARGET"
  echo "[Penguin] Installed $INSTALL_TARGET"
fi
