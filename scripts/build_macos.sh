#!/usr/bin/env bash
# Build BlueLock.app (menu bar app with the lock monitor built in) plus zip/dmg.
set -euo pipefail

VERSION="${1:-1.0.0}"
cd "$(dirname "$0")/.."

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "build_macos.sh must run on macOS." >&2
  exit 1
fi

python3 -m venv .venv-macos
.venv-macos/bin/python -m pip install --upgrade pip
.venv-macos/bin/python -m pip install -r requirements-macos.txt pyinstaller

.venv-macos/bin/pyinstaller --noconfirm --clean --windowed \
  --name BlueLock \
  --osx-bundle-identifier com.bluelock.tray \
  tray_mac.py

APP="dist/BlueLock.app"
PLIST="$APP/Contents/Info.plist"
# Menu bar only (no Dock icon).
plutil -replace LSUIElement -bool true "$PLIST"
# Without this key macOS kills the app the first time it touches CoreBluetooth.
plutil -replace NSBluetoothAlwaysUsageDescription -string \
  "BlueLock watches your device's Bluetooth signal to lock this Mac when you walk away." "$PLIST"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$VERSION" "$PLIST"

# Editing Info.plist invalidates PyInstaller's signature; Apple Silicon refuses to
# launch unsigned code, so re-sign ad hoc.
codesign --force --deep --sign - "$APP"

ditto -c -k --keepParent "$APP" "dist/BlueLock-$VERSION-macos.zip"
hdiutil create -volname BlueLock -srcfolder "$APP" -ov -format UDZO "dist/BlueLock-$VERSION-macos.dmg"

echo "Built $APP, dist/BlueLock-$VERSION-macos.zip and dist/BlueLock-$VERSION-macos.dmg"
