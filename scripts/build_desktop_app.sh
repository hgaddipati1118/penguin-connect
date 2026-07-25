#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$REPO_DIR/dist"
APP_BUNDLE="$DIST_DIR/Penguin.app"
CONTENTS_DIR="$APP_BUNDLE/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
INSTALL_TARGET="/Applications/Penguin.app"

mkdir -p "$DIST_DIR"
if [ -d "$APP_BUNDLE" ]; then
  rm -rf "$APP_BUNDLE"
fi
mkdir -p "$MACOS_DIR"

cp "$REPO_DIR/desktop/Info.plist" "$CONTENTS_DIR/Info.plist"
/usr/libexec/PlistBuddy -c "Set :PenguinRepoPath $REPO_DIR" "$CONTENTS_DIR/Info.plist"

swiftc \
  -O \
  -parse-as-library \
  -framework Cocoa \
  -framework WebKit \
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
