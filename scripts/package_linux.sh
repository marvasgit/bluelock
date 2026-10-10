#!/usr/bin/env bash
# Package the PyInstaller binaries from `make build-linux` into a release tarball
# that installs itself with ./install.sh.
set -euo pipefail

VERSION="${1:?usage: package_linux.sh VERSION}"
cd "$(dirname "$0")/.."

NAME="bluelock-$VERSION-linux-$(uname -m)"
STAGE="dist/$NAME"

for f in dist/bluelock dist/bluelock-tray; do
  [[ -x "$f" ]] || { echo "Missing $f; run 'make build-linux' first." >&2; exit 1; }
done

rm -rf "$STAGE"
mkdir -p "$STAGE/bin" "$STAGE/systemd"
cp dist/bluelock dist/bluelock-tray "$STAGE/bin/"
cp systemd/bluelock.service systemd/bluelock-tray.service "$STAGE/systemd/"
cp config.example.toml README.md docs/USER_GUIDE.md "$STAGE/"
cp scripts/install_linux_bundle.sh "$STAGE/install.sh"
chmod +x "$STAGE/install.sh"

tar -C dist -czf "dist/$NAME.tar.gz" "$NAME"
echo "Built dist/$NAME.tar.gz"
