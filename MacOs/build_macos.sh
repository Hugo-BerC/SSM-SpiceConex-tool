#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
APP_NAME="SSM-PowerConnect"
ENTRY_POINT="MacOs/AWSPWRCNv4.1.py"

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --add-data "MacOs/skin.jpg:." \
  "$ENTRY_POINT"

DMG_PATH="dist/${APP_NAME}.dmg"

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "dist/${APP_NAME}.app" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Build complete: dist/${APP_NAME}.app"
echo "DMG created: ${DMG_PATH}"
