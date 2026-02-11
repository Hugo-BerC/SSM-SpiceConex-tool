#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
APP_NAME="SSM-PowerConnect"
ENTRY_POINT="MacOs/AWSPWRCNv4.1.py"
ICON_PNG="icon_converted.png"
ICON_ICNS="build/${APP_NAME}.icns"

if [[ -f "$ICON_PNG" ]]; then
  ICONSET_DIR="build/icon.iconset"
  rm -rf "$ICONSET_DIR"
  mkdir -p "$ICONSET_DIR"
  sips -s format png -z 16 16 "$ICON_PNG" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
  sips -s format png -z 32 32 "$ICON_PNG" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
  sips -s format png -z 32 32 "$ICON_PNG" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
  sips -s format png -z 64 64 "$ICON_PNG" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
  sips -s format png -z 128 128 "$ICON_PNG" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
  sips -s format png -z 256 256 "$ICON_PNG" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
  sips -s format png -z 256 256 "$ICON_PNG" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
  sips -s format png -z 512 512 "$ICON_PNG" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
  sips -s format png -z 512 512 "$ICON_PNG" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
  sips -s format png -z 1024 1024 "$ICON_PNG" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null
  iconutil -c icns "$ICONSET_DIR" -o "$ICON_ICNS"
  rm -rf "$ICONSET_DIR"
fi

PYINSTALLER_ICON_ARGS=()
if [[ -f "$ICON_ICNS" ]]; then
  PYINSTALLER_ICON_ARGS+=(--icon "$ICON_ICNS")
fi

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --add-data "MacOs/skin.jpg:." \
  "${PYINSTALLER_ICON_ARGS[@]}" \
  "$ENTRY_POINT"

DMG_PATH="dist/${APP_NAME}.dmg"

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "dist/${APP_NAME}.app" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

if [[ -f "$ICON_ICNS" ]] && command -v SetFile >/dev/null 2>&1; then
  DMG_MOUNT_DIR="/Volumes/${APP_NAME}"
  hdiutil attach "$DMG_PATH" -nobrowse -quiet
  cp "$ICON_ICNS" "$DMG_MOUNT_DIR/.VolumeIcon.icns"
  SetFile -a C "$DMG_MOUNT_DIR"
  hdiutil detach "$DMG_MOUNT_DIR" -quiet
fi

echo "Build complete: dist/${APP_NAME}.app"
echo "DMG created: ${DMG_PATH}"
