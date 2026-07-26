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

mkdir -p "$DIST_DIR"
if [ -d "$APP_BUNDLE" ]; then
  rm -rf "$APP_BUNDLE"
fi
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

cp "$REPO_DIR/desktop/Info.plist" "$CONTENTS_DIR/Info.plist"
cp "$ICON_SOURCE" "$RESOURCES_DIR/PenguinIcon.png"
/usr/libexec/PlistBuddy -c "Set :PenguinRepoPath $REPO_DIR" "$CONTENTS_DIR/Info.plist"

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

codesign --force --deep --sign - "$APP_BUNDLE"
echo "[Penguin] Built $APP_BUNDLE"

if [ "${1:-}" = "--install" ]; then
  if [ -d "$INSTALL_TARGET" ]; then
    TRASH_TARGET="$HOME/.Trash/Penguin-$(date +%Y%m%d-%H%M%S).app"
    mv "$INSTALL_TARGET" "$TRASH_TARGET"
    echo "[Penguin] Moved the previous app to $TRASH_TARGET"
  fi
  ditto "$APP_BUNDLE" "$INSTALL_TARGET"
  echo "[Penguin] Installed $INSTALL_TARGET"
fi
