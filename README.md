# Bluetooth Lock

Lock your PC when a specific Bluetooth (BLE) device is too far away.

Runs on Ubuntu, Windows 10/11 and macOS 12+ (see the [macOS](#macos-12) section).

**Using BlueLock?** Read the [User Guide](docs/USER_GUIDE.md): setup, the tray menu,
tuning, commands and troubleshooting. This README covers installing, building and packaging.

## Requirements
- Ubuntu 24.04+ (tested)
- Bluetooth adapter with BLE support
- System packages: `sudo apt install python3-venv python3-gi gir1.2-ayatanaappindicator3-0.1 bluez`

## Install (deb)
Build and install the package, then run the installer:
```
sudo apt install -y debhelper dh-make

# From the repo root
dpkg-buildpackage -us -uc
sudo apt install ../bluelock_1.0.0-1_all.deb

# Set up user services + config
bluelock install
```
Quick build helpers:
```
make deb
make deb-version VERSION=1.0.1-1
make install-deb DEB_VERSION=1.0.1-1
make clean-deb
```

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
          bluelock_1.0.1_all.deb
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
cp ../bluelock_*_all.deb repo/pool/main/b/bluelock/

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

This repo includes `.github/workflows/publish-apt.yml` to build and publish the APT repo
to GitHub Pages on tags like `v1.0.1` or via manual dispatch.

Required secrets (Repository Settings → Secrets and variables → Actions):
- `GPG_PRIVATE_KEY`: ASCII-armored private key
- `GPG_KEY_ID`: key id or fingerprint
- `GPG_PASSPHRASE`: passphrase for the private key

Workflow output:
- GitHub Pages is updated with the APT repo
- The public key is published at `https://you.github.io/bluelock/bluelock.gpg`

To trigger a release:
```bash
git tag v1.0.1
git push origin v1.0.1
```
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
- `bluelock install` sets up a Python venv, builds the daemon and tray binaries with PyInstaller into `~/.local/share/bluelock/bin`, and installs and starts the systemd user services. It does not install system packages (see Requirements).
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
