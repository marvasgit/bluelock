#!/usr/bin/env python3
"""Tray UI for controlling the Bluetooth lock service on Windows."""

import argparse
import asyncio
import os
from pathlib import Path
import time
from typing import Any, Optional
import subprocess
import sys
import threading

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from bleak import BleakScanner
from PIL import Image, ImageDraw
import pystray

import bluetooth_lock
from bluelock import KnownDevice, bind_device, is_bound, list_known_devices, set_config_value
from bluetooth_lock import estimate_distance_m, matches_device


def default_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "bluelock" / "config.toml"
    return Path("~/.config/bluelock/config.toml").expanduser()


CONFIG_PATH = default_config_path()


def ensure_config() -> None:
    if CONFIG_PATH.exists():
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        "[device]\naddress = \"AA:BB:CC:DD:EE:FF\"\n\n"
        "[lock]\ndistance_m = 3.0\n\n"
        "[bluetooth]\ntx_power = -59\npath_loss_exponent = 2.2\n"
    )


def load_config() -> dict[str, Any]:
    ensure_config()
    return tomllib.loads(CONFIG_PATH.read_text())


def open_config() -> None:
    os.system(f'start "" "{CONFIG_PATH}"')


def update_distance(new_distance: float) -> None:
    ensure_config()
    set_config_value(CONFIG_PATH, "lock", "distance_m", float(new_distance))


def update_debug(enabled: bool) -> None:
    ensure_config()
    set_config_value(CONFIG_PATH, "lock", "debug", bool(enabled))


def build_icon_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill=(40, 120, 220, 255))
    draw.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 255))
    return image


def scan_device_info() -> tuple[Optional[int], Optional[str], Optional[str], Optional[float]]:
    data = load_config()
    device = data.get("device", {})
    target_addr = device.get("address")
    target_name = device.get("name")
    bluetooth = data.get("bluetooth", {})
    adapter = bluetooth.get("adapter")
    tx_power = int(bluetooth.get("tx_power", -59))
    path_loss_exponent = float(bluetooth.get("path_loss_exponent", 2.2))

    async def _scan() -> tuple[Optional[int], Optional[str], Optional[str], Optional[float]]:
        devices = await BleakScanner.discover(timeout=1.5, adapter=adapter, return_adv=True)
        for dev, adv in devices.values():
            if matches_device(dev.address, dev.name, target_addr, target_name):
                if adv.rssi is None:
                    return None, dev.address, dev.name, None
                distance = estimate_distance_m(adv.rssi, tx_power, path_loss_exponent)
                return adv.rssi, dev.address, dev.name, distance
        return None, None, None, None

    return asyncio.run(_scan())


class TrayState:
    def __init__(self) -> None:
        self.lock_distance = 3.0
        self.current_distance: Optional[float] = None
        self.last_seen: Optional[float] = None
        self.device_label = "--"
        self.debug_enabled = False
        self.known_devices: list[KnownDevice] = []
        self.bound_address: Optional[str] = None
        self.bound_name: Optional[str] = None
        self.devices_listed_at = 0.0


def find_daemon_exe() -> Optional[str]:
    candidates = ["bluelock_daemon.exe", "bluetooth_lock.exe"]
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent
    for name in candidates:
        path = base / name
        if path.exists():
            return str(path)
    return None


def start_daemon(proc_holder: dict) -> None:
    if proc_holder.get("proc") is not None:
        return
    exe = find_daemon_exe()
    if exe:
        proc_holder["proc"] = subprocess.Popen([exe, str(CONFIG_PATH)])
        return
    script = Path(__file__).resolve().parent / "bluetooth_lock.py"
    proc_holder["proc"] = subprocess.Popen([sys.executable, str(script), str(CONFIG_PATH)])


def stop_daemon(proc_holder: dict) -> None:
    proc = proc_holder.get("proc")
    if proc is None:
        return
    proc.terminate()
    proc_holder["proc"] = None


def restart_daemon(proc_holder: dict) -> None:
    stop_daemon(proc_holder)
    start_daemon(proc_holder)


def start_embedded_daemon() -> Optional[threading.Thread]:
    try:
        config = bluetooth_lock.load_config(CONFIG_PATH)
    except Exception:
        return None
    if not config.device.address and not config.device.name:
        return None

    def _run() -> None:
        asyncio.run(bluetooth_lock.monitor(config))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def run_tray(embedded_daemon: bool) -> int:
    ensure_config()
    state = TrayState()
    proc_holder: dict[str, Optional[subprocess.Popen]] = {"proc": None}
    embedded_thread: Optional[threading.Thread] = None

    def refresh_state() -> None:
        data = load_config()
        lock = data.get("lock", {})
        state.lock_distance = float(lock.get("distance_m", 3.0))
        state.debug_enabled = bool(lock.get("debug", False))
        device_cfg = data.get("device", {})
        state.bound_address = device_cfg.get("address")
        state.bound_name = device_cfg.get("name")

        if time.time() - state.devices_listed_at >= 30:
            state.devices_listed_at = time.time()
            try:
                state.known_devices = list_known_devices()
            except Exception as exc:
                print(f"Listing Bluetooth devices failed: {exc}", file=sys.stderr)

        try:
            rssi, addr, name, distance = scan_device_info()
        except Exception:
            rssi, addr, name, distance = None, None, None, None

        if addr or name:
            state.last_seen = time.time()
            parts = []
            if name:
                parts.append(name)
            if addr:
                parts.append(addr)
            if rssi is not None:
                parts.append(f"{rssi} dBm")
            state.device_label = " | ".join(parts)
        else:
            state.device_label = "--"
            stale_after = float(lock.get("missing_timeout_s", 8.0)) * 2.0
            stale_after = max(stale_after, 15.0)
            if state.last_seen is not None and (time.time() - state.last_seen) > stale_after:
                state.last_seen = None

        state.current_distance = distance

    def background_refresh(icon: pystray.Icon) -> None:
        while icon.visible:
            refresh_state()
            icon.update_menu()
            time.sleep(5)

    def menu_status(_item: pystray.MenuItem) -> str:
        running = proc_holder.get("proc") is not None or embedded_thread is not None
        return f"Status: {'Active' if running else 'Inactive'}"

    def menu_lock_distance(_item: pystray.MenuItem) -> str:
        return f"Lock Distance: {state.lock_distance:.2f} m"

    def menu_current_distance(_item: pystray.MenuItem) -> str:
        if state.current_distance is None:
            return "Current Distance: --"
        return f"Current Distance: {state.current_distance:.2f} m"

    def menu_last_seen(_item: pystray.MenuItem) -> str:
        if state.last_seen is None:
            return "Last Seen: --"
        age_s = time.time() - state.last_seen
        return f"Last Seen: {age_s:.1f}s ago"

    def menu_device(_item: pystray.MenuItem) -> str:
        return f"Device: {state.device_label}"

    def on_set_distance(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        try:
            import tkinter
            from tkinter import simpledialog

            root = tkinter.Tk()
            root.withdraw()
            value = simpledialog.askfloat("Set Lock Distance", "Distance in meters:", initialvalue=state.lock_distance)
            root.destroy()
            if value and value > 0:
                update_distance(value)
        except Exception:
            return

    def on_calibrate(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        data = load_config()
        device = data.get("device", {})
        target_addr = device.get("address")
        target_name = device.get("name")
        if not target_addr and not target_name:
            return

        bluetooth = data.get("bluetooth", {})
        adapter = bluetooth.get("adapter")
        tx_power = int(bluetooth.get("tx_power", -59))
        path_loss_exponent = float(bluetooth.get("path_loss_exponent", 2.2))

        async def calibrate() -> Optional[float]:
            distances: list[float] = []
            for _ in range(5):
                devices = await BleakScanner.discover(timeout=2.0, adapter=adapter, return_adv=True)
                for dev, adv in devices.values():
                    if matches_device(dev.address, dev.name, target_addr, target_name):
                        if adv.rssi is not None:
                            distances.append(estimate_distance_m(adv.rssi, tx_power, path_loss_exponent))
                        break
                await asyncio.sleep(0.2)
            if not distances:
                return None
            return float(sorted(distances)[len(distances) // 2])

        try:
            distance = asyncio.run(calibrate())
        except Exception:
            distance = None

        if distance is None:
            return
        update_distance(round(distance, 2))

    def on_toggle_debug(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        update_debug(not state.debug_enabled)
        refresh_state()

    def on_bind(icon: pystray.Icon, device: KnownDevice) -> None:
        ensure_config()
        try:
            bind_device(CONFIG_PATH, device)
        except (OSError, ValueError) as exc:
            print(f"Could not bind {device.label}: {exc}", file=sys.stderr)
            return
        if proc_holder.get("proc") is not None:
            restart_daemon(proc_holder)
        refresh_state()
        icon.update_menu()

    def on_refresh_devices(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        state.devices_listed_at = 0.0
        refresh_state()
        icon.update_menu()

    def device_item(dev: KnownDevice) -> pystray.MenuItem:
        # pystray rejects actions with more than (icon, item) parameters, even
        # defaulted ones, so bind the device through a closure.
        return pystray.MenuItem(
            dev.label,
            lambda icon, _item: on_bind(icon, dev),
            checked=lambda _item: is_bound(dev, state.bound_address, state.bound_name),
            radio=True,
        )

    def device_menu_items() -> list[pystray.MenuItem]:
        items = [device_item(dev) for dev in state.known_devices]
        if not items:
            items.append(pystray.MenuItem("No paired devices found", None, enabled=False))
        return [*items, pystray.Menu.SEPARATOR, pystray.MenuItem("Refresh List", on_refresh_devices)]

    def on_quit(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        stop_daemon(proc_holder)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(menu_status, None, enabled=False),
        pystray.MenuItem(menu_lock_distance, None, enabled=False),
        pystray.MenuItem(menu_current_distance, None, enabled=False),
        pystray.MenuItem(menu_last_seen, None, enabled=False),
        pystray.MenuItem(menu_device, None, enabled=False),
        pystray.MenuItem("Start", lambda *_: start_daemon(proc_holder)),
        pystray.MenuItem("Stop", lambda *_: stop_daemon(proc_holder)),
        pystray.MenuItem("Restart", lambda *_: restart_daemon(proc_holder)),
        pystray.MenuItem("Bind Device", pystray.Menu(device_menu_items)),
        pystray.MenuItem("Set Distance...", on_set_distance),
        pystray.MenuItem("Calibrate Distance...", on_calibrate),
        pystray.MenuItem(
            "Debug Mode",
            on_toggle_debug,
            checked=lambda _item: state.debug_enabled,
        ),
        pystray.MenuItem("Open Config", lambda *_: open_config()),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("bluelock", build_icon_image(), "BlueLock", menu)
    if embedded_daemon:
        embedded_thread = start_embedded_daemon()
    refresh_state()
    threading.Thread(target=background_refresh, args=(icon,), daemon=True).start()
    icon.run()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BlueLock tray (Windows)")
    parser.add_argument("--embedded-daemon", action="store_true", help="Run daemon in the tray process")
    parser.add_argument("--no-embedded", action="store_true", help="Disable embedded daemon")
    args = parser.parse_args()
    has_external = find_daemon_exe() is not None
    embedded = args.embedded_daemon or (not has_external and not args.no_embedded)
    return run_tray(embedded)


if __name__ == "__main__":
    raise SystemExit(main())
