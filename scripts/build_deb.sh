#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"

if [[ -n "$VERSION" ]]; then
  if command -v dch >/dev/null 2>&1; then
    dch --newversion "$VERSION" --distribution noble --urgency medium "Release $VERSION"
  else
    echo "dch not found; please update debian/changelog manually." >&2
    exit 1
  fi
fi

dpkg-buildpackage -us -uc -b
