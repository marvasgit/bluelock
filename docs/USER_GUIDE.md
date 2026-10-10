# BlueLock User Guide

BlueLock locks your computer when your phone (or another Bluetooth device) moves
away from it. This guide covers everyday use on Ubuntu; Windows and macOS
differences are at the end. For building and packaging, see the [README](../README.md).

## How it works

BlueLock has two parts:

- **The daemon** (`bluelock.service`) watches your device and locks the screen.
- **The tray icon** (`bluelock-tray.service`) shows what the daemon sees and lets you
  change settings. Closing the tray does **not** stop locking.

The daemon decides your device is "away" in one of three ways, best first:

1. **Distance**: from the device's BLE broadcasts. Most earbuds, watches and
   Android phones work this way.
2. **Connection signal**: for devices that stay connected but never show up in
   BLE scans, such as iPhones. The signal of the connection is read instead:
   `0 dB` is strong, and it goes negative as you walk away.
3. **Missing**: the device stops broadcasting *and* disconnects for
   `missing_timeout_s` seconds (8 by default).

It locks **once** each time you walk away. It won't lock again until your device
has come back within range, so unlocking with your phone still away doesn't put
you in a lock loop.

## First-time setup (Ubuntu)

1. Add the BlueLock APT repo and install (see the README's *GitHub Actions* section for
   the full commands), or install a downloaded release:
   ```bash
   sudo apt install ./bluelock_<version>-1_amd64.deb
   ```
2. That's it: the programs come ready-built, and BlueLock starts right away for you and
   at login for every user. If the tray icon doesn't appear, run `bluelock install`.
3. Pair your phone in **Settings → Bluetooth** if it isn't paired yet.
4. Click the BlueLock icon in the top bar → **Bind Device** → pick your phone.
5. Turn on **Debug Mode**, walk away, and check that you get a "would lock"
   notification at the right spot (see [Tuning](#tuning)). Then turn Debug Mode off.

## The tray menu

The first lines are status (greyed out, not clickable):

| Line | Meaning |
| --- | --- |
| **Status: Active / Inactive** | Whether the daemon is running. |
| **Lock Distance: 3.00 m** | Lock threshold used when a BLE distance is available. |
| **Current Distance: 1.85 m** | Distance estimated from BLE broadcasts. |
| **Connection Signal: 0 dB, locks at -10 dB** | Shown instead of distance when the device is connected but not visible over BLE (iPhone). |
| **Current Distance: N/A (BLE idle)** | Connected, but neither a distance nor a connection signal is available (e.g. `hcitool` missing). Only locking on disconnect works. |
| **Current Distance: --** | Device not seen and not connected. |
| **Last Seen: 2.0s ago** | Time since the last BLE broadcast; `connected (no BLE adv)` means it's connected only. |
| **Device: …** | What the tray found: name, address, signal. |
| **Connected: Yes / No** | Whether the device has a Bluetooth connection. |

Actions:

| Item | What it does |
| --- | --- |
| **Start / Stop / Restart** | Controls the daemon. **Stop** disables locking until you start it again. |
| **Reconnect Device** | Asks Bluetooth to reconnect the bound device. The daemon also retries every 30 s on its own. |
| **Bind Device ▸** | Lists paired devices, connected ones first. Click one to monitor it. **Refresh List** after pairing something new. |
| **Set Distance…** | Type a lock distance in meters. |
| **Calibrate Distance…** | Measures where the lock boundary is: see [Tuning](#tuning). |
| **Debug Mode** | When ticked, BlueLock never locks; it shows a notification with the reason instead. |
| **Open Config** | Opens `~/.config/bluelock/config.toml` in your text editor. |
| **Quit** | Closes the tray icon only. The daemon keeps locking. |

Settings changed from the tray restart the daemon automatically. If you edit the
config file by hand, run `bluelock restart` afterwards.

## Tuning

Always tune with **Debug Mode** on, so a wrong setting can't lock you out
repeatedly.

**Calibrate (recommended):** put the phone where the lock should trigger, such
as on the doorframe, then click **Calibrate Distance…** on the computer. It takes
about 15 seconds.

- If the phone shows up in BLE scans, this sets the lock **distance**.
- If not (iPhone), it sets the **connection signal** level instead. It refuses if the
  signal is still strong (0 dB), because that would lock all the time; move the phone farther away.

**By hand** in the config (`Open Config`):

| Problem | Change |
| --- | --- |
| Locks while I'm still at the desk | Raise `distance_m`, or make `link_rssi_threshold` more negative (e.g. `-14`). |
| Locks too late / not until I've left the room | Lower `distance_m`, or make `link_rssi_threshold` less negative (e.g. `-6`). |
| Locks from a single bad reading | Raise `breach_samples` (default 3). |
| Locks when the phone briefly goes quiet | Raise `missing_timeout_s` (e.g. `30` for iPhones). |

Every setting is explained in [`config.example.toml`](../config.example.toml).

## Command line

```bash
bluelock status    # daemon and tray: active / inactive
bluelock start     # start the daemon
bluelock stop      # stop the daemon (locking off)
bluelock restart   # restart the daemon, e.g. after editing the config
bluelock config    # print the config file path
bluelock run       # run the daemon in this terminal (for testing)
bluelock tray      # run the tray in this terminal (for testing)
bluelock install   # enable and restart both services for your user
```

`start`, `stop` and `restart` affect the daemon only. To control the tray, use
`systemctl --user start|stop bluelock-tray`.

Logs:

```bash
journalctl --user -u bluelock -f        # daemon: start-up, lock reasons
journalctl --user -u bluelock-tray -f   # tray
```

## Troubleshooting

**No tray icon.**
Check `systemctl --user status bluelock-tray`. The tray starts with your desktop
session; if you just installed, log out and back in, or run
`systemctl --user start bluelock-tray`. On plain GNOME (not Ubuntu), install the
*AppIndicator and KStatusNotifierItem Support* extension. A
"Cannot load GTK/AppIndicator" in `journalctl --user -u bluelock-tray` means
`gir1.2-gtk-3.0` or `gir1.2-ayatanaappindicator3-0.1` is missing (the deb normally
installs both).

**It never locks.**
1. Is **Debug Mode** ticked? Then it only shows notifications.
2. Is **Status** Active, and is the right device bound (**Bind Device** shows a mark)?
3. Look for lock reasons with `journalctl --user -u bluelock -f`.
4. If the log shows a lock but the screen didn't lock, your desktop may not support
   the default lock command (`loginctl lock-session`). Set your own in the config,
   e.g. `command = "xdg-screensaver lock"`.

**"N/A (BLE idle)".**
The device is connected but BlueLock can't measure how far it is. Make sure
`hcitool` exists (`which hcitool`, from the `bluez` package) and that
`link_rssi_threshold` isn't `false`. Until then BlueLock still locks once the
device disconnects.

**Bind Device says "No paired devices found".**
Pair the device in **Settings → Bluetooth** first, then **Refresh List**.

**"Calibration failed: device not found and not connected".**
The phone is neither broadcasting nor connected. Unlock it, turn Bluetooth on,
use **Reconnect Device**, then calibrate again.

**Locks right after I unlock.**
This shouldn't happen: after a lock, BlueLock waits until your device is back in
range. If it does, check the log for the reason and raise `lock_cooldown_s`.

**Still running an old version after upgrading.**
Run `bluelock install`. It removes per-user service files left by older BlueLock
versions (in `~/.config/systemd/user/`), which override the packaged ones.

## Windows and macOS

**Windows:** run `bluelock_tray.exe` (or the single `bluelock.exe`). The menu matches
Linux, minus **Reconnect Device**. The installer adds it to startup. Config:
`%APPDATA%\bluelock\config.toml`. In the single-exe version, restart the app after
changing settings so the built-in daemon picks them up.

**macOS:** BlueLock is a menu bar app (the **ᛒ** icon) with the daemon built in;
**Start at Login** is in its menu. macOS hides Bluetooth addresses, so devices are
matched by name. On macOS BlueLock can only see BLE broadcasts: it can't read the
connection state or connection signal. It works best with devices that broadcast
their name, such as many watches and earbuds. iPhones usually don't, so an iPhone may never
be detected on a Mac. If a device drops out briefly and locks too eagerly, raise
`missing_timeout_s`. Config: `~/.config/bluelock/config.toml`.
