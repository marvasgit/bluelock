# Bluetooth Lock

Lock your PC when a specific Bluetooth (BLE) device is too far away.

Runs on Ubuntu, Windows 10/11 and macOS 12+ (see the [macOS](#macos-12) section).

**Using BlueLock?** Read the [User Guide](docs/USER_GUIDE.md): setup, the tray menu,
tuning, commands and troubleshooting. This README covers installing, building and packaging.

## Requirements
- Ubuntu 24.04+ (tested)
- Bluetooth adapter with BLE support
- Building from source also needs: `sudo apt install python3-venv libpython3-dev python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 bluez debhelper`

## Install (deb)
The deb ships ready-built programs (in `/usr/lib/bluelock/`) and starts BlueLock for
every user at login, so no build happens on your machine.

- **From the APT repo** (gets updates via `apt upgrade`): see the install steps under
  [GitHub Actions (auto-publish on tag)](#github-actions-auto-publish-on-tag).
- **From a release:** download `bluelock_<ver>-1_amd64.deb` from GitHub Releases, then
  `sudo apt install ./bluelock_<ver>-1_amd64.deb`.

Installing with `sudo` also starts it right away for your user; other users get it at
their next login. If it isn't running, run `bluelock install`.

Build the deb yourself (compiles the programs first, then packages them):
```
make deb                          # -> ../bluelock_1.0.0-1_amd64.deb
make deb-version VERSION=1.0.1-1  # same, with a new changelog version
make install-deb
```
The programs bundle libraries from the build machine, so a deb only runs on systems at
least as new as the one it was built on. Release debs are built by CI on Ubuntu 24.04.

## Install (npm)
```
npm install -g bluelock
bluelock install
```

## Publishing the deb (self-hosted APT repo)

This project does not ship in the official Ubuntu repositories. The recommended way
to distribute the deb is to host your own APT repo (e.g. GitHub Pages, S3, R2, or any
HTTPS static host).

High-level steps:
1. Build the deb: `make deb`
2. Create a repo structure (e.g. `repo/dists/stable/main/binary-amd64/`)
3. Generate repo metadata (`Packages`, `Packages.gz`, `Release`)
4. (Recommended) Sign the repo with GPG
5. Host the repo over HTTPS
6. Provide install instructions for users

Example repo layout:
```
repo/
  dists/
    stable/
      Release
      InRelease
      main/
        binary-amd64/
          Packages
          Packages.gz
  pool/
    main/
      b/
        bluelock/
          bluelock_1.0.1-1_amd64.deb
```

Minimal (unsigned) metadata generation from `repo/`:
```bash
dpkg-scanpackages --multiversion pool > dists/stable/main/binary-amd64/Packages
gzip -kf dists/stable/main/binary-amd64/Packages
apt-ftparchive release dists/stable > dists/stable/Release
```

Signing (recommended):
```bash
gpg --default-key "Your Name <you@example.com>" --detach-sign -o dists/stable/Release.gpg dists/stable/Release
gpg --default-key "Your Name <you@example.com>" --clearsign -o dists/stable/InRelease dists/stable/Release
```

User install instructions (example):
```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://your-domain.example/bluelock.gpg | sudo tee /etc/apt/keyrings/bluelock.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/bluelock.gpg] https://your-domain.example/repo stable main" | sudo tee /etc/apt/sources.list.d/bluelock.list
sudo apt update
sudo apt install bluelock
```

### GitHub Pages example

Assume your repo is `https://github.com/you/bluelock` and you will publish to GitHub Pages from the `gh-pages` branch.

1. Build the deb:
```bash
make deb
```

2. Prepare the repo directory locally:
```bash
mkdir -p repo/pool/main/b/bluelock repo/dists/stable/main/binary-amd64
cp ../bluelock_*.deb repo/pool/main/b/bluelock/

cd repo
dpkg-scanpackages --multiversion pool > dists/stable/main/binary-amd64/Packages
gzip -kf dists/stable/main/binary-amd64/Packages
apt-ftparchive release dists/stable > dists/stable/Release
```

3. (Recommended) Sign the repo:
```bash
gpg --default-key "Your Name <you@example.com>" --detach-sign -o dists/stable/Release.gpg dists/stable/Release
gpg --default-key "Your Name <you@example.com>" --clearsign -o dists/stable/InRelease dists/stable/Release
```

4. Publish `repo/` to GitHub Pages (gh-pages branch):
```bash
git checkout --orphan gh-pages
git rm -rf .
cp -R repo/* .
git add .
git commit -m "Publish APT repo"
git push -u origin gh-pages --force
```

5. Enable GitHub Pages:
- Repository Settings → Pages → Deploy from a branch
- Branch: `gh-pages` / `/` (root)

6. User install instructions (replace `you`):
```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://you.github.io/bluelock/bluelock.gpg | sudo tee /etc/apt/keyrings/bluelock.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/bluelock.gpg] https://you.github.io/bluelock stable main" | sudo tee /etc/apt/sources.list.d/bluelock.list
sudo apt update
sudo apt install bluelock
```

### GitHub Actions (auto-publish on tag)

The `apt-repo` job in `.github/workflows/build.yml` takes the deb the `linux` job built,
generates a signed repo with `scripts/build_apt_repo.sh`, and publishes it to the
`gh-pages` branch, served at **https://marvasgit.github.io/bluelock/**. It runs on tags
like `v1.0.1` (publishing that version), or manually with *Build bundles → Run workflow*
and **publish_apt** ticked.

One-time setup:
1. Create a signing key and add it as repository secrets
   (Settings → Secrets and variables → Actions):
   - `GPG_PRIVATE_KEY`: ASCII-armored private key (`gpg --armor --export-secret-keys <id>`)
   - `GPG_KEY_ID`: key fingerprint
   - `GPG_PASSPHRASE`: its passphrase (omit if the key has none)
2. Run the workflow once so the `gh-pages` branch exists.
3. Settings → Pages → *Deploy from a branch* → `gh-pages` / `(root)`.

The workflow fails early if the key secrets are missing, since users install with
`signed-by`. To build the same repo locally:
`GPG_KEY_ID=<fpr> ./scripts/build_apt_repo.sh repo ../bluelock_*.deb`.

Users install with:
```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://marvasgit.github.io/bluelock/bluelock.gpg | sudo tee /etc/apt/keyrings/bluelock.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/bluelock.gpg] https://marvasgit.github.io/bluelock stable main" | sudo tee /etc/apt/sources.list.d/bluelock.list
sudo apt update
sudo apt install bluelock
```
Later releases arrive through `sudo apt upgrade`.

## Release bundles (GitHub Actions)

`.github/workflows/build.yml` builds a separate bundle for each platform:

| Platform | Runner | Files |
| --- | --- | --- |
| Linux (x86_64, Ubuntu 24.04+) | `ubuntu-24.04` | `bluelock-<ver>-linux-x86_64.tar.gz`, `bluelock_<ver>-1_amd64.deb` |
| Windows 10/11 | `windows-latest` | `bluelock-<ver>-win64.zip`, `bluelock-<ver>-win64-single.zip`, `bluelock-<ver>-win64-setup.exe` |
| macOS (Apple Silicon) | `macos-latest` | `BlueLock-<ver>-macos.dmg`, `BlueLock-<ver>-macos.zip` |

- **Release:** push a tag, e.g. `git tag v1.1.0 && git push origin v1.1.0`. All bundles are
  attached to a GitHub Release named after the tag.
- **Test build:** Actions → *Build bundles* → *Run workflow* (or
  `gh workflow run build.yml`). Bundles are kept as run artifacts, with no release.

The Linux tarball installs for the current user with `./install.sh`
(`./install.sh --uninstall` removes it). Locally, the same tarball comes from
`make build-linux && ./scripts/package_linux.sh <ver>`.

## Configure
Edit `~/.config/bluelock/config.toml`:
- `device.address` (MAC) or `device.name`
- `lock.distance_m`
- `lock.debug` (when true, never locks; prints/shows reasons)
- Optional: `lock.command` for a custom lock action
 - Optional: `lock.breach_samples` and `lock.lock_cooldown_s` to reduce re-locking

### Choosing the device

Open the tray menu → **Bind Device**. It lists the Bluetooth devices paired with
this computer, with currently connected ones first and marked *(connected)*.
Click one to make it the monitored device: BlueLock writes `device.address` and
`device.name` to the config and restarts monitoring. The list refreshes on its own;
use **Refresh List** after pairing something new.

On macOS only the name is stored (`device.address` is cleared), since macOS doesn't
give apps real Bluetooth addresses.

Phones often advertise over BLE with a rotating private address rather than the
paired address, and iPhones don't show up in BLE scans at all. In that case BlueLock
(Linux) uses the **connection signal** of the connected phone instead. The tray shows
`Connection Signal: 0 dB, locks at -10 dB`, where 0 means strong and the value goes negative
as you walk away. It locks once the signal stays at or below `lock.link_rssi_threshold`.
To tune it, leave the phone where the lock should trigger and click **Calibrate Distance…**.
Use Debug Mode while tuning. The connection signal needs `hcitool` (package `bluez`).

## Run
```
bluelock start
```

## Commands
```
bluelock status
bluelock stop
bluelock restart
bluelock config
bluelock run
bluelock tray
```

## Notes
- `bluelock install` (deb) enables and restarts the packaged services for your user, and removes per-user units left by older versions that built BlueLock locally. The npm version's `bluelock install` still builds the programs locally.
- `bluelock start/stop/restart` control the daemon only; the tray is `bluelock-tray.service`.
- If you don't want services, run `bluelock run` and `bluelock tray` manually.
- Use the tray menu “Calibrate Distance…” to set `lock.distance_m` based on real RSSI at your desired lock distance.
- RSSI-based distance is approximate; try adjusting `tx_power`, `path_loss_exponent`, and `distance_m`.

## Windows (10/11)

### Build portable zip

From PowerShell:
```powershell
.\scripts\build_windows.ps1 -Version 1.0.0
```

This produces `dist\bluelock-1.0.0-win64.zip` containing:
- `bluelock_tray.exe`
- `bluelock_daemon.exe`
- `config.example.toml`

Run `bluelock_tray.exe` to start the tray app (it can start/stop the daemon).

It also produces `dist\bluelock-1.0.0-win64-single.zip` containing:
- `bluelock.exe` (tray + embedded daemon)
- `config.example.toml`

### Build installer (NSIS)

1. Build the portable zip (above) to generate `dist\*.exe`.
2. Install NSIS and run:
```powershell
makensis .\windows\installer.nsi
```

The installer:
- Installs to `Program Files\BlueLock`
- Adds a Start Menu + Desktop shortcut
- Registers tray app to start at login

### Config location (Windows)

Config is stored at:
```
%APPDATA%\bluelock\config.toml
```

## macOS (12+)

On macOS BlueLock is a single menu bar app, `BlueLock.app`, with the lock monitor
built in (no separate daemon). Look for **ᛒ** in the menu bar.

### Build

On a Mac with Python 3.11+ (from python.org or Homebrew):
```
./scripts/build_macos.sh 1.0.0
# or: make build-macos VERSION=1.0.0
```

This produces:
- `dist/BlueLock.app`
- `dist/BlueLock-1.0.0-macos.zip`
- `dist/BlueLock-1.0.0-macos.dmg`

The app is ad-hoc signed, not notarized. On first launch, right-click
`BlueLock.app` → **Open** (or allow it under System Settings → Privacy & Security).

### Install
1. Drag `BlueLock.app` to `/Applications` and open it.
2. Allow Bluetooth access when macOS asks.
3. Menu bar → **Open Config** and set your device (see below), then **Restart**.
4. Optional: enable **Start at Login**. This writes `~/Library/LaunchAgents/com.bluelock.tray.plist`.

To run from source instead: `pip install -r requirements-macos.txt && python3 tray_mac.py`.

### Device matching on macOS

macOS does not give apps real Bluetooth MAC addresses, so match the device by **name**:
```toml
[device]
name = "iPhone"
```
Once the device is seen, the menu shows the per-Mac identifier (a UUID) that macOS assigned to it.
You can also put that UUID in `device.address` to pin one exact device.

iPhones advertise over BLE only intermittently while locked. If the Mac locks too eagerly,
raise `lock.missing_timeout_s` (e.g. `30`).

### Locking

BlueLock locks the screen the same way as  → **Lock Screen** does, and needs no extra permission.
If that is unavailable, it falls back to pressing Ctrl+Cmd+Q, which needs Accessibility
permission for BlueLock. A custom `lock.command` overrides both.

### Config location (macOS)
```
~/.config/bluelock/config.toml
```
