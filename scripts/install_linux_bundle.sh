#!/usr/bin/env bash
# Install (or with --uninstall, remove) BlueLock for the current user from an
# extracted release bundle. Shipped in the Linux tarball as install.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/share/bluelock/bin"
UNIT_DIR="$HOME/.config/systemd/user"
CONFIG="$HOME/.config/bluelock/config.toml"
SERVICES=(bluelock.service bluelock-tray.service)

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now "${SERVICES[@]}" 2>/dev/null || true
  rm -f "$BIN_DIR/bluelock" "$BIN_DIR/bluelock-tray"
  for svc in "${SERVICES[@]}"; do rm -f "$UNIT_DIR/$svc"; done
  systemctl --user daemon-reload
  echo "BlueLock removed. Your config was kept at $CONFIG."
  exit 0
fi

# Running binaries can't be overwritten ("Text file busy"), so stop first.
systemctl --user stop "${SERVICES[@]}" 2>/dev/null || true

mkdir -p "$BIN_DIR" "$UNIT_DIR" "$(dirname "$CONFIG")"
install -m 755 "$HERE/bin/bluelock" "$HERE/bin/bluelock-tray" "$BIN_DIR/"
for svc in "${SERVICES[@]}"; do install -m 644 "$HERE/systemd/$svc" "$UNIT_DIR/"; done
if [[ ! -f "$CONFIG" ]]; then
  install -m 644 "$HERE/config.example.toml" "$CONFIG"
fi

systemctl --user daemon-reload
# reenable also drops links left by older installs (tray under default.target).
systemctl --user reenable "${SERVICES[@]}"
systemctl --user start "${SERVICES[@]}"

echo "BlueLock installed and started."
echo "Pick your phone in the tray icon: Bind Device. Guide: $HERE/USER_GUIDE.md"
