#!/usr/bin/env bash
# Build a static APT repository (pool/ + dists/stable) from .deb files, ready to
# host on GitHub Pages or any HTTPS server. If GPG_KEY_ID is set, the repo is
# signed (InRelease, Release.gpg) and the public key is exported as bluelock.gpg.
set -euo pipefail

OUT="${1:?usage: build_apt_repo.sh OUT_DIR DEB...}"
shift
[[ $# -gt 0 ]] || { echo "No .deb files given." >&2; exit 1; }

ARCHES=(amd64 arm64)
mkdir -p "$OUT/pool/main/b/bluelock"
cp "$@" "$OUT/pool/main/b/bluelock/"
cd "$OUT"

for arch in "${ARCHES[@]}"; do
  mkdir -p "dists/stable/main/binary-$arch"
  # Arch "all" packages are listed for every architecture.
  dpkg-scanpackages --arch "$arch" --multiversion pool > "dists/stable/main/binary-$arch/Packages"
  gzip -kf "dists/stable/main/binary-$arch/Packages"
done

apt-ftparchive \
  -o APT::FTPArchive::Release::Origin=BlueLock \
  -o APT::FTPArchive::Release::Label=BlueLock \
  -o APT::FTPArchive::Release::Suite=stable \
  -o APT::FTPArchive::Release::Codename=stable \
  -o APT::FTPArchive::Release::Components=main \
  -o "APT::FTPArchive::Release::Architectures=${ARCHES[*]}" \
  release dists/stable > dists/stable/Release.tmp
mv dists/stable/Release.tmp dists/stable/Release

if [[ -n "${GPG_KEY_ID:-}" ]]; then
  gpg_sign=(gpg --batch --yes --default-key "$GPG_KEY_ID")
  if [[ -n "${GPG_PASSPHRASE:-}" ]]; then
    gpg_sign+=(--pinentry-mode loopback --passphrase "$GPG_PASSPHRASE")
  fi
  "${gpg_sign[@]}" --detach-sign --armor -o dists/stable/Release.gpg dists/stable/Release
  "${gpg_sign[@]}" --clearsign -o dists/stable/InRelease dists/stable/Release
  # Binary (dearmored) key, as /etc/apt/keyrings expects for signed-by.
  gpg --batch --yes --export "$GPG_KEY_ID" > bluelock.gpg
  echo "Signed with $GPG_KEY_ID"
else
  echo "GPG_KEY_ID not set: repository is UNSIGNED." >&2
fi
echo "APT repository written to $OUT"
