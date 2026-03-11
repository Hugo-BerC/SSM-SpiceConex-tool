#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
APP_NAME="SSM-PowerConnect"
APP_BUNDLE_ID="com.iberia.ssm-powerconnect"
ENTRY_POINT="MacOs/AWSPWRCNv4.1.py"
ICON_PNG="icon.png"
DMG_PATH="MacOs/${APP_NAME}.dmg"
TMP_DIR="$(mktemp -d)"
ICON_ICNS="${TMP_DIR}/${APP_NAME}.icns"
SKIN_PATH="${ROOT_DIR}/MacOs/skin.jpg"
APP_PATH="${TMP_DIR}/dist/${APP_NAME}.app"
APP_PLIST="${APP_PATH}/Contents/Info.plist"
PYINSTALLER_ICON_ARGS=()

cleanup() {
  rm -rf "$TMP_DIR" build dist 2>/dev/null || true
}
trap cleanup EXIT

if [[ -f "$ICON_PNG" ]]; then
  ICONSET_DIR="$TMP_DIR/icon.iconset"
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

if [[ -f "$ICON_ICNS" ]]; then
  PYINSTALLER_ICON_ARGS+=(--icon "$ICON_ICNS")
fi

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "$APP_BUNDLE_ID" \
  "${PYINSTALLER_ICON_ARGS[@]+${PYINSTALLER_ICON_ARGS[@]}}" \
  --distpath "$TMP_DIR/dist" \
  --workpath "$TMP_DIR/build" \
  --specpath "$TMP_DIR/spec" \
  --add-data "${SKIN_PATH}:." \
  "$ENTRY_POINT"

/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $APP_BUNDLE_ID" "$APP_PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $APP_NAME" "$APP_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 1" "$APP_PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion 1" "$APP_PLIST"
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string SSM-PowerConnect needs access to Terminal and System Events to open AWS SSM sessions in new Terminal tabs." "$APP_PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :NSAppleEventsUsageDescription SSM-PowerConnect needs access to Terminal and System Events to open AWS SSM sessions in new Terminal tabs." "$APP_PLIST"
codesign --force --deep --sign - "$APP_PATH"

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$APP_PATH" \
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

echo "DMG created: ${DMG_PATH}"
