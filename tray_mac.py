#!/usr/bin/env python3
"""Menu bar app for BlueLock on macOS.

The lock monitor runs inside this process (on a background thread with its own
asyncio loop) so macOS asks for Bluetooth permission once, for BlueLock.app.
"""

import asyncio
import plistlib
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

import rumps

import bluetooth_lock
from bluelock import (
    KnownDevice,
    MonitorStatus,
    bind_device,
    default_config_path,
    is_bound,
    list_known_devices,
    set_config_value,
)
from bluelock import load_config as load_app_config

CONFIG_PATH = default_config_path()
LAUNCH_AGENT_LABEL = "com.bluelock.tray"
LAUNCH_AGENT_PATH = Path(f"~/Library/LaunchAgents/{LAUNCH_AGENT_LABEL}.plist").expanduser()
CALIBRATION_MAX_AGE_S = 10.0

# macOS hides real MAC addresses from apps; CoreBluetooth reports a per-Mac UUID
# instead, so the starter config matches by name.
DEFAULT_CONFIG = """\
[device]
# macOS does not expose Bluetooth MAC addresses. Match by name (partial,
# case-insensitive), or set address to the CoreBluetooth UUID shown in the menu.
name = "iPhone"

[lock]
distance_m = 3.0
debug = false

[bluetooth]
tx_power = -59
path_loss_exponent = 2.2
"""


def ensure_config() -> None:
    if CONFIG_PATH.exists():
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(DEFAULT_CONFIG)


def read_config() -> dict[str, Any]:
    ensure_config()
    return tomllib.loads(CONFIG_PATH.read_text())


def update_lock_setting(key: str, value: bool | float) -> None:
    ensure_config()
    set_config_value(CONFIG_PATH, "lock", key, value)


def launch_agent_args() -> list[str]:
    if getattr(sys, "frozen", False):
        # .../BlueLock.app/Contents/MacOS/BlueLock -> launch the bundle via
        # LaunchServices so Bluetooth permission stays attributed to the app.
        app = Path(sys.executable).resolve().parents[2]
        return ["/usr/bin/open", "-a", str(app)]
    return [sys.executable, str(Path(__file__).resolve())]


def set_start_at_login(enabled: bool) -> None:
    if not enabled:
        LAUNCH_AGENT_PATH.unlink(missing_ok=True)
        return
    LAUNCH_AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": launch_agent_args(),
        "RunAtLoad": True,
    }
    LAUNCH_AGENT_PATH.write_bytes(plistlib.dumps(plist))


class MonitorThread:
    def __init__(self) -> None:
        self.status = MonitorStatus()
        self.error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running():
            return
        ensure_config()
        config = load_app_config(CONFIG_PATH)
        if not config.device.address and not config.device.name:
            raise ValueError("Set device.name or device.address in the config first.")
        self.status = MonitorStatus()
        self.error = None
        started = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._task = loop.create_task(bluetooth_lock.monitor(config, self.status))
            started.set()
            try:
                loop.run_until_complete(self._task)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.error = str(exc) or type(exc).__name__
                print(f"Monitor stopped: {self.error}", file=sys.stderr)
            finally:
                loop.close()

        self._thread = threading.Thread(target=run, name="bluelock-monitor", daemon=True)
        self._thread.start()
        started.wait(timeout=5)

    def stop(self) -> None:
        if not self.running():
            return
        assert self._loop is not None and self._task is not None
        self._loop.call_soon_threadsafe(self._task.cancel)
        self._thread.join(timeout=5)
        self._thread = None

    def restart(self) -> None:
        self.stop()
        self.start()


class BlueLockApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("BlueLock", title="ᛒ", quit_button=None)
        self.monitor = MonitorThread()

        # Items without a callback render greyed out: read-only status lines.
        self.status_item = rumps.MenuItem("Status: --")
        self.lock_distance_item = rumps.MenuItem("Lock Distance: --")
        self.current_distance_item = rumps.MenuItem("Current Distance: --")
        self.last_seen_item = rumps.MenuItem("Last Seen: --")
        self.device_item = rumps.MenuItem("Device: --")
        self.debug_item = rumps.MenuItem("Debug Mode", callback=self.on_toggle_debug)
        self.login_item = rumps.MenuItem("Start at Login", callback=self.on_toggle_login)
        self.bind_menu = rumps.MenuItem("Bind Device")
        self.bind_menu.add(rumps.MenuItem("Looking for devices…"))
        self._known_devices: Optional[list[KnownDevice]] = None
        self._pending_devices: Optional[list[KnownDevice]] = None
        self._listing = False
        self.menu = [
            self.status_item,
            self.lock_distance_item,
            self.current_distance_item,
            self.last_seen_item,
            self.device_item,
            None,
            rumps.MenuItem("Start", callback=self.on_start),
            rumps.MenuItem("Stop", callback=self.on_stop),
            rumps.MenuItem("Restart", callback=self.on_restart),
            self.bind_menu,
            None,
            rumps.MenuItem("Set Distance…", callback=self.on_set_distance),
            rumps.MenuItem("Calibrate Distance…", callback=self.on_calibrate),
            self.debug_item,
            self.login_item,
            rumps.MenuItem("Open Config", callback=self.on_open_config),
            None,
            rumps.MenuItem("Quit BlueLock", callback=self.on_quit),
        ]
        self.timer = rumps.Timer(self.refresh, 2)
        self.device_timer = rumps.Timer(self.refresh_device_list, 30)

    def start_monitor(self, interactive: bool) -> None:
        try:
            self.monitor.start()
        except Exception as exc:
            self.monitor.error = str(exc)
            if interactive:
                rumps.alert("BlueLock", f"Could not start monitoring:\n{exc}")
        self.refresh()

    def restart_monitor_if_running(self) -> None:
        if self.monitor.running():
            self.monitor.stop()
            self.start_monitor(interactive=True)
        else:
            self.refresh()

    def refresh_device_list(self, _sender: Any = None) -> None:
        # system_profiler takes a second or two; list on a worker thread and let
        # refresh() apply the result, since rumps menus are main-thread only.
        if self._listing:
            return
        self._listing = True

        def worker() -> None:
            try:
                devices = list_known_devices()
            except Exception as exc:
                print(f"Listing Bluetooth devices failed: {exc}", file=sys.stderr)
                devices = []
            self._pending_devices = devices

        threading.Thread(target=worker, daemon=True).start()

    def apply_device_list(self, devices: list[KnownDevice], force: bool = False) -> None:
        self._listing = False
        if not force and devices == self._known_devices:
            return
        self._known_devices = devices
        self.bind_menu.clear()
        device_cfg = read_config().get("device", {})
        if not devices:
            self.bind_menu.add(rumps.MenuItem("No paired devices found"))
        for dev in devices:
            item = rumps.MenuItem(dev.label, callback=lambda _sender, d=dev: self.on_bind(d))
            item.state = int(is_bound(dev, device_cfg.get("address"), device_cfg.get("name")))
            self.bind_menu.add(item)
        self.bind_menu.add(rumps.separator)
        self.bind_menu.add(rumps.MenuItem("Refresh List", callback=self.refresh_device_list))

    def on_bind(self, device: KnownDevice) -> None:
        ensure_config()
        try:
            bind_device(CONFIG_PATH, device)
        except (OSError, ValueError) as exc:
            rumps.alert("BlueLock", f"Could not bind {device.label}:\n{exc}")
            return
        self.monitor.stop()
        self.start_monitor(interactive=True)
        self.apply_device_list(self._known_devices or [], force=True)

    def refresh(self, _sender: Any = None) -> None:
        pending, self._pending_devices = self._pending_devices, None
        if pending is not None:
            self.apply_device_list(pending)
        try:
            data = read_config()
        except Exception as exc:
            data = {}
            self.monitor.error = f"Config error: {exc}"
        lock = data.get("lock", {})
        device_cfg = data.get("device", {})
        status = self.monitor.status
        running = self.monitor.running()

        if running:
            self.status_item.title = "Status: Active"
        elif self.monitor.error:
            self.status_item.title = f"Status: Error - {self.monitor.error[:60]}"
        else:
            self.status_item.title = "Status: Inactive"
        self.title = "ᛒ" if running else "ᛒ (off)"

        self.lock_distance_item.title = f"Lock Distance: {float(lock.get('distance_m', 3.0)):.2f} m"
        if running and status.distance_m is not None:
            self.current_distance_item.title = f"Current Distance: {status.distance_m:.2f} m"
        else:
            self.current_distance_item.title = "Current Distance: --"
        if running and status.last_seen is not None:
            self.last_seen_item.title = f"Last Seen: {time.time() - status.last_seen:.1f}s ago"
        else:
            self.last_seen_item.title = "Last Seen: --"

        parts = [p for p in (status.name, status.address) if p] if running else []
        if parts and status.rssi is not None:
            parts.append(f"{status.rssi} dBm")
        if not parts:
            parts = [str(v) for v in (device_cfg.get("name"), device_cfg.get("address")) if v]
        self.device_item.title = f"Device: {' | '.join(parts) or '--'}"

        self.debug_item.state = int(bool(lock.get("debug", False)))
        self.login_item.state = int(LAUNCH_AGENT_PATH.exists())

    def on_start(self, _sender: Any) -> None:
        self.start_monitor(interactive=True)

    def on_stop(self, _sender: Any) -> None:
        self.monitor.stop()
        self.refresh()

    def on_restart(self, _sender: Any) -> None:
        self.monitor.stop()
        self.start_monitor(interactive=True)

    def on_set_distance(self, _sender: Any) -> None:
        current = read_config().get("lock", {}).get("distance_m", 3.0)
        response = rumps.Window(
            message="Lock when the device is farther than (meters):",
            title="Set Lock Distance",
            default_text=str(current),
            ok="Save",
            cancel="Cancel",
            dimensions=(160, 24),
        ).run()
        if not response.clicked:
            return
        try:
            value = float(response.text.strip())
        except ValueError:
            rumps.alert("BlueLock", f"Not a number: {response.text!r}")
            return
        if value <= 0:
            rumps.alert("BlueLock", "Distance must be greater than zero.")
            return
        update_lock_setting("distance_m", value)
        self.restart_monitor_if_running()

    def on_calibrate(self, _sender: Any) -> None:
        status = self.monitor.status
        fresh = status.last_seen is not None and time.time() - status.last_seen <= CALIBRATION_MAX_AGE_S
        if not self.monitor.running() or not fresh or status.distance_m is None:
            rumps.alert(
                "Calibration failed",
                "No recent reading from your device. Start monitoring, keep the device "
                "where you want the lock boundary, wait a few seconds, then try again.",
            )
            return
        distance = round(status.distance_m, 2)
        if rumps.alert(
            "Calibrate Distance",
            f"Set lock distance to the current reading of {distance:.2f} m?",
            ok="Set",
            cancel="Cancel",
        ) != 1:
            return
        update_lock_setting("distance_m", distance)
        self.restart_monitor_if_running()

    def on_toggle_debug(self, sender: rumps.MenuItem) -> None:
        update_lock_setting("debug", not bool(sender.state))
        self.restart_monitor_if_running()

    def on_toggle_login(self, sender: rumps.MenuItem) -> None:
        set_start_at_login(not bool(sender.state))
        self.refresh()

    def on_open_config(self, _sender: Any) -> None:
        ensure_config()
        subprocess.run(["open", "-t", str(CONFIG_PATH)], check=False)

    def on_quit(self, _sender: Any) -> None:
        self.monitor.stop()
        rumps.quit_application()


def main() -> int:
    ensure_config()
    app = BlueLockApp()
    app.start_monitor(interactive=False)
    app.timer.start()
    app.refresh_device_list()
    app.device_timer.start()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
